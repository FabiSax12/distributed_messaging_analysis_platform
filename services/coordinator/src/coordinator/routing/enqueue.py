"""Publish planned subtasks to their work queues (SQS)."""

from __future__ import annotations

import logging
from typing import Any

from shared.models import SubTaskItem

from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)

_SEND_BATCH_SIZE = 10


def enqueue_case(
    subtasks: list[SubTaskItem],
    *,
    sqs: Any,
    queue_urls: dict[str, str],
    subtasks_repo: SubTasksRepo,
) -> None:
    """Group by queue, send `SendMessageBatch` 10 at a time and mark `enqueued=true`.

    Assumes the case's `PutItem` and its subtasks' `BatchWriteItem` were ALREADY
    done (outbox pattern: first persist the intent in DynamoDB,
    then publish to SQS). A subtask that ends up in `Failed` within
    a batch is NOT retried here: it is left with `enqueued=false` and it is
    `barrier/sweeper.py` that re-enqueues it later. This way no case
    is left hanging forever by a transient SendMessageBatch error.
    Prefailed subtasks (no `queue`, unsupported format) are ignored:
    they are never enqueued.
    """
    by_queue: dict[str, list[SubTaskItem]] = {}
    for subtask in subtasks:
        if subtask.queue is None:
            continue
        by_queue.setdefault(subtask.queue, []).append(subtask)

    for queue_name, queued_subtasks in by_queue.items():
        queue_url = queue_urls[queue_name]
        for start in range(0, len(queued_subtasks), _SEND_BATCH_SIZE):
            chunk = queued_subtasks[start : start + _SEND_BATCH_SIZE]
            _send_and_mark(sqs, subtasks_repo, queue_url, chunk)


def _send_and_mark(
    sqs: Any,
    subtasks_repo: SubTasksRepo,
    queue_url: str,
    chunk: list[SubTaskItem],
) -> None:
    by_id = {subtask.subtask_id: subtask for subtask in chunk}
    entries = [
        {
            "Id": subtask.subtask_id,
            "MessageBody": subtask.to_message().to_body(),
            "MessageAttributes": {
                "case_id": {"DataType": "String", "StringValue": subtask.case_id},
                "subtask_id": {"DataType": "String", "StringValue": subtask.subtask_id},
            },
        }
        for subtask in chunk
    ]

    resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)

    for successful in resp.get("Successful", []):
        subtask = by_id[successful["Id"]]
        subtasks_repo.mark_enqueued(subtask.case_id, subtask.subtask_id)

    for failed in resp.get("Failed", []):
        subtask = by_id[failed["Id"]]
        logger.warning(
            "could not enqueue subtask, leaving it for the sweeper",
            extra={
                "case_id": subtask.case_id,
                "subtask_id": subtask.subtask_id,
                "sqs_error_code": failed.get("Code"),
            },
        )
