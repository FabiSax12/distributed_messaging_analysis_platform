"""Access to the `Workers` table (PK `worker_id`). Each worker writes its own
heartbeat directly (out of scope for this batch); the coordinator only
reads for `monitor/` (batch 2) and for the `/api/state` view.
"""

from __future__ import annotations

from typing import Any

from shared.models import WorkerItem, from_item

from ..aws import deserialize_item


class WorkersRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def scan_all(self) -> list[WorkerItem]:
        """All registered workers (alive or not; `WorkerItem.is_alive()` decides).

        The table is small (one item per worker EC2 instance), so an
        unpaginated Scan is more than enough.
        """
        items: list[WorkerItem] = []
        kwargs: dict[str, Any] = {"TableName": self._table}
        while True:
            resp = self._client.scan(**kwargs)
            items.extend(
                from_item(WorkerItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key
