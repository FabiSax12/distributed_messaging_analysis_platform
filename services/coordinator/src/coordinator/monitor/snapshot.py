"""Build a complete `StateResponse` every `SNAPSHOT_INTERVAL_S` and cache it in memory.

`api/state.py` (`GET /api/state`) only reads `get_latest_snapshot()`: it never
touches PostgreSQL or the broker within the request, so the dashboard can
poll every 2 s without generating proportional load. The loop owns its own
AMQP connection (queue depths via passive `queue_declare`) and rebuilds it if
it drops.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pika.adapters.blocking_connection import BlockingConnection
from shared.api import CaseSummary, QueueDepth, StateResponse, WorkerView
from shared.broker import BROKER_ERRORS, connect, queue_depth
from shared.messages import utcnow
from shared.routing import DLQ, RESULTS_QUEUE, Pool, work_queue

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..repo.workers import WorkersRepo
from . import balancer
from .broadcaster import Broadcaster
from .notifications import emit

logger = logging.getLogger(__name__)

_snapshot: StateResponse | None = None
_lock = threading.Lock()


def get_latest_snapshot() -> StateResponse | None:
    """Latest successful pass, or `None` if the process has not completed any yet."""
    with _lock:
        return _snapshot


class QueueProbe:
    """Ready-message counts per queue over a connection owned by one thread."""

    def __init__(self, amqp_url: str) -> None:
        self._url = amqp_url
        self._connection: BlockingConnection | None = None

    def depths(self, in_flight_by_pool: dict[str, int]) -> list[QueueDepth]:
        try:
            return self._depths(in_flight_by_pool)
        except BROKER_ERRORS:
            self.close()
            raise

    def _depths(self, in_flight_by_pool: dict[str, int]) -> list[QueueDepth]:
        if self._connection is None or not self._connection.is_open:
            self._connection = connect(self._url, name="coordinator-snapshot")
        channel = self._connection.channel()
        try:
            queues = [
                QueueDepth(
                    queue=work_queue(pool),
                    visible=queue_depth(channel, work_queue(pool)),
                    in_flight=in_flight_by_pool.get(pool.value, 0),
                )
                for pool in Pool
            ]
            queues += [
                QueueDepth(queue=name, visible=queue_depth(channel, name), in_flight=0)
                for name in (RESULTS_QUEUE, DLQ)
            ]
        finally:
            channel.close()
        return queues

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except BROKER_ERRORS:
                pass


def run_snapshot_once(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    workers_repo: WorkersRepo,
    probe: QueueProbe,
    recent_cases_limit: int,
    heartbeat_timeout_s: float,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
    broadcaster: Broadcaster | None = None,
) -> None:
    """One pass: gather cases/workers/queues/alerts and replace the global snapshot.

    A failed pass is logged and the previous snapshot is kept: it must not
    bring down the loop thread nor leave `/api/state` without data.
    `broadcaster`, if given, pushes the new snapshot over `WS /api/ws`.
    """
    try:
        snapshot = _build_snapshot(
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            workers_repo=workers_repo,
            probe=probe,
            recent_cases_limit=recent_cases_limit,
            heartbeat_timeout_s=heartbeat_timeout_s,
            pool_saturation_queue_threshold=pool_saturation_queue_threshold,
            pool_saturation_cpu_percent=pool_saturation_cpu_percent,
        )
    except Exception:
        logger.exception("run_snapshot_once failed, keeping the previous snapshot")
        return

    emit(snapshot)

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
    probe: QueueProbe,
    recent_cases_limit: int,
    heartbeat_timeout_s: float,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
) -> StateResponse:
    now = utcnow()

    cases = cases_repo.list_open()
    cases.extend(cases_repo.list_recent_terminal(recent_cases_limit))
    counts = subtasks_repo.status_counts([case.case_id for case in cases])
    case_summaries = [
        CaseSummary(case=case, by_status=counts.get(case.case_id, {})) for case in cases
    ]

    workers = [
        WorkerView(
            **worker.model_dump(),
            alive=worker.is_alive(now=now, timeout_s=heartbeat_timeout_s),
        )
        for worker in workers_repo.list_all()
    ]

    queues = probe.depths(subtasks_repo.in_flight_by_pool())

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


def run_snapshot_loop(
    stop_event: threading.Event,
    interval: float,
    *,
    amqp_url: str,
    **kwargs: Any,
) -> None:
    """Run `run_snapshot_once` every `interval` seconds until `stop_event`."""
    probe = QueueProbe(amqp_url)
    try:
        while not stop_event.is_set():
            run_snapshot_once(probe=probe, **kwargs)
            stop_event.wait(interval)
    finally:
        probe.close()
