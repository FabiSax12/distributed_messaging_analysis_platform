"""One repair pass. Runs at startup (before accepting traffic) and then every
`SWEEP_INTERVAL_S`; it backs the B.6 "worker crash" and "coordinator
restart" scenarios together with the outbox relay (`routing/outbox.py`),
which republishes whatever was committed but never confirmed by the broker.

1. Crash detection: workers whose heartbeat is older than
   `HEARTBEAT_TIMEOUT_S` are marked `dead`, and every sub-task still
   `assigned`/`running` on a dead worker gets a synthesized `worker_lost`
   failure. That goes through the normal retry policy, so it is retried with
   backoff (or closed as `retries_exhausted`). This covers a hung or
   partitioned worker whose AMQP connection stays open, where the broker
   never redelivers. (A worker process that dies closes its connection; the
   broker redelivers the message and the next worker reports `worker_lost`
   itself, see `workers/runner/process.py`.)
2. Stuck barriers: open cases with `pending_count == 0` (the process died
   between the barrier transaction and `finalize`) are finalized;
   `finalize` is idempotent.

Each query uses an index (see `db/schema.sql`), so a pass is cheap.
"""

from __future__ import annotations

import logging

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, utcnow
from shared.states import SubTaskStatus

from ..repo.workers import WorkersRepo
from .close import Barrier

logger = logging.getLogger(__name__)


def run_sweep(
    *, barrier: Barrier, workers_repo: WorkersRepo, heartbeat_timeout_s: float
) -> None:
    for worker_id in workers_repo.mark_stale_dead(heartbeat_timeout_s):
        logger.warning(
            "worker marked dead: no heartbeat",
            extra={"worker_id": worker_id, "timeout_s": heartbeat_timeout_s},
        )

    for lost in barrier.subtasks.list_in_flight_on_dead_workers():
        logger.warning(
            "requeueing sub-task of a dead worker",
            extra={
                "case_id": lost.case_id,
                "subtask_id": lost.subtask_id,
                "worker_id": lost.worker_id,
                "attempt": lost.attempt,
            },
        )
        barrier.apply_terminal(
            ResultMessage(
                subtask_id=lost.subtask_id,
                case_id=lost.case_id,
                status=SubTaskStatus.FAILED,
                attempt=lost.attempt,
                worker_id=lost.worker_id,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.WORKER_LOST,
                    message=f"worker {lost.worker_id} stopped sending heartbeats",
                ),
            )
        )

    for case_id in barrier.cases.list_stuck_barriers():
        logger.info(
            "sweeper: finalizing case stuck at pending_count = 0",
            extra={"case_id": case_id},
        )
        barrier.finalize(case_id)
