"""Closing a subtask: the "per-event" half of the barrier.

Each terminal `ResultMessage` (completed/failed/cancelled) closes ONE
subtask and decrements ONE from its case's `pending_count`. Both writes
(SubTasks and Cases) go in a single `TransactWriteItems` on purpose: if we
only updated SubTasks and the process died before touching Cases, the case
would be left with a `pending_count` hanging forever (the barrier would
never reach zero); the other way around, `pending_count` would drop without
the subtask reflecting its final state. A transaction makes both things
happen together or not at all.
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ResultMessage
from shared.states import SubTaskStatus

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from .finalize import finalize

logger = logging.getLogger(__name__)


def close_subtask(
    result: ResultMessage,
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
) -> None:
    """Apply a subtask's terminal result and trigger `finalize` if it closes the case.

    `s3`/`results_bucket` are needed here (even though the "close" itself is a
    `TransactWriteItems`) because reaching `pending_count == 0` triggers
    `finalize()` in the same consumer thread, and `finalize` uploads the
    report to S3 before writing the case's final state.
    """
    failed = result.status is SubTaskStatus.FAILED
    cancelled = result.status is SubTaskStatus.CANCELLED

    _warn_if_pool_mismatch(result, subtasks_repo)

    subtask_update = subtasks_repo.build_close_update(result)
    case_update = cases_repo.build_close_update(
        result.case_id, result.subtask_id, failed=failed, cancelled=cancelled
    )

    try:
        dynamodb.transact_write_items(TransactItems=[subtask_update, case_update])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise  # unexpected error (throttling, etc.): let SQS retry the message

        reasons = exc.response.get("CancellationReasons", [])
        if not any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
            # e.g. TransactionConflict: another concurrent write on the
            # same rows. Not a known duplicate, so we re-raise so that
            # the consumer does NOT delete the message and SQS retries it.
            raise

        _log_conditional_check_failed(result, reasons, subtasks_repo)
        _maybe_finalize(result.case_id, cases_repo, subtasks_repo, s3, results_bucket)
        return

    _maybe_finalize(result.case_id, cases_repo, subtasks_repo, s3, results_bucket)


def _log_conditional_check_failed(
    result: ResultMessage,
    reasons: list[dict[str, Any]],
    subtasks_repo: SubTasksRepo,
) -> None:
    """Distinguish a real duplicate (routine) from an invalid reference (not).

    `TransactItems` in `close_subtask` is `[subtask_update, case_update]`, so
    `reasons[0]`/`reasons[1]` correspond in the same order. If the reason
    came from the Cases side (`closed` already contains the `subtask_id`), it
    is unquestionably a real duplicate: that subtask already went through this
    same path before, so both writes were already applied together in a
    previous transaction. If instead the reason came only from the SubTasks
    side (its `state_order` did not advance, or it does not exist at all), a
    targeted `GetItem` is needed to tell which of the two it is.
    """
    subtask_reason = reasons[0] if len(reasons) > 0 else {}
    case_reason = reasons[1] if len(reasons) > 1 else {}

    if case_reason.get("Code") == "ConditionalCheckFailed":
        logger.debug(
            "duplicate/late result, ignoring the close but checking the barrier",
            extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
        )
        return

    if subtask_reason.get("Code") == "ConditionalCheckFailed":
        existing = subtasks_repo.get(result.case_id, result.subtask_id)
        if existing is None:
            logger.warning(
                "ResultMessage references a nonexistent subtask, discarding "
                "the message but this may indicate a bug",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
        else:
            logger.debug(
                "duplicate/late result, ignoring the close but checking the barrier",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
        return

    # Should not happen (the `any()` in `close_subtask` already guaranteed
    # some item failed with `ConditionalCheckFailed`), but just in case we
    # do not treat it as fatal: we are already in the "do not re-raise" branch.
    logger.debug(
        "duplicate/late result, ignoring the close but checking the barrier",
        extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
    )


def _warn_if_pool_mismatch(result: ResultMessage, subtasks_repo: SubTasksRepo) -> None:
    """Alert (without blocking the close) if the worker reported a pool different from the planned one."""
    if result.pool is None:
        return
    existing = subtasks_repo.get(result.case_id, result.subtask_id)
    if existing is not None and existing.pool != result.pool:
        logger.warning(
            "reported pool does not match the planned one, possibly a misconfigured worker",
            extra={
                "case_id": result.case_id,
                "subtask_id": result.subtask_id,
                "reported_pool": result.pool.value,
                "planned_pool": existing.pool.value,
            },
        )


def _maybe_finalize(
    case_id: str,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    s3: Any,
    results_bucket: str,
) -> None:
    case = cases_repo.get(case_id, consistent=True)
    if case is not None and case.pending_count == 0:
        finalize(
            case_id,
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            s3=s3,
            results_bucket=results_bucket,
        )
