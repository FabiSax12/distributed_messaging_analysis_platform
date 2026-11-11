"""Closing a whole CASE: the "all at once" half of the barrier.

Triggered when `pending_count` reaches 0 (from `barrier/close.py`) or when
the sweeper finds a case that sat at zero without being closed (the process
died between the barrier transaction and this function). It is IDEMPOTENT:
it can run in parallel from several threads for the same case, because the
only effect that cannot be repeated safely (`finalize_write`) is guarded in
SQL by "status is not terminal yet". Only the call that wins that UPDATE
publishes the case-finished notification, after the commit, so each case
notifies exactly once whichever path (results consumer, sweeper, prefailed
case at creation) closes it.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.messages import utcnow
from shared.models import report_key
from shared.states import TERMINAL_CASE, CaseStatus, final_case_status

from ..monitor.notifications import case_finished_alert
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
    bucket: str,
) -> CaseStatus | None:
    case = cases_repo.get(case_id)
    if case is None or case.status in TERMINAL_CASE or case.pending_count > 0:
        return None

    subtasks = subtasks_repo.list_by_case(case_id)
    status = final_case_status(case.total, case.failed_count, case.cancel_requested)
    finished_at = utcnow()

    report = build_report(case, subtasks, status=status, finished_at=finished_at)
    key = report_key(case_id)

    # The report is uploaded BEFORE writing the case's final state: if the
    # process dies in between, the case is still open and the sweeper calls
    # `finalize` again, overwriting the same deterministic key.
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=report.model_dump_json().encode("utf-8"),
        ContentType="application/json",
    )

    if not cases_repo.finalize_write(
        case_id, status, finished_at=finished_at, report_key=key
    ):
        logger.debug(
            "finalize: another thread already closed the case",
            extra={"case_id": case_id},
        )
        return None

    logger.info(
        "case finalized",
        extra={"case_id": case_id, "status": status.value, "report_key": key},
    )
    case_finished_alert(report, key)
    return status
