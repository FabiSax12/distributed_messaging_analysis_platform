"""One repair pass over all open cases.

Meant to run once at process startup and then in a loop every
`SWEEP_INTERVAL_S` (the loop and thread are set up by `main.py` in batch 2;
only the function that does ONE pass lives here). It covers two gaps the
"per-event" barrier (`barrier/close.py`) cannot close on its own:

1. A subtask left with `enqueued=false` because its `SendMessageBatch`
   partially failed (see `routing/enqueue.py`) will never receive a
   `ResultMessage` from anyone, so without the sweeper it would stay pending
   forever.
2. A case that reached `pending_count == 0` but the process died between the
   `TransactWriteItems` of `close_subtask` and the `finalize()` it triggers;
   `finalize` is idempotent, so calling it again here is safe.

At the scale of hundreds of cases, one Scan per pass (once a minute) is
acceptable; in production this would be a GSI on `status`/`(status, enqueued)`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from shared.messages import utcnow
from shared.models import SubTaskItem

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..routing.enqueue import enqueue_case
from .finalize import finalize

logger = logging.getLogger(__name__)

_ORPHAN_ENQUEUE_AFTER_S = 30
_STALL_ALERT_AFTER = timedelta(minutes=30)


def run_sweep(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    s3: Any,
    results_bucket: str,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    orphan_cutoff = now - timedelta(seconds=_ORPHAN_ENQUEUE_AFTER_S)

    # A single Scan of orphans for the whole sweep (not one per case): they are
    # candidates from the WHOLE SubTasks table, so they are grouped by case
    # once and re-enqueued together with the other orphaned subtasks of that
    # case, instead of scanning the full table once per open case.
    orphans_by_case: dict[str, list[SubTaskItem]] = {}
    for subtask in subtasks_repo.scan_orphan_enqueue_candidates(orphan_cutoff):
        orphans_by_case.setdefault(subtask.case_id, []).append(subtask)

    for case in cases_repo.scan_non_terminal():
        orphans = orphans_by_case.get(case.case_id)
        if orphans:
            enqueue_case(
                orphans, sqs=sqs, queue_urls=queue_urls, subtasks_repo=subtasks_repo
            )
            logger.info(
                "sweeper: re-enqueued orphaned subtasks",
                extra={"case_id": case.case_id, "count": len(orphans)},
            )

        # Consistent read (not the Scan's, which is eventual) before
        # deciding whether to close the case: `finalize` does not re-check
        # `pending_count`, so the guard has to be here.
        fresh = cases_repo.get(case.case_id, consistent=True)
        if fresh is not None and fresh.pending_count == 0:
            finalize(
                fresh.case_id,
                cases_repo=cases_repo,
                subtasks_repo=subtasks_repo,
                s3=s3,
                results_bucket=results_bucket,
            )
            continue

        _warn_if_stalled(
            case_id=case.case_id,
            created_at=case.created_at,
            subtasks_repo=subtasks_repo,
            now=now,
        )


def _warn_if_stalled(
    *, case_id: str, created_at: datetime, subtasks_repo: SubTasksRepo, now: datetime
) -> None:
    subtasks = subtasks_repo.query_by_case(case_id)
    timestamps = [
        ts
        for subtask in subtasks
        for ts in (subtask.finished_at, subtask.started_at, subtask.created_at)
        if ts is not None
    ]
    last_activity = max(timestamps, default=created_at)
    if now - last_activity > _STALL_ALERT_AFTER:
        # TODO(batch 2): expose this as an `Alert` in `GET /api/state`
        # (monitor/balancer.py builds `shared.api.Alert`); for now
        # the log is enough for it to show up in CloudWatch Insights.
        stall_minutes = int(_STALL_ALERT_AFTER.total_seconds() // 60)
        logger.warning(
            f"stalled case: no subtask changed state in the last {stall_minutes} min",
            extra={"case_id": case_id, "last_activity": last_activity.isoformat()},
        )
