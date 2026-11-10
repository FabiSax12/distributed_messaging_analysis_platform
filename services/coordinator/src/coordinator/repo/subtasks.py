"""Access to the `subtasks` table, including the barrier write.

The coordinator writes all of a sub-task's state; workers only publish
`ResultMessage`s. Every progress/close write is guarded by `state_order`
(see `shared/states.py`), so a late or duplicate result never overwrites a
newer state, and by `closed`, so the barrier counts each sub-task once.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from shared.messages import ErrorInfo, ResultMessage
from shared.models import SubTaskItem
from shared.routing import Pool
from shared.states import SubTaskStatus, state_order

from .cases import TERMINAL_CASE_VALUES

_COLUMNS = (
    "subtask_id, case_id, input_key, media_type, operation, pool, queue, priority, "
    "status, state_order, attempt, enqueued, retry_at, worker_id, progress, "
    "created_at, started_at, finished_at, output_keys, output_meta, error, params"
)

_IN_FLIGHT = (SubTaskStatus.ASSIGNED.value, SubTaskStatus.RUNNING.value)
_DISPATCHABLE = (SubTaskStatus.PENDING.value, SubTaskStatus.RETRYING.value)


@dataclass(frozen=True)
class InFlight:
    subtask_id: str
    case_id: str
    attempt: int
    worker_id: str


def _row_params(item: SubTaskItem) -> dict[str, Any]:
    params = item.model_dump()
    params["output_keys"] = Jsonb(params["output_keys"])
    params["output_meta"] = Jsonb(params["output_meta"])
    params["params"] = Jsonb(params["params"])
    params["error"] = Jsonb(params["error"]) if params["error"] is not None else None
    return params


def _error(error: ErrorInfo | None) -> Jsonb | None:
    return Jsonb(error.model_dump(mode="json")) if error is not None else None


def insert_many(conn: Connection[Any], items: list[SubTaskItem]) -> None:
    """INSERT inside the caller's transaction (the outbox in `api/cases.py`)."""
    placeholders = ", ".join(f"%({name.strip()})s" for name in _COLUMNS.split(","))
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO subtasks ({_COLUMNS}) VALUES ({placeholders})",
            [_row_params(item) for item in items],
        )


class SubTasksRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ---- Reads ---------------------------------------------------------------- #

    def get(self, subtask_id: str) -> SubTaskItem | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM subtasks WHERE subtask_id = %s", (subtask_id,)
            ).fetchone()
        return SubTaskItem.model_validate(row) if row else None

    def list_by_case(self, case_id: str) -> list[SubTaskItem]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM subtasks WHERE case_id = %s ORDER BY subtask_id",
                (case_id,),
            ).fetchall()
        return [SubTaskItem.model_validate(row) for row in rows]

    def status_counts(self, case_ids: list[str]) -> dict[str, dict[str, int]]:
        """{case_id: {status: count}} for the dashboard overview, in one query."""
        counts: dict[str, Counter[str]] = {case_id: Counter() for case_id in case_ids}
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT case_id, status, count(*) AS n FROM subtasks
                WHERE case_id = ANY(%s) GROUP BY case_id, status
                """,
                (case_ids,),
            ).fetchall()
        for row in rows:
            counts[row["case_id"]][row["status"]] = row["n"]
        return {case_id: dict(counter) for case_id, counter in counts.items()}

    def in_flight_by_pool(self) -> dict[str, int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT pool, count(*) AS n FROM subtasks
                WHERE status = ANY(%s) AND NOT closed GROUP BY pool
                """,
                (list(_IN_FLIGHT),),
            ).fetchall()
        return {row["pool"]: row["n"] for row in rows}

    # ---- Outbox ---------------------------------------------------------------- #

    def list_dispatchable(
        self, limit: int, *, policy: str = "fifo", aging_s: float = 30
    ) -> list[SubTaskItem]:
        """New sub-tasks not confirmed by the broker yet, plus retries that are due."""
        with self._pool.connection() as conn:
            rows = []
            for pool in Pool:
                rows.extend(
                    conn.execute(
                        f"""
                    SELECT {_COLUMNS} FROM subtasks
                    WHERE NOT enqueued AND NOT closed AND queue IS NOT NULL
                      AND pool = %s AND status = ANY(%s) AND (retry_at IS NULL OR retry_at <= now())
                    ORDER BY
                      CASE WHEN %s = 'aging' THEN LEAST(4,
                        CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                          WHEN 'medium' THEN 2 ELSE 1 END +
                        FLOOR(EXTRACT(EPOCH FROM (now()-created_at)) / %s)) ELSE 0 END DESC,
                      created_at, subtask_id LIMIT %s
                    """,
                        (pool.value, list(_DISPATCHABLE), policy, aging_s, limit),
                    ).fetchall()
                )
        return [SubTaskItem.model_validate(row) for row in rows]

    def mark_enqueued(self, published: list[tuple[str, int]]) -> None:
        """`published` = (subtask_id, attempt). The attempt guard keeps a
        confirmation of an old attempt from hiding a newer retry."""
        if not published:
            return
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "UPDATE subtasks SET enqueued = true WHERE subtask_id = %s AND attempt = %s",
                published,
            )

    # ---- Progress, retry and close ---------------------------------------------- #

    def update_progress(self, result: ResultMessage) -> bool:
        """Apply a NON-terminal state (assigned/running).

        Accepted if `state_order` advances. Every `running` of one attempt
        shares a `state_order`, so an equal order is also accepted when it
        carries more progress than the stored one. Returns False for a stale
        or duplicate message (expected, not an error).
        """
        with self._pool.connection() as conn:
            cur = conn.execute(
                """
                UPDATE subtasks SET
                    status = %(status)s,
                    state_order = %(so)s,
                    worker_id = COALESCE(%(worker_id)s, worker_id),
                    progress = COALESCE(%(progress)s, progress),
                    started_at = COALESCE(started_at, %(started_at)s)
                WHERE subtask_id = %(subtask_id)s AND NOT closed
                  AND (state_order < %(so)s
                       OR (state_order = %(so)s AND %(progress)s::int IS NOT NULL
                           AND COALESCE(progress, -1) < %(progress)s::int))
                """,
                {
                    "status": result.status.value,
                    "so": result.order,
                    "worker_id": result.worker_id,
                    "progress": result.progress,
                    "started_at": result.started_at,
                    "subtask_id": result.subtask_id,
                },
            )
            return cur.rowcount == 1

    def schedule_retry(
        self,
        *,
        subtask_id: str,
        failed_attempt: int,
        delay_s: float,
        error: ErrorInfo,
    ) -> str | None:
        """`retrying` + backoff for the attempt after `failed_attempt`.

        Only if `failed_attempt` is still the current attempt and the
        sub-task is open: a duplicate failure, or one from an attempt that
        was already retried, changes nothing. `retry_at` is computed with
        the database clock, the same one `list_dispatchable` compares with. `enqueued = false` hands the
        sub-task back to the outbox relay, which publishes it at `retry_at`.
        The case moves to `retrying` in the same transaction. Returns the
        case_id, or None if nothing changed.
        """
        next_attempt = failed_attempt + 1
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE subtasks SET
                    status = %s, attempt = %s, state_order = %s,
                    retry_at = now() + make_interval(secs => %s),
                    enqueued = false, worker_id = NULL, progress = NULL, error = %s
                WHERE subtask_id = %s AND NOT closed AND attempt = %s
                RETURNING case_id
                """,
                (
                    SubTaskStatus.RETRYING.value,
                    next_attempt,
                    state_order(SubTaskStatus.RETRYING, next_attempt),
                    delay_s,
                    _error(error),
                    subtask_id,
                    failed_attempt,
                ),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE cases SET status = 'retrying' WHERE case_id = %s AND status <> ALL(%s)",
                (row["case_id"], TERMINAL_CASE_VALUES),
            )
            return row["case_id"]

    def close(self, result: ResultMessage) -> int | None:
        """The barrier: close the sub-task and decrement its case, atomically.

        One statement (a data-modifying CTE) so both rows change in the same
        transaction or neither does. `NOT closed` makes it idempotent per
        sub-task: a concurrent duplicate waits on the row lock, re-checks the
        condition and updates nothing. A failure or cancellation only closes
        the current attempt (an older attempt was already retried); a
        completion from any attempt is valid work and closes it.

        Returns the case's new `pending_count`, or None if the result was a
        duplicate, stale or referenced an unknown sub-task.
        """
        failed = result.status is SubTaskStatus.FAILED
        cancelled = result.status is SubTaskStatus.CANCELLED
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                WITH closed_subtask AS (
                    UPDATE subtasks SET
                        status = %(status)s,
                        state_order = %(so)s,
                        closed = true,
                        retry_at = NULL,
                        worker_id = COALESCE(%(worker_id)s, worker_id),
                        started_at = COALESCE(started_at, %(started_at)s),
                        finished_at = %(finished_at)s,
                        progress = CASE WHEN %(completed)s THEN 100 ELSE progress END,
                        output_keys = %(output_keys)s,
                        output_meta = %(output_meta)s,
                        error = %(error)s
                    WHERE subtask_id = %(subtask_id)s AND case_id = %(case_id)s
                      AND NOT closed AND state_order < %(so)s
                      AND (%(completed)s OR attempt = %(attempt)s)
                    RETURNING case_id
                )
                UPDATE cases SET
                    pending_count = cases.pending_count - 1,
                    failed_count = cases.failed_count + %(failed)s,
                    cancelled_count = cases.cancelled_count + %(cancelled)s
                FROM closed_subtask
                WHERE cases.case_id = closed_subtask.case_id
                RETURNING cases.pending_count
                """,
                {
                    "status": result.status.value,
                    "so": result.order,
                    "worker_id": result.worker_id,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "completed": result.status is SubTaskStatus.COMPLETED,
                    "output_keys": Jsonb(result.output_keys),
                    "output_meta": Jsonb(result.output_meta),
                    "error": _error(result.error),
                    "subtask_id": result.subtask_id,
                    "case_id": result.case_id,
                    "attempt": result.attempt,
                    "failed": int(failed),
                    "cancelled": int(cancelled),
                },
            ).fetchone()
        return row["pending_count"] if row else None

    # ---- Crash detection ------------------------------------------------------- #

    def list_in_flight_on_dead_workers(self) -> list[InFlight]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT s.subtask_id, s.case_id, s.attempt, s.worker_id
                FROM subtasks s JOIN workers w ON w.worker_id = s.worker_id
                WHERE w.status = 'dead' AND s.status = ANY(%s) AND NOT s.closed
                """,
                (list(_IN_FLIGHT),),
            ).fetchall()
        return [InFlight(**row) for row in rows]
