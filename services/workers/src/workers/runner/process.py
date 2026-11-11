"""Process one work message end to end and report it to the coordinator.

`handle` is the `on_message` callback of `shared.broker.consume` (manual
ack, prefetch=1). Every AMQP call happens on the consuming thread (pika is
not thread-safe); the transfer supervisor runs on a helper thread; the op runs in a killable child while the consuming
thread keeps servicing AMQP heartbeats and publishes the op's progress.

Protocol:

1. Unreadable `SubTaskMessage` -> rejected without requeue (dead-letter
   queue): retrying will not fix it.
2. Pre-start check against PostgreSQL (read-only):
   - unknown sub-task, already closed, or `attempt` older than the current
     one -> stale duplicate (a retry already superseded it): ack and drop;
   - same attempt already `assigned`/`running` and this copy is not a
     redelivery -> the outbox published the attempt twice and another
     worker has it: ack and drop;
   - case `cancel_requested` -> report `cancelled` without processing.
   If the database is unreachable, the message is republished as a fresh
   copy (keeping a crash marker if it had one) and acked, and the consumer
   pauses with backoff: a database blip must not look like a crash.
3. `redelivered` (or the crash marker header) -> the worker that held it
   died (or lost its connection) mid-task: report `failed` / `worker_lost`
   without running it. The coordinator schedules the retry, so a file that
   crashes workers counts against `RETRY_MAX_ATTEMPTS` instead of looping
   forever.
4. `assigned` -> S3 download -> `running` with progress -> op -> upload to
   `results/{case}/{subtask}/` -> `completed`.
5. `OpError` -> `failed` with its code; any other exception -> `failed` /
   `internal`. The coordinator decides which codes are retried.
6. The message is acked AFTER the terminal result is confirmed by the
   broker. If the worker dies in between, the redelivered copy hits step 2
   or 3 and the barrier ignores the duplicate.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import queue
import tempfile
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties
from psycopg_pool import ConnectionPool
from storage.chunking import ChunkIntegrityError
from storage.chunking.file_chunker import DiskReassembler, FileChunker

from shared.analysis import AnalysisOperation, AnalysisResult
from shared.broker import backoff_delay, publish
from shared.messages import ErrorCode, ErrorInfo, ResultMessage, SubTaskMessage, utcnow
from shared.routing import RESULTS_QUEUE, Pool
from shared.states import SubTaskStatus

from ..ops.base import Operation, OpError
from .db import DB_ERRORS, subtask_state
from .heartbeat import Heartbeat
from .isolation import run_isolated

logger = logging.getLogger(__name__)

# Progress points between `running` publications: enough for the dashboard
# without flooding the results queue.
_PROGRESS_STEP = 10
# How long the consuming thread blocks in AMQP I/O between checks of the op.
_POLL_S = 0.5
# Header set on a republished copy of a redelivered message, so the crash
# is not forgotten when the copy goes back to the queue (`redelivered` is
# False on a new message).
CRASH_HEADER = "x-redelivered"
_IN_FLIGHT = frozenset({SubTaskStatus.ASSIGNED, SubTaskStatus.RUNNING})


@dataclass
class WorkerContext:
    s3: Any
    bucket: str
    db: ConnectionPool
    worker_id: str
    pool: Pool
    heartbeat: Heartbeat
    operations: Mapping[AnalysisOperation, Operation]
    op_timeout_s: float
    # One per consumer thread: the op runs here while the consumer thread
    # keeps the AMQP connection alive.
    executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1)
    )
    db_failures: int = 0

    def result(self, task: SubTaskMessage, **fields: Any) -> ResultMessage:
        return ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            attempt=task.attempt,
            worker_id=self.worker_id,
            pool=self.pool,
            **fields,
        )


def handle(
    ctx: WorkerContext,
    channel: BlockingChannel,
    method: Basic.Deliver,
    properties: BasicProperties,
    body: bytes,
) -> None:
    try:
        task = SubTaskMessage.from_body(body)
    except Exception:
        logger.exception("unreadable SubTaskMessage, dead-lettering it")
        channel.basic_reject(method.delivery_tag, requeue=False)
        return

    log = {
        "case_id": task.case_id,
        "subtask_id": task.subtask_id,
        "attempt": task.attempt,
    }

    def report(result: ResultMessage) -> None:
        publish(channel, RESULTS_QUEUE, result.to_body())

    crashed = bool(method.redelivered or (properties.headers or {}).get(CRASH_HEADER))
    try:
        state = subtask_state(ctx.db, task.subtask_id)
        ctx.db_failures = 0
    except DB_ERRORS as exc:
        _requeue_fresh(ctx, channel, method, properties, body, crashed, exc, log)
        return

    if state is None or state.closed or task.attempt < state.attempt:
        logger.info("stale work message, dropping it", extra=log)
        channel.basic_ack(method.delivery_tag)
        return

    if not crashed and task.attempt == state.attempt and state.status in _IN_FLIGHT:
        logger.info("attempt already taken by another worker, dropping it", extra=log)
        channel.basic_ack(method.delivery_tag)
        return

    if state.cancel_requested:
        report(
            ctx.result(
                task,
                status=SubTaskStatus.CANCELLED,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.CANCELLED, message="case cancelled before processing"
                ),
            )
        )
        channel.basic_ack(method.delivery_tag)
        logger.info("sub-task cancelled without processing", extra=log)
        return

    if crashed:
        report(
            ctx.result(
                task,
                status=SubTaskStatus.FAILED,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.WORKER_LOST,
                    message="redelivered: the previous worker died before finishing it",
                ),
            )
        )
        channel.basic_ack(method.delivery_tag)
        logger.warning("redelivered work message reported as worker_lost", extra=log)
        return

    started_at = utcnow()
    report(ctx.result(task, status=SubTaskStatus.ASSIGNED, started_at=started_at))

    progress: queue.SimpleQueue[int] = queue.SimpleQueue()
    with ctx.heartbeat.track(task.subtask_id):
        future = ctx.executor.submit(_run, ctx, task, progress.put)
        try:
            _wait(channel, future, progress, task, ctx, started_at, report)
        except BaseException:
            # The connection broke (or we are stopping). Let the op finish
            # so its temp dir is removed and the executor is free before the
            # consumer reconnects; its result is discarded and the broker
            # redelivers the message, which is then reported as worker_lost.
            logger.warning(
                "connection lost mid-task, discarding the op result", extra=log
            )
            wait([future])
            raise
    try:
        output_keys, output_meta = future.result()
        terminal = ctx.result(
            task,
            status=SubTaskStatus.COMPLETED,
            started_at=started_at,
            finished_at=utcnow(),
            output_keys=output_keys,
            output_meta=output_meta,
        )
    except OpError as exc:
        terminal = _failed(ctx, task, started_at, exc.code, exc.message)
    except Exception as exc:
        logger.exception("unexpected op failure, reporting internal error", extra=log)
        terminal = _failed(ctx, task, started_at, ErrorCode.INTERNAL, repr(exc))

    report(terminal)
    channel.basic_ack(method.delivery_tag)
    logger.info("sub-task finished", extra={**log, "status": terminal.status.value})


def _requeue_fresh(
    ctx: WorkerContext,
    channel: BlockingChannel,
    method: Basic.Deliver,
    properties: BasicProperties,
    body: bytes,
    crashed: bool,
    error: BaseException,
    log: dict[str, Any],
) -> None:
    """Database down during the pre-start check: put a fresh copy back in
    the queue (same priority and ids, crash marker kept) and ack this one,
    then pause before taking the next message."""
    headers = dict(properties.headers or {})
    if crashed:
        headers[CRASH_HEADER] = True
    publish(
        channel,
        method.routing_key,
        body,
        priority=properties.priority,
        message_id=properties.message_id,
        headers=headers,
    )
    channel.basic_ack(method.delivery_tag)
    delay = backoff_delay(ctx.db_failures)
    ctx.db_failures += 1
    logger.warning(
        "database unreachable before starting, requeued the message",
        extra={**log, "retry_in_s": round(delay, 2), "error": repr(error)},
    )
    channel.connection.sleep(delay)


def _wait(
    channel: BlockingChannel,
    future: Future[Any],
    progress: queue.SimpleQueue[int],
    task: SubTaskMessage,
    ctx: WorkerContext,
    started_at: datetime,
    report: Any,
) -> None:
    """Service AMQP I/O (heartbeats) and publish progress until the op ends."""
    last = -_PROGRESS_STEP
    while True:
        done = future.done()
        while not progress.empty():
            pct = progress.get()
            if pct >= last + _PROGRESS_STEP:
                last = pct
                # `started_at` here too: if the coordinator applies this
                # `running` before the `assigned`, it is all it has.
                report(
                    ctx.result(
                        task,
                        status=SubTaskStatus.RUNNING,
                        progress=pct,
                        started_at=started_at,
                    )
                )
        if done:
            return
        channel.connection.process_data_events(time_limit=_POLL_S)


def _failed(
    ctx: WorkerContext,
    task: SubTaskMessage,
    started_at: datetime,
    code: ErrorCode,
    message: str,
) -> ResultMessage:
    return ctx.result(
        task,
        status=SubTaskStatus.CANCELLED
        if code == ErrorCode.CANCELLED
        else SubTaskStatus.FAILED,
        started_at=started_at,
        finished_at=utcnow(),
        error=ErrorInfo(code=code, message=message[-2000:]),
    )


def _run(
    ctx: WorkerContext, task: SubTaskMessage, on_progress: Any
) -> tuple[list[str], dict[str, Any]]:
    """Runs on the helper thread: S3 and the op only, never AMQP."""
    op = ctx.operations.get(task.operation)
    if op is None:
        raise OpError(
            ErrorCode.PROCESSING_ERROR,
            f"operation {task.operation} is not implemented on this worker",
        )
    with tempfile.TemporaryDirectory(prefix="worker-") as tmp:
        src = Path(tmp) / "in" / Path(task.input_key).name
        out_dir = Path(tmp) / "out"
        src.parent.mkdir()
        out_dir.mkdir()
        _download(ctx, task.input_key, src, task.params.get("sha256"))

        on_progress(0)

        def cancelled():
            try:
                current = subtask_state(ctx.db, task.subtask_id)
                return current is None or current.closed or current.cancel_requested
            except DB_ERRORS:
                return False  # A transient DB outage must not discard valid work.

        output = run_isolated(
            op,
            task,
            src,
            out_dir,
            timeout_s=ctx.op_timeout_s,
            on_progress=on_progress,
            cancelled=cancelled,
        )
        if cancelled():
            raise OpError(ErrorCode.CANCELLED, "Case cancelled before upload")

        analysis = AnalysisResult.from_meta(output.meta)
        if analysis.operation != task.operation:
            raise OpError(
                ErrorCode.PROCESSING_ERROR, "Analysis operation does not match task"
            )

        keys = []
        for path in output.files:
            if (
                not path.resolve().is_relative_to(out_dir.resolve())
                or not path.is_file()
            ):
                raise OpError(
                    ErrorCode.PROCESSING_ERROR, "Operation output escaped its directory"
                )
            key = f"{task.output_prefix}{path.name}"
            content_type = (
                mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
            ctx.s3.upload_file(
                str(path), ctx.bucket, key, ExtraArgs={"ContentType": content_type}
            )
            keys.append(key)
        return keys, output.meta


def _download(
    ctx: WorkerContext, key: str, dest: Path, expected_hash: str | None = None
) -> None:
    try:
        response = ctx.s3.get_object(Bucket=ctx.bucket, Key=key)
        size = response["ContentLength"]
        block_size = 4 * 1024 * 1024
        expected = expected_hash or response.get("Metadata", {}).get("sha256")
        with response["Body"] as body, tempfile.TemporaryFile() as spool:
            digest = hashlib.sha256()
            for chunk in FileChunker().split(body, block_size):
                digest.update(chunk.data)
                spool.write(chunk.data)
            actual = digest.hexdigest()
            if expected and expected != actual:
                raise OpError(ErrorCode.CORRUPT_INPUT, "Source SHA-256 mismatch")
            spool.seek(0)
            with DiskReassembler(
                actual, (size + block_size - 1) // block_size
            ) as receiver:
                for chunk in FileChunker().split(spool, block_size):
                    receiver.add(chunk)
                with dest.open("wb") as out:
                    receiver.finish(out)
        # Conditional creation is atomic across workers; identical bytes have one key.
        with dest.open("rb") as stream:
            try:
                ctx.s3.put_object(
                    Bucket=ctx.bucket,
                    Key="content/" + actual,
                    Body=stream,
                    IfNoneMatch="*",
                    Metadata={"sha256": actual},
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {
                    "PreconditionFailed",
                    "412",
                }:
                    raise
    except ChunkIntegrityError as exc:
        raise OpError(ErrorCode.CORRUPT_INPUT, str(exc)) from exc
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            raise OpError(
                ErrorCode.CORRUPT_INPUT, f"{key} does not exist in the bucket"
            ) from exc
        raise
