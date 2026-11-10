"""Coordinator entry point.

Concurrency model: a single `uvicorn --workers 1` process with several Python
threads for the background work. Each thread is I/O-bound (AMQP sockets,
PostgreSQL queries, S3 calls), so the GIL is not a problem; a single process
also keeps the in-memory snapshot and metrics ring buffer simple.

pika is not thread-safe, so every thread that talks to RabbitMQ owns its own
connection: N results consumers, the dead-letter consumer, the outbox relay
and the snapshot loop. PostgreSQL is shared through one thread-safe
`psycopg_pool.ConnectionPool`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from shared.broker import consume
from shared.models import DEFAULT_HEARTBEAT_INTERVAL_S
from shared.routing import DLQ, RESULTS_QUEUE

from .api import cases as cases_router
from .api import dataset as dataset_router
from .api import health as health_router
from .api import state as state_router
from .api import uploads as uploads_router
from .api import ws as ws_router
from .aws import s3_client
from .barrier.close import Barrier
from .barrier.sweeper import run_sweep
from .config import get_settings
from .consumers.dlq import build_dlq_handler
from .consumers.results import build_results_handler
from .db import DB_ERRORS, apply_schema, create_pool
from .logging import setup_logging
from .monitor.broadcaster import Broadcaster
from .monitor.samples import run_samples_loop
from .monitor.snapshot import run_snapshot_loop
from .repo.cases import CasesRepo
from .repo.subtasks import SubTasksRepo
from .repo.workers import WorkersRepo
from .routing.outbox import OutboxRelay
from .security import OriginVerifyMiddleware

logger = logging.getLogger(__name__)

_THREAD_JOIN_TIMEOUT_S = 10.0
_DEAD_MARKING_GRACE_S = 2 * DEFAULT_HEARTBEAT_INTERVAL_S


def _sweeper_loop(stop_event: threading.Event, interval: float, **kwargs: Any) -> None:
    """A failed recurring pass is logged and retried on the next cycle (the
    startup pass in `lifespan` runs outside this loop and fails loudly).

    For `_DEAD_MARKING_GRACE_S` after a failed pass, workers are not marked
    dead: if the database was down, the workers could not heartbeat either,
    so their `last_seen` is stale for a reason that is not a crash; the
    grace gives them a couple of heartbeats to catch up."""
    last_failure: float | None = None
    while not stop_event.wait(interval):
        in_grace = (
            last_failure is not None
            and time.monotonic() - last_failure < _DEAD_MARKING_GRACE_S
        )
        try:
            run_sweep(**kwargs, mark_dead=not in_grace)
        except Exception:
            last_failure = time.monotonic()
            logger.exception("run_sweep failed, will retry on the next cycle")


def _thread(
    thread_name: str, target: Callable[..., None], /, *args: Any, **kwargs: Any
) -> threading.Thread:
    # Positional-only: `target` may itself take a `name` keyword (`consume`).
    return threading.Thread(
        target=target, args=args, kwargs=kwargs, name=thread_name, daemon=True
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()

    # The loop is bound BEFORE starting the background threads: the snapshot
    # thread needs it to call `publish` (see `Broadcaster.bind_loop`).
    broadcaster = Broadcaster()
    broadcaster.bind_loop(asyncio.get_running_loop())
    app.state.broadcaster = broadcaster

    db = create_pool(settings.DATABASE_URL, max_size=settings.DB_POOL_MAX)
    apply_schema(db)
    cases_repo = CasesRepo(db)
    subtasks_repo = SubTasksRepo(db)
    workers_repo = WorkersRepo(db)
    barrier = Barrier(
        cases=cases_repo,
        subtasks=subtasks_repo,
        s3=s3_client(),
        bucket=settings.S3_BUCKET,
        settings=settings,
    )
    outbox = OutboxRelay(
        subtasks_repo=subtasks_repo,
        amqp_url=settings.AMQP_URL,
        poll_s=settings.OUTBOX_POLL_S,
    )
    app.state.db = db
    app.state.barrier = barrier
    app.state.outbox = outbox

    # Synchronous sweep BEFORE accepting traffic: repairs whatever a previous
    # crash left half-done. The outbox relay's first pass then republishes
    # anything committed but never confirmed by the broker.
    sweep_kwargs = {
        "barrier": barrier,
        "workers_repo": workers_repo,
        "heartbeat_timeout_s": settings.HEARTBEAT_TIMEOUT_S,
    }
    run_sweep(**sweep_kwargs)

    stop_event = threading.Event()
    threads: dict[str, threading.Thread] = {}

    def add(thread: threading.Thread) -> None:
        threads[thread.name] = thread

    results_handler = build_results_handler(barrier)
    for i in range(settings.RESULTS_CONSUMER_THREADS):
        name = f"results-consumer-{i}"
        add(
            _thread(
                name,
                consume,
                settings.AMQP_URL,
                RESULTS_QUEUE,
                results_handler,
                stop_event,
                name=f"coordinator-{name}",
                prefetch=settings.RESULTS_PREFETCH,
                transient=DB_ERRORS,
            )
        )
    add(
        _thread(
            "dlq-consumer",
            consume,
            settings.AMQP_URL,
            DLQ,
            build_dlq_handler(barrier),
            stop_event,
            name="coordinator-dlq-consumer",
            prefetch=10,
            transient=DB_ERRORS,
        )
    )
    add(_thread("outbox-relay", outbox.run, stop_event))
    add(
        _thread(
            "sweeper-loop",
            _sweeper_loop,
            stop_event,
            settings.SWEEP_INTERVAL_S,
            **sweep_kwargs,
        )
    )
    add(
        _thread(
            "snapshot-loop",
            run_snapshot_loop,
            stop_event,
            settings.SNAPSHOT_INTERVAL_S,
            amqp_url=settings.AMQP_URL,
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            workers_repo=workers_repo,
            recent_cases_limit=settings.RECENT_CASES_IN_STATE,
            heartbeat_timeout_s=settings.HEARTBEAT_TIMEOUT_S,
            pool_saturation_queue_threshold=settings.POOL_SATURATION_QUEUE_THRESHOLD,
            pool_saturation_cpu_percent=settings.POOL_SATURATION_CPU_PERCENT,
            broadcaster=broadcaster,
        )
    )
    add(
        _thread(
            "samples-loop",
            run_samples_loop,
            stop_event,
            settings.SAMPLE_INTERVAL_S,
            settings.METRICS_FLUSH_S,
            s3=s3_client(),
            results_bucket=settings.S3_BUCKET,
        )
    )

    for thread in threads.values():
        thread.start()
    app.state.threads = threads
    logger.info("coordinator started", extra={"threads": list(threads)})

    try:
        yield
    finally:
        stop_event.set()
        outbox.wake()
        for thread in threads.values():
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning(
                    "thread %s did not finish within the shutdown timeout, it may keep running",
                    thread.name,
                )
        await broadcaster.close_all(code=1001)
        db.close()
        logger.info("coordinator shut down")


app = FastAPI(
    title="Coordinator — Distributed Messaging Analysis",
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
