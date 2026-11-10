"""The worker's only PostgreSQL access: its heartbeat row and a read-only
pre-start check. Workers never write `cases` or `subtasks`; every state
change goes to the coordinator through the results queue.

The tables are created by the coordinator (`coordinator/db/schema.sql`).
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from shared.models import WorkerItem, WorkerStatus


@dataclass(frozen=True)
class SubTaskState:
    attempt: int
    closed: bool
    cancel_requested: bool


def create_pool(url: str, *, max_size: int) -> ConnectionPool:
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
        open=False,
        name="worker",
    )
    pool.open(wait=True, timeout=30)
    return pool


def subtask_state(pool: ConnectionPool, subtask_id: str) -> SubTaskState | None:
    """Current attempt, barrier flag and case cancellation of a sub-task."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT s.attempt, s.closed, c.cancel_requested
            FROM subtasks s JOIN cases c ON c.case_id = s.case_id
            WHERE s.subtask_id = %s
            """,
            (subtask_id,),
        ).fetchone()
    return SubTaskState(**row) if row else None


def upsert_heartbeat(pool: ConnectionPool, worker: WorkerItem) -> None:
    """`last_seen = now()` on the database clock, the same clock the
    coordinator's sweeper compares against, so machine clock skew does not
    matter. A heartbeat also revives a row the sweeper marked dead."""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO workers (worker_id, pool, instance_type, hostname, concurrency,
                                 cpu_percent, mem_percent, active_subtasks, status,
                                 started_at, last_seen)
            VALUES (%(worker_id)s, %(pool)s, %(instance_type)s, %(hostname)s,
                    %(concurrency)s, %(cpu_percent)s, %(mem_percent)s,
                    %(active_subtasks)s, %(status)s, %(started_at)s, now())
            ON CONFLICT (worker_id) DO UPDATE SET
                pool = EXCLUDED.pool,
                instance_type = EXCLUDED.instance_type,
                hostname = EXCLUDED.hostname,
                concurrency = EXCLUDED.concurrency,
                cpu_percent = EXCLUDED.cpu_percent,
                mem_percent = EXCLUDED.mem_percent,
                active_subtasks = EXCLUDED.active_subtasks,
                status = EXCLUDED.status,
                started_at = EXCLUDED.started_at,
                last_seen = now()
            """,
            {
                **worker.model_dump(exclude={"last_seen"}),
                "active_subtasks": Jsonb(worker.active_subtasks),
                "status": WorkerStatus.ALIVE.value,
            },
        )
