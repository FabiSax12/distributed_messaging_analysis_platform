"""Worker entry point: `POOL=text uv run --package workers python -m workers.main`.

Concurrency model: one process per machine with `WORKER_CONCURRENCY`
consumer threads plus one heartbeat thread. Each consumer thread owns its
AMQP connection (pika is not thread-safe) and consumes its pool queue with
prefetch=1, so a worker never holds more messages than it is running.

SIGTERM/SIGINT set `stop_event`: each consumer finishes its current
sub-task and exits. If the process is killed instead, the broker requeues
the unacked message and the next worker reports it as `worker_lost`
(`runner/process.py`); the coordinator retries it.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import signal
import socket
import threading
from collections.abc import Mapping
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config

from shared.analysis import AnalysisOperation
from shared.broker import consume
from shared.routing import Pool, work_queue

from .config import Settings, get_settings
from .logging import setup_logging
from .ops.analysis import ANALYSIS_OPERATIONS
from .ops.base import Operation
from .runner.db import create_pool
from .runner.heartbeat import Heartbeat
from .runner.process import WorkerContext, handle

logger = logging.getLogger(__name__)

_IMDS = "http://169.254.169.254/latest"
_IMDS_TIMEOUT_S = 1.0


def ec2_identity() -> tuple[str, str] | None:
    """(instance-id, instance-type) via IMDSv2, or `None` outside EC2."""
    try:
        token_req = Request(
            f"{_IMDS}/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urlopen(token_req, timeout=_IMDS_TIMEOUT_S).read().decode()

        def get(path: str) -> str:
            req = Request(
                f"{_IMDS}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token}
            )
            return urlopen(req, timeout=_IMDS_TIMEOUT_S).read().decode()

        return get("instance-id"), get("instance-type")
    except OSError:
        return None


def worker_identity(pool: Pool, ec2: tuple[str, str] | None) -> tuple[str, str]:
    """(worker_id, instance_type). On EC2 without pid: a restart reuses the
    same row. Elsewhere with host and pid, so several can run side by side."""
    if ec2 is not None:
        instance_id, instance_type = ec2
        return f"{pool.value}-{instance_id}", instance_type
    return f"{pool.value}-{socket.gethostname()}-{os.getpid()}", "local"


def check_ffmpeg(pool: Pool) -> None:
    """The audio pool extracts audio tracks from videos with ffmpeg
    (`ops/ffmpeg.py`); the other pools do not need it."""
    if pool is not Pool.AUDIO:
        return
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"{', '.join(missing)} not found in PATH (see workers/README.md)")


def s3_client(settings: Settings):
    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        max_pool_connections=max(10, settings.WORKER_CONCURRENCY * 2),
        s3={"addressing_style": "path"} if settings.S3_ENDPOINT_URL else {},
    )
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        config=config,
    )


def run_worker(
    settings: Settings,
    operations: Mapping[AnalysisOperation, Operation],
    stop_event: threading.Event | None = None,
) -> None:
    """Run consumers and the heartbeat until `stop_event` (or a signal).

    `operations` is `ANALYSIS_OPERATIONS` in production; the local tooling
    (`scripts/fake_worker.py`) passes a fake registry instead.
    """
    stop_event = stop_event or threading.Event()
    worker_id, instance_type = worker_identity(settings.POOL, ec2_identity())
    db = create_pool(settings.DATABASE_URL, max_size=settings.WORKER_CONCURRENCY + 1)
    s3 = s3_client(settings)
    heartbeat = Heartbeat(
        db,
        worker_id=worker_id,
        pool=settings.POOL,
        instance_type=instance_type,
        hostname=socket.gethostname(),
        concurrency=settings.WORKER_CONCURRENCY,
    )

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop_event.set())

    threading.Thread(
        target=heartbeat.run,
        args=(stop_event, settings.HEARTBEAT_INTERVAL_S),
        name="heartbeat",
        daemon=True,
    ).start()

    queue = work_queue(settings.POOL)
    consumers = []
    for i in range(settings.WORKER_CONCURRENCY):
        ctx = WorkerContext(
            s3=s3,
            bucket=settings.S3_BUCKET,
            db=db,
            worker_id=worker_id,
            pool=settings.POOL,
            heartbeat=heartbeat,
            operations=operations,
            op_timeout_s=settings.op_timeout_s,
        )
        consumers.append(
            threading.Thread(
                target=consume,
                args=(settings.AMQP_URL, queue, functools.partial(handle, ctx), stop_event),
                kwargs={"name": f"{worker_id}-{i}", "prefetch": 1, "confirm": True},
                name=f"consumer-{i}",
            )
        )
    for thread in consumers:
        thread.start()
    logger.info(
        "worker started",
        extra={
            "worker_id": worker_id,
            "pool": settings.POOL.value,
            "queue": queue,
            "concurrency": settings.WORKER_CONCURRENCY,
            "operations": sorted(op.value for op in operations),
        },
    )

    # join with timeout: the main thread has to wake up to handle signals.
    while any(thread.is_alive() for thread in consumers):
        for thread in consumers:
            thread.join(timeout=1)
    db.close()
    logger.info("worker stopped", extra={"worker_id": worker_id})


def main() -> None:
    setup_logging()
    settings = get_settings()
    check_ffmpeg(settings.POOL)
    run_worker(settings, ANALYSIS_OPERATIONS)


if __name__ == "__main__":
    main()
