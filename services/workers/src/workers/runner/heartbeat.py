"""Worker heartbeat in the `workers` table.

Every `HEARTBEAT_INTERVAL_S` the worker upserts its row with the machine's
CPU and memory (psutil), its pool and the `subtask_id`s in flight. The
coordinator shows it on the dashboard, and its sweeper marks the worker
`dead` when `last_seen` is older than `HEARTBEAT_TIMEOUT_S`, then requeues
the sub-tasks that worker had in flight (B.6 worker crash).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psutil
from psycopg_pool import ConnectionPool

from shared.messages import utcnow
from shared.models import WorkerItem
from shared.routing import Pool

from .db import upsert_heartbeat

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(
        self,
        db: ConnectionPool,
        *,
        worker_id: str,
        pool: Pool,
        instance_type: str,
        hostname: str,
        concurrency: int,
    ) -> None:
        self._db = db
        self._worker_id = worker_id
        self._pool = pool
        self._instance_type = instance_type
        self._hostname = hostname
        self._concurrency = concurrency
        self._started_at = utcnow()
        self._active: set[str] = set()
        self._lock = threading.Lock()
        # The first cpu_percent(None) always returns 0.0: discard it so the
        # first heartbeat already carries a real value.
        psutil.cpu_percent(interval=None)

    @contextmanager
    def track(self, subtask_id: str) -> Iterator[None]:
        """Mark the sub-task as in flight for the duration of the block."""
        with self._lock:
            self._active.add(subtask_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(subtask_id)

    def beat(self) -> None:
        with self._lock:
            active = sorted(self._active)
        now = utcnow()
        upsert_heartbeat(
            self._db,
            WorkerItem(
                worker_id=self._worker_id,
                pool=self._pool,
                instance_type=self._instance_type,
                hostname=self._hostname,
                concurrency=self._concurrency,
                cpu_percent=psutil.cpu_percent(interval=None),
                mem_percent=psutil.virtual_memory().percent,
                active_subtasks=active,
                started_at=self._started_at,
                last_seen=now,
            ),
        )

    def run(self, stop_event: threading.Event, interval: float) -> None:
        """A failed beat is logged and retried on the next cycle: if this
        thread died, the sweeper would requeue work that is still running."""
        while not stop_event.is_set():
            try:
                self.beat()
            except Exception:
                logger.exception("heartbeat failed, retrying on the next cycle")
            stop_event.wait(interval)
