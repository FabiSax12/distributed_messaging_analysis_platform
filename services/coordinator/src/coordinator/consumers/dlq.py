"""DLQ handler: subtasks that exhausted their SQS retries.

If a message from a work queue exhausts `maxReceiveCount` (worker died
mid-process, unhandled exception, etc.), SQS moves it here. The
coordinator consumes it and records it as `failed` via
`shared.messages.result_from_dlq`, exactly as if the worker had
reported the failure itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared.messages import SubTaskMessage, result_from_dlq

from ..barrier.close import close_subtask
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)


def build_dlq_handler(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
    dlq_assumed_attempts: int,
) -> Callable[[dict[str, Any]], bool]:
    def handle(message: dict[str, Any]) -> bool:
        try:
            subtask_message = SubTaskMessage.from_body(message["Body"])
        except Exception:
            logger.exception(
                "unreadable SubTaskMessage in the DLQ, discarding",
                extra={"message_id": message.get("MessageId")},
            )
            return True

        result = result_from_dlq(subtask_message, dlq_assumed_attempts)
        try:
            close_subtask(
                result,
                cases_repo=cases_repo,
                subtasks_repo=subtasks_repo,
                dynamodb=dynamodb,
                s3=s3,
                results_bucket=results_bucket,
            )
        except Exception:
            logger.exception(
                "close_subtask failed for a DLQ message, will retry",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
            return False
        return True

    return handle
