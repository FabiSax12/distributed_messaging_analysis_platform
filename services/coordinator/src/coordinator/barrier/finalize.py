"""Closing a whole CASE: the "all at once" half of the barrier.

Triggered when `pending_count` reaches 0 (from `barrier/close.py`) or
when the sweeper finds a case that sat at zero without being closed (the
process died between the `TransactWriteItems` and this function). It is
IDEMPOTENT: it can run in parallel from several threads/calls for the same
case without corrupting anything, because the only effect that cannot be
repeated safely (`finalize_write`) is protected by an atomic condition.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.messages import utcnow
from shared.models import report_key
from shared.states import TERMINAL_CASE, CaseStatus, final_case_status

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..reports.builder import build_report

logger = logging.getLogger(__name__)


def finalize(
    case_id: str,
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    s3: Any,
    results_bucket: str,
) -> CaseStatus | None:
    case = cases_repo.get(case_id, consistent=True)
    if case is None or case.status in TERMINAL_CASE:
        return None

    subtasks = subtasks_repo.query_by_case(case_id)
    status = final_case_status(case.total, case.failed_count, case.cancel_requested)
    finished_at = utcnow()

    report = build_report(case, subtasks, status=status, finished_at=finished_at)
    key = report_key(case_id)

    # The report is uploaded BEFORE writing the case's final state, on
    # purpose: if the process dies between the two writes, the case is still
    # not terminal and the sweeper (or another duplicate `close_subtask`) will
    # call `finalize` again, which will overwrite the same report at the
    # same deterministic key (no harm in overwriting it with the same
    # content) and this time will manage to write the final state.
    s3.put_object(
        Bucket=results_bucket,
        Key=key,
        Body=report.model_dump_json().encode("utf-8"),
        ContentType="application/json",
    )

    wrote = cases_repo.finalize_write(
        case_id, status, finished_at=finished_at, report_key=key
    )
    if not wrote:
        # Another thread/call won the race and already left the case terminal.
        logger.debug(
            "finalize: another process already closed the case", extra={"case_id": case_id}
        )
        return None

    logger.info(
        "case finalized",
        extra={"case_id": case_id, "status": status.value, "report_key": key},
    )
    return status
