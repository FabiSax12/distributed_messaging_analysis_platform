"""Worker entry point: `uv run --package workers python -m workers.main`.

Concurrency model: one process per machine with `concurrency` consumer threads
plus one heartbeat thread. The threads do not compete for the GIL: the heavy
work runs in ffmpeg subprocesses and the rest is network waiting (SQS, S3,
external APIs). So the heartbeat's `concurrency` is literally how many
sub-tasks the machine can have in flight at once.

SIGTERM (systemd stop) sets `stop_event`: each thread finishes its current
sub-task and exits. If systemd kills it earlier, the message was not deleted
and SQS redelivers it to another worker.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import threading
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config

from shared.routing import Pool

from .config import get_settings
from .logging import setup_logging
from .runner.heartbeat import Heartbeat
from .runner.poller import op_timeouts
from .runner.process import WorkerContext, consume

logger = logging.getLogger(__name__)

_IMDS = "http://169.254.169.254/latest"
_IMDS_TIMEOUT_S = 1.0

# max_pool_connections: one heartbeat thread + N consumers share clients.
_BOTO_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 5}, max_pool_connections=20
)


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
    """(worker_id, instance_type). On EC2 without pid: a restart overwrites the same
    Workers item. On a laptop with host and pid, so several can run."""
    if ec2 is not None:
        instance_id, instance_type = ec2
        return f"{pool.value}-{instance_id}", instance_type
    return f"{pool.value}-{socket.gethostname()}-{os.getpid()}", "local"


def check_ffmpeg() -> None:
    # The ops/ffmpeg.py subprocess helpers need both binaries.
    # TODO: relax this once the text/image/audio ops show which pools need ffmpeg.
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"{', '.join(missing)} not found in PATH (see workers/README.md)")


def main() -> None:
    setup_logging()
    check_ffmpeg()
    settings = get_settings()
    worker_id, instance_type = worker_identity(settings.POOL, ec2_identity())

    session = boto3.session.Session(region_name=settings.AWS_REGION)
    sqs = session.client("sqs", config=_BOTO_CONFIG)
    s3 = session.client("s3", config=_BOTO_CONFIG)
    dynamodb = session.client("dynamodb", config=_BOTO_CONFIG)

    heartbeat = Heartbeat(
        dynamodb,
        settings.TABLE_WORKERS,
        worker_id=worker_id,
        pool=settings.POOL,
        instance_type=instance_type,
        hostname=socket.gethostname(),
        concurrency=settings.concurrency,
    )
    ctx = WorkerContext(
        sqs=sqs,
        s3=s3,
        dynamodb=dynamodb,
        queue_urls=settings.QUEUE_URLS,
        cases_table=settings.TABLE_CASES,
        dataset_bucket=settings.DATASET_BUCKET,
        results_bucket=settings.RESULTS_BUCKET,
        worker_id=worker_id,
        pool=settings.POOL,
        heartbeat=heartbeat,
        timeouts=op_timeouts(sqs, settings.QUEUE_URLS, settings.poll_order),
    )

    stop_event = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop_event.set())

    threading.Thread(
        target=heartbeat.run, args=(stop_event,), name="heartbeat", daemon=True
    ).start()
    consumers = [
        threading.Thread(
            target=consume,
            args=(ctx, settings.poll_order, stop_event),
            name=f"consumer-{i}",
        )
        for i in range(settings.concurrency)
    ]
    for thread in consumers:
        thread.start()
    logger.info(
        "worker started",
        extra={
            "worker_id": worker_id,
            "pool": settings.POOL.value,
            "concurrency": settings.concurrency,
            "poll_order": settings.poll_order,
            "timeouts": ctx.timeouts,
        },
    )

    # join with timeout: the main thread has to wake up to handle
    # signals (Python only delivers them in the main thread).
    while any(thread.is_alive() for thread in consumers):
        for thread in consumers:
            thread.join(timeout=1)
    logger.info("worker stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
