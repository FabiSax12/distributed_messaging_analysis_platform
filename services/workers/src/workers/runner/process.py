"""Process a work message end to end and report it to the coordinator.

Protocol (the same as `services/coordinator/scripts/fake_worker.py`):

1. Unreadable `SubTaskMessage` -> deleted (poison message, retrying will not
   fix it).
2. Cancelled case (`Cases.cancel_requested`, consistent read) -> report
   `cancelled` without processing and delete.
3. `ApproximateReceiveCount > 1` -> `retrying` (another worker died or the
   visibility timeout expired).
4. `assigned` -> dataset download -> `running` with progress -> operation
   -> upload to `results/{case}/{subtask}/` -> `completed`.
5. Expected error (`OpError`) -> `failed` with its `ErrorCode`.
6. The message is deleted AFTER publishing the terminal status. If the worker
   dies between the two, SQS redelivers it and the coordinator's barrier ignores
   the duplicate terminal (`closed` in Cases).
7. Any other exception (S3 down, bug) -> neither terminal nor delete: SQS
   retries up to `maxReceiveCount` and the DLQ hands it to the coordinator.

The worker never writes Cases or SubTasks: everything goes out through the
`resultados` queue.
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, SubTaskMessage, utcnow
from shared.routing import RESULTS_QUEUE, Pool
from shared.states import SubTaskStatus

from ..ops import OPERATIONS
from ..ops.base import OpError
from .heartbeat import Heartbeat
from .poller import receive_next

logger = logging.getLogger(__name__)

# How many progress points between `running` publications: enough for the
# dashboard without flooding the results queue with one message per second.
_PROGRESS_STEP = 10

_IDLE_ON_ERROR_S = 5


@dataclass(frozen=True)
class WorkerContext:
    sqs: Any
    s3: Any
    dynamodb: Any
    queue_urls: dict[str, str]
    cases_table: str
    dataset_bucket: str
    results_bucket: str
    worker_id: str
    pool: Pool
    heartbeat: Heartbeat
    timeouts: dict[str, float]  # source queue -> operation limit

    def publish(self, result: ResultMessage) -> None:
        self.sqs.send_message(
            QueueUrl=self.queue_urls[RESULTS_QUEUE], MessageBody=result.to_body()
        )

    def result(
        self, task: SubTaskMessage, attempt: int, **fields: Any
    ) -> ResultMessage:
        return ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            attempt=attempt,
            worker_id=self.worker_id,
            pool=self.pool,
            **fields,
        )


def handle_message(ctx: WorkerContext, queue_name: str, raw: dict[str, Any]) -> None:
    queue_url = ctx.queue_urls[queue_name]
    receipt = raw["ReceiptHandle"]
    attempt = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    def delete() -> None:
        ctx.sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)

    try:
        task = SubTaskMessage.from_body(raw["Body"])
    except Exception:
        logger.exception(
            "unreadable SubTaskMessage, discarding", extra={"queue": queue_name}
        )
        delete()
        return

    log = {"case_id": task.case_id, "subtask_id": task.subtask_id, "queue": queue_name}

    if _is_cancelled(ctx, task.case_id):
        ctx.publish(
            ctx.result(
                task,
                attempt,
                status=SubTaskStatus.CANCELLED,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.CANCELLED, message="case cancelled before processing"
                ),
            )
        )
        delete()
        logger.info("sub-task cancelled without processing", extra=log)
        return

    if attempt > 1:
        ctx.publish(ctx.result(task, attempt, status=SubTaskStatus.RETRYING))
    started_at = utcnow()
    ctx.publish(
        ctx.result(task, attempt, status=SubTaskStatus.ASSIGNED, started_at=started_at)
    )

    try:
        with ctx.heartbeat.track(task.subtask_id):
            output_keys, output_meta = _run(ctx, task, queue_name, attempt, started_at)
        terminal = ctx.result(
            task,
            attempt,
            status=SubTaskStatus.COMPLETED,
            started_at=started_at,
            finished_at=utcnow(),
            output_keys=output_keys,
            output_meta=_without_floats(output_meta),
        )
    except OpError as exc:
        terminal = ctx.result(
            task,
            attempt,
            status=SubTaskStatus.FAILED,
            started_at=started_at,
            finished_at=utcnow(),
            error=ErrorInfo(code=exc.code, message=exc.message),
        )
    except Exception:
        logger.exception(
            "unexpected failure: message not deleted, SQS will retry",
            extra={**log, "attempt": attempt},
        )
        return

    ctx.publish(terminal)
    delete()
    logger.info(
        "sub-task finished",
        extra={**log, "status": terminal.status.value, "attempt": attempt},
    )


def _run(
    ctx: WorkerContext,
    task: SubTaskMessage,
    queue_name: str,
    attempt: int,
    started_at: datetime,
) -> tuple[list[str], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="worker-") as tmp:
        src = Path(tmp) / "in" / Path(task.input_key).name
        out_dir = Path(tmp) / "out"
        src.parent.mkdir()
        out_dir.mkdir()
        _download(ctx, task.input_key, src)

        last = -_PROGRESS_STEP

        def on_progress(pct: int) -> None:
            nonlocal last
            if pct >= last + _PROGRESS_STEP:
                last = pct
                # `started_at` here too: if the coordinator processes this
                # `running` before the `assigned`, it discards the `assigned` by
                # `state_order` and this start time is all it has.
                ctx.publish(
                    ctx.result(
                        task,
                        attempt,
                        status=SubTaskStatus.RUNNING,
                        progress=pct,
                        started_at=started_at,
                    )
                )

        on_progress(0)
        output = OPERATIONS[task.operation](
            task,
            src,
            out_dir,
            timeout_s=ctx.timeouts[queue_name],
            on_progress=on_progress,
        )

        keys = []
        for path in output.files:
            key = f"{task.output_prefix}{path.name}"
            content_type = (
                mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
            ctx.s3.upload_file(
                str(path),
                ctx.results_bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            keys.append(key)
        return keys, output.meta


def _without_floats(value: Any) -> Any:
    """float -> str, recursive. The coordinator stores `output_meta` as-is in
    DynamoDB, which rejects float ("Use Decimal types instead") and leaves the
    sub-task closure retrying forever. Operations already report durations in
    integer ms; this covers any float that slips through."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {k: _without_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_floats(v) for v in value]
    return value


def _download(ctx: WorkerContext, key: str, dest: Path) -> None:
    try:
        ctx.s3.download_file(ctx.dataset_bucket, key, str(dest))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            raise OpError(
                ErrorCode.CORRUPT_INPUT, f"{key} does not exist in the dataset"
            ) from exc
        raise


def _is_cancelled(ctx: WorkerContext, case_id: str) -> bool:
    item = ctx.dynamodb.get_item(
        TableName=ctx.cases_table,
        Key={"case_id": {"S": case_id}},
        ConsistentRead=True,
        ProjectionExpression="cancel_requested",
    ).get("Item", {})
    return item.get("cancel_requested", {}).get("BOOL", False)


def consume(
    ctx: WorkerContext, poll_order: tuple[str, ...], stop_event: threading.Event
) -> None:
    """Loop of one consumer thread. Finishes the in-flight sub-task before
    exiting when `stop_event` is set (systemd stop)."""
    while not stop_event.is_set():
        try:
            received = receive_next(ctx.sqs, ctx.queue_urls, poll_order)
            if received is not None:
                handle_message(ctx, *received)
        except Exception:
            logger.exception("consumer loop iteration failed, retrying")
            stop_event.wait(_IDLE_ON_ERROR_S)
