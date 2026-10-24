"""Worker heartbeat in the `Workers` table.

It is the worker's only DynamoDB write (IAM only grants it PutItem/UpdateItem
on `Workers`). Every `HEARTBEAT_INTERVAL_S` it replaces its item with the
machine's CPU and memory (psutil) and the `subtask_id`s in flight; the
coordinator reads it in `monitor/snapshot.py` and marks the worker dead if
`WORKER_DEAD_AFTER_S` passes without a report.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psutil
from boto3.dynamodb.types import TypeSerializer

from shared.messages import utcnow
from shared.models import HEARTBEAT_INTERVAL_S, WorkerItem, to_item
from shared.routing import Pool

logger = logging.getLogger(__name__)

_serializer = TypeSerializer()


class Heartbeat:
    def __init__(
        self,
        dynamodb: Any,
        table: str,
        *,
        worker_id: str,
        pool: Pool,
        instance_type: str,
        hostname: str,
        concurrency: int,
    ) -> None:
        self._dynamodb = dynamodb
        self._table = table
        self._worker_id = worker_id
        self._pool = pool
        self._instance_type = instance_type
        self._hostname = hostname
        self._concurrency = concurrency
        self._started_at = utcnow()
        self._active: set[str] = set()
        self._lock = threading.Lock()
        # The first call to cpu_percent(None) always returns 0.0: we
        # discard it here so the first heartbeat already carries a real value.
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
        item = WorkerItem(
            worker_id=self._worker_id,
            pool=self._pool,
            instance_type=self._instance_type,
            hostname=self._hostname,
            concurrency=self._concurrency,
            cpu_percent=psutil.cpu_percent(interval=None),
            mem_percent=psutil.virtual_memory().percent,
            active_subtasks=active,
            started_at=self._started_at,
            last_seen=utcnow(),
        )
        self._dynamodb.put_item(
            TableName=self._table,
            Item={k: _serializer.serialize(v) for k, v in to_item(item).items()},
        )

    def run(
        self, stop_event: threading.Event, interval: float = HEARTBEAT_INTERVAL_S
    ) -> None:
        """Heartbeat thread loop. A failed beat is logged and retried on the
        next cycle: if the thread died, the dashboard would mark a worker that
        is still processing as dead."""
        while not stop_event.is_set():
            try:
                self.beat()
            except Exception:
                logger.exception("heartbeat failed, retrying on the next cycle")
            stop_event.wait(interval)
