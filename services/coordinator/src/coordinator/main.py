"""Coordinator entry point.

Concurrency model: a single `uvicorn --workers 1` process (the
container/process command is part of the planned Docker Compose setup) with several Python threads for all the
background work. Each thread is I/O-bound: SQS long-polling (`consumers/`), periodic
DynamoDB Scans/Queries (`barrier/sweeper.py`, `monitor/snapshot.py`) or
S3 Get/PutObject (`monitor/samples.py`). The GIL is not a problem here because
none of these threads does heavy computation in pure Python; they spend almost all their
time blocked in network syscalls inside boto3/urllib3, which release it.
A single process also keeps the shared in-memory state simple (the
snapshot of `monitor/snapshot.py` and the ring buffer of `monitor/samples.py`):
they live as module-level variables protected by a lock, with no need for IPC or
an external backend just for this.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from shared.routing import DLQ, RESULTS_QUEUE

from .api import cases as cases_router
from .api import dataset as dataset_router
from .api import health as health_router
from .api import state as state_router
from .api import uploads as uploads_router
from .api import ws as ws_router
from .aws import dynamodb_client, s3_client, sqs_client
from .barrier.sweeper import run_sweep
from .config import get_settings
from .consumers.base import SqsConsumerThread
from .consumers.dlq import build_dlq_handler
from .consumers.results import build_results_handler
from .logging import setup_logging
from .monitor.broadcaster import Broadcaster
from .monitor.samples import run_samples_loop
from .monitor.snapshot import run_snapshot_loop
from .repo.cases import CasesRepo
from .repo.subtasks import SubTasksRepo
from .repo.workers import WorkersRepo
from .security import OriginVerifyMiddleware

logger = logging.getLogger(__name__)

_THREAD_JOIN_TIMEOUT_S = 10.0


def _sweeper_loop(stop_event: threading.Event, interval: float, **kwargs: Any) -> None:
    """`run_sweep` is a single pass (see its docstring); we build the loop here,
    just as we do with those of `monitor/snapshot.py` and `monitor/samples.py`.

    Like `run_snapshot_once`, a failed pass is logged and dropped
    instead of killing the thread forever: the initial sweep in `lifespan`
    (outside this loop) must fail loudly if something is misconfigured,
    but a transient failure in a recurring pass should not
    stop sweeping stalled cases forever."""
    while not stop_event.is_set():
        try:
            run_sweep(**kwargs)
        except Exception:
            logger.exception("run_sweep failed, will retry on the next cycle")
        stop_event.wait(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()

    # The loop is bound BEFORE starting the background threads: the
    # snapshot thread needs this reference ready in order to call `publish`
    # (see the docstring of `Broadcaster.bind_loop`).
    broadcaster = Broadcaster()
    broadcaster.bind_loop(asyncio.get_running_loop())
    app.state.broadcaster = broadcaster

    dynamodb = dynamodb_client()
    s3 = s3_client()
    sqs = sqs_client()

    cases_repo = CasesRepo(dynamodb, settings.TABLE_CASES)
    subtasks_repo = SubTasksRepo(dynamodb, settings.TABLE_SUBTASKS)
    workers_repo = WorkersRepo(dynamodb, settings.TABLE_WORKERS)

    # Synchronous sweep BEFORE accepting traffic: repairs any case that
    # was left half-closed by a previous crash/restart of the process.
    run_sweep(
        cases_repo=cases_repo,
        subtasks_repo=subtasks_repo,
        sqs=sqs,
        queue_urls=settings.QUEUE_URLS,
        s3=s3,
        results_bucket=settings.RESULTS_BUCKET,
    )

    stop_event = threading.Event()
    threads: dict[str, threading.Thread] = {}

    results_handler = build_results_handler(
        cases_repo=cases_repo,
        subtasks_repo=subtasks_repo,
        dynamodb=dynamodb,
        s3=s3,
        results_bucket=settings.RESULTS_BUCKET,
    )
    for i in range(settings.RESULTS_CONSUMER_THREADS):
        thread = SqsConsumerThread(
            sqs=sqs,
            queue_url=settings.QUEUE_URLS[RESULTS_QUEUE],
            handler=results_handler,
            stop_event=stop_event,
            name=f"results-consumer-{i}",
        )
        threads[thread.name] = thread

    dlq_handler = build_dlq_handler(
        cases_repo=cases_repo,
        subtasks_repo=subtasks_repo,
        dynamodb=dynamodb,
        s3=s3,
        results_bucket=settings.RESULTS_BUCKET,
        dlq_assumed_attempts=settings.DLQ_ASSUMED_ATTEMPTS,
    )
    dlq_thread = SqsConsumerThread(
        sqs=sqs,
        queue_url=settings.QUEUE_URLS[DLQ],
        handler=dlq_handler,
        stop_event=stop_event,
        name="dlq-consumer",
    )
    threads[dlq_thread.name] = dlq_thread

    sweeper_thread = threading.Thread(
        target=_sweeper_loop,
        args=(stop_event, settings.SWEEP_INTERVAL_S),
        kwargs={
            "cases_repo": cases_repo,
            "subtasks_repo": subtasks_repo,
            "sqs": sqs,
            "queue_urls": settings.QUEUE_URLS,
            "s3": s3,
            "results_bucket": settings.RESULTS_BUCKET,
        },
        name="sweeper-loop",
        daemon=True,
    )
    threads[sweeper_thread.name] = sweeper_thread

    snapshot_thread = threading.Thread(
        target=run_snapshot_loop,
        args=(stop_event, settings.SNAPSHOT_INTERVAL_S),
        kwargs={
            "cases_repo": cases_repo,
            "subtasks_repo": subtasks_repo,
            "workers_repo": workers_repo,
            "sqs": sqs,
            "queue_urls": settings.QUEUE_URLS,
            "recent_cases_limit": settings.RECENT_CASES_IN_STATE,
            "pool_saturation_queue_threshold": settings.POOL_SATURATION_QUEUE_THRESHOLD,
            "pool_saturation_cpu_percent": settings.POOL_SATURATION_CPU_PERCENT,
            "broadcaster": broadcaster,
        },
        name="snapshot-loop",
        daemon=True,
    )
    threads[snapshot_thread.name] = snapshot_thread

    samples_thread = threading.Thread(
        target=run_samples_loop,
        args=(stop_event, settings.SAMPLE_INTERVAL_S, settings.METRICS_FLUSH_S),
        kwargs={"s3": s3, "results_bucket": settings.RESULTS_BUCKET},
        name="samples-loop",
        daemon=True,
    )
    threads[samples_thread.name] = samples_thread

    for thread in threads.values():
        thread.start()
    app.state.threads = threads
    logger.info("coordinator started", extra={"threads": list(threads)})

    try:
        yield
    finally:
        stop_event.set()
        for thread in threads.values():
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning(
                    "thread %s did not finish within the shutdown timeout, it may keep running",
                    thread.name,
                )
        await broadcaster.close_all(code=1001)
        logger.info("coordinator shut down")


app = FastAPI(
    title="Coordinator — Distributed Multimedia Processing",
    description=(
        "Coordinator API: case ingestion, subtask tracking via "
        "barrier and live state. Swagger UI at `/docs`, OpenAPI JSON "
        "importable in Postman (Import → Link) at `/openapi.json`."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(OriginVerifyMiddleware, secret=get_settings().ORIGIN_VERIFY_SECRET)

app.include_router(health_router.router)
app.include_router(cases_router.router, prefix="/api")
app.include_router(uploads_router.router, prefix="/api")
app.include_router(dataset_router.router, prefix="/api")
app.include_router(state_router.router, prefix="/api")
app.include_router(ws_router.router, prefix="/api")
