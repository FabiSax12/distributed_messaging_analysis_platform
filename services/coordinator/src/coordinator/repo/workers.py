"""Access to the `workers` table. Each worker upserts its own heartbeat row;
the coordinator reads it for `/api/state` and the sweeper marks stale ones
dead (crash detection)."""

from __future__ import annotations

from psycopg_pool import ConnectionPool
from shared.models import WorkerItem, WorkerStatus


class WorkersRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def list_all(self) -> list[WorkerItem]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY worker_id").fetchall()
        return [WorkerItem.model_validate(row) for row in rows]

    def mark_stale_dead(self, timeout_s: float) -> list[str]:
        """alive -> dead for every worker silent for more than `timeout_s`.

        Uses the database clock on both sides (`now()` here, `now()` in the
        worker's upsert), so clock skew between machines does not matter.
        """
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                UPDATE workers SET status = %s
                WHERE status = %s AND last_seen < now() - make_interval(secs => %s)
                RETURNING worker_id
                """,
                (WorkerStatus.DEAD.value, WorkerStatus.ALIVE.value, timeout_s),
            ).fetchall()
        return [row["worker_id"] for row in rows]
