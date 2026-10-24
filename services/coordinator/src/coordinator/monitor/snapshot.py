"""Build a complete `StateResponse` every `SNAPSHOT_INTERVAL_S` and cache it in memory.

`api/state.py` (`GET /api/state`) only reads `get_latest_snapshot()`: it never touches
AWS within the request, so the dashboard can poll aggressively (every
2s) without generating proportional load of Scans/GetQueueAttributes. This module
exposes a "single pass" function (`run_snapshot_once`) and whoever uses it builds
the loop (same pattern as `barrier/sweeper.run_sweep`, see its docstring).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

from shared.api import CaseSummary, QueueDepth, StateResponse, WorkerView
from shared.messages import utcnow
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE
from shared.states import TERMINAL_CASE

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..repo.workers import WorkersRepo
from . import balancer
from .broadcaster import Broadcaster

logger = logging.getLogger(__name__)

_ALL_QUEUES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)

# Minimum fields `SubTasksRepo.query_by_case` needs in order to validate
# the resulting `SubTaskItem` (see its docstring), plus `status`, which is the only one
# we actually use here to build `by_status`.
_SUBTASK_STATUS_PROJECTION = [
    "case_id",
    "subtask_id",
    "input_key",
    "priority",
    "status",
]

_snapshot: StateResponse | None = None
_lock = threading.Lock()


def get_latest_snapshot() -> StateResponse | None:
    """Latest successful pass, or `None` if the process has not completed any yet."""
    with _lock:
        return _snapshot


def run_snapshot_once(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    workers_repo: WorkersRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    recent_cases_limit: int,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
    broadcaster: Broadcaster | None = None,
) -> None:
    """One pass: gather cases/workers/queues/alerts and replace the global snapshot.

    If anything fails (throttling, nonexistent queue, etc.) it is logged and the
    previous snapshot is kept; a failed pass must not bring down the thread that
    calls it in a loop, nor leave `/api/state` without data.

    `broadcaster`, if passed, receives the just-replaced snapshot via
    `Broadcaster.publish` to push it over `WS /api/ws` (see `main.py` and
    `monitor/broadcaster.py`). It is optional (`None` by default) so as not to break
    existing callers that do not know about the WebSocket.
    """
    try:
        snapshot = _build_snapshot(
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            workers_repo=workers_repo,
            sqs=sqs,
            queue_urls=queue_urls,
            recent_cases_limit=recent_cases_limit,
            pool_saturation_queue_threshold=pool_saturation_queue_threshold,
            pool_saturation_cpu_percent=pool_saturation_cpu_percent,
        )
    except Exception:
        logger.exception("run_snapshot_once failed, keeping the previous snapshot")
        return

    global _snapshot
    with _lock:
        _snapshot = snapshot

    if broadcaster is not None:
        try:
            broadcaster.publish(snapshot)
        except Exception:
            logger.exception(
                "broadcaster.publish failed, continuing with the next snapshot cycle"
            )


def _build_snapshot(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    workers_repo: WorkersRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    recent_cases_limit: int,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
) -> StateResponse:
    now = utcnow()

    cases = list(cases_repo.scan_non_terminal())
    cases.extend(_recent_terminal_cases(cases_repo, recent_cases_limit))
    case_summaries = [
        CaseSummary(case=case, by_status=_by_status(subtasks_repo, case.case_id))
        for case in cases
    ]

    workers = [
        WorkerView(**worker.model_dump(), alive=worker.is_alive(now=now))
        for worker in workers_repo.scan_all()
    ]

    queues = [_queue_depth(sqs, queue_urls, name) for name in _ALL_QUEUES]

    alerts = balancer.build_alerts(
        queues,
        workers,
        queue_threshold=pool_saturation_queue_threshold,
        cpu_threshold=pool_saturation_cpu_percent,
    )

    return StateResponse(
        generated_at=now,
        cases=case_summaries,
        workers=workers,
        queues=queues,
        alerts=alerts,
    )


def _recent_terminal_cases(cases_repo: CasesRepo, limit: int) -> list[Any]:
    """The `limit` most recent finished cases, across all terminal states.

    `CasesRepo.list_recent` only filters by ONE status at a time, so
    we gather the candidates from each terminal state and re-sort/
    trim here. At this project's scale it is already a full Scan per
    status (see the `list_recent` docstring), so this is simply
    several of those Scans instead of one.
    """
    candidates = [
        case
        for status in TERMINAL_CASE
        for case in cases_repo.list_recent(status.value, limit)
    ]
    candidates.sort(key=lambda c: c.case_id, reverse=True)
    return candidates[:limit]


def _by_status(subtasks_repo: SubTasksRepo, case_id: str) -> dict[str, int]:
    subtasks = subtasks_repo.query_by_case(
        case_id, projection=_SUBTASK_STATUS_PROJECTION
    )
    return dict(Counter(subtask.status.value for subtask in subtasks))


def _queue_depth(sqs: Any, queue_urls: dict[str, str], name: str) -> QueueDepth:
    resp = sqs.get_queue_attributes(
        QueueUrl=queue_urls[name],
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )
    attrs = resp.get("Attributes", {})
    return QueueDepth(
        queue=name,
        visible=int(attrs.get("ApproximateNumberOfMessages", 0)),
        in_flight=int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
    )


def run_snapshot_loop(
    stop_event: threading.Event,
    interval: float,
    **kwargs: Any,
) -> None:
    """Loop that runs `run_snapshot_once` every `interval` seconds until `stop_event`.

    `main.py` launches it in its own thread. `stop_event.wait(interval)` instead of
    `time.sleep` so shutdown does not have to wait out the full interval.
    """
    while not stop_event.is_set():
        run_snapshot_once(**kwargs)
        stop_event.wait(interval)
