"""Results queue handler: a single one, shared by N consumer threads.

`RESULTS_CONSUMER_THREADS` instances of `SqsConsumerThread` run this same
handler against the same queue; that is fine, SQS spreads messages across
concurrent receivers, there is no need to split the queue.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared.messages import ResultMessage
from shared.states import TERMINAL_SUBTASK, SubTaskStatus

from ..barrier.close import close_subtask
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)


def build_results_handler(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
) -> Callable[[dict[str, Any]], bool]:
    def handle(message: dict[str, Any]) -> bool:
        try:
            result = ResultMessage.from_body(message["Body"])
        except Exception:
            logger.exception(
                "unreadable ResultMessage, discarding (poison message)",
                extra={"message_id": message.get("MessageId")},
            )
            return True

        if result.status not in TERMINAL_SUBTASK:
            return _apply_progress(
                result, cases_repo=cases_repo, subtasks_repo=subtasks_repo
            )

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
            # close_subtask only re-raises for the "must retry" case
            # (see its docstring); anything else we treat the same way, as a
            # transient failure: do not delete, let SQS retry.
            logger.exception(
                "close_subtask failed, will retry",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
            return False
        return True

    return handle


def _apply_progress(
    result: ResultMessage, *, cases_repo: CasesRepo, subtasks_repo: SubTasksRepo
) -> bool:
    """Non-terminal states: assigned/running/retrying."""
    updated = subtasks_repo.update_progress(
        result.case_id,
        result.subtask_id,
        status=result.status.value,
        state_order=result.order,
        attempt=result.attempt,
        worker_id=result.worker_id,
        progress=result.progress,
        started_at_if_missing=result.started_at,
    )
    if not updated:
        # `state_order` did not advance: old or duplicate message. It is already
        # superseded by a newer write, so it is deleted anyway.
        logger.debug(
            "stale/duplicate progress, ignoring",
            extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
        )
        return True

    # These two case writes are conditional (see repo/cases.py) so they
    # never overwrite a terminal state with stale progress.
    if result.status is SubTaskStatus.RETRYING:
        cases_repo.mark_retrying_if_not_terminal(result.case_id)
    else:  # assigned / running
        cases_repo.mark_processing_if_needed(result.case_id)

    return True
