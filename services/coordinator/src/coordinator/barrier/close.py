"""Terminal results: retry policy plus the "per-event" half of the barrier.

Every terminal `ResultMessage` (completed/failed/cancelled), whether a worker
published it or the coordinator synthesized it (dead-lettered message, dead
worker), goes through `Barrier.apply_terminal`:

1. A failure with a transient code (`shared.messages.RETRYABLE_ERRORS`) and
   attempts left is not terminal for the case: the sub-task moves to
   `retrying` with `retry_at = now + backoff` and the outbox relay
   republishes it when due. The coordinator is the only place that decides
   retries, so the max and the backoff are one config (B.4).
2. Past `RETRY_MAX_ATTEMPTS` it is closed as `failed` / `retries_exhausted`.
3. Anything else closes the sub-task: `SubTasksRepo.close` marks it closed
   and decrements its case's `pending_count` in one transaction; at zero the
   case is finalized in the same thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from shared.messages import (
    RETRYABLE_ERRORS,
    ErrorCode,
    ErrorInfo,
    ResultMessage,
    utcnow,
)
from shared.states import SubTaskStatus

from ..config import Settings
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from .finalize import finalize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Barrier:
    cases: CasesRepo
    subtasks: SubTasksRepo
    s3: Any
    bucket: str
    settings: Settings

    def apply_terminal(self, result: ResultMessage) -> None:
        log = {"case_id": result.case_id, "subtask_id": result.subtask_id}
        if self._is_retryable(result):
            if result.attempt < self.settings.RETRY_MAX_ATTEMPTS:
                self._schedule_retry(result)
                return
            assert result.error is not None
            result = result.model_copy(
                update={
                    "error": ErrorInfo(
                        code=ErrorCode.RETRIES_EXHAUSTED,
                        message=(
                            f"failed {result.attempt} attempt(s), last error "
                            f"{result.error.code}: {result.error.message}"
                        )[:2000],
                    )
                }
            )
            logger.warning(
                "retries exhausted", extra={**log, "attempt": result.attempt}
            )
        self.close(result)

    def close(self, result: ResultMessage) -> None:
        pending = self.subtasks.close(result)
        if pending is None:
            # Duplicate, stale attempt or unknown sub-task: nothing changed,
            # but a duplicate may arrive after a crash that left the barrier at
            # zero without finalizing, so check anyway.
            logger.debug(
                "terminal result ignored (duplicate or stale)",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
            self.finalize_if_done(result.case_id)
            return
        if pending == 0:
            self.finalize(result.case_id)

    def finalize_if_done(self, case_id: str) -> None:
        case = self.cases.get(case_id)
        if case is not None and case.pending_count == 0:
            self.finalize(case_id)

    def finalize(self, case_id: str) -> None:
        finalize(
            case_id,
            cases_repo=self.cases,
            subtasks_repo=self.subtasks,
            s3=self.s3,
            bucket=self.bucket,
        )

    def _is_retryable(self, result: ResultMessage) -> bool:
        return (
            result.status is SubTaskStatus.FAILED
            and result.error is not None
            and result.error.code in RETRYABLE_ERRORS
        )

    def _schedule_retry(self, result: ResultMessage) -> None:
        assert result.error is not None
        delay_s = self.settings.retry_delay_s(result.attempt)
        case_id = self.subtasks.schedule_retry(
            subtask_id=result.subtask_id,
            failed_attempt=result.attempt,
            retry_at=utcnow() + timedelta(seconds=delay_s),
            error=result.error,
        )
        extra = {
            "case_id": result.case_id,
            "subtask_id": result.subtask_id,
            "failed_attempt": result.attempt,
            "error_code": result.error.code.value,
        }
        if case_id is None:
            logger.debug("retry already scheduled or sub-task closed", extra=extra)
            return
        logger.info(
            "sub-task scheduled for retry", extra={**extra, "retry_in_s": delay_s}
        )
