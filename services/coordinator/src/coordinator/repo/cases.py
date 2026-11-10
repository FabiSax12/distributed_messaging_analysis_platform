"""Access to the `cases` table.

Writes that touch the barrier (`pending_count`, failed/cancelled counts) live
in `repo/subtasks.py::close` because they must share a transaction with the
sub-task row. Every status write here is guarded in SQL (`status NOT IN
terminal`), so two threads or duplicate messages never overwrite a terminal
case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool
from shared.messages import utcnow
from shared.models import CaseItem
from shared.states import TERMINAL_CASE, CaseStatus

TERMINAL_CASE_VALUES = [status.value for status in TERMINAL_CASE]

_COLUMNS = (
    "case_id, label, source, priority, status, file_count, total, pending_count, "
    "failed_count, cancelled_count, cancel_requested, created_at, started_at, "
    "finished_at, report_key"
)


class CaseNotFound(Exception):
    """No case exists with that `case_id`."""


class CaseAlreadyTerminal(Exception):
    """The case has already reached a terminal state; the operation no longer applies."""


def insert(conn: Connection[Any], case: CaseItem) -> None:
    """INSERT inside the caller's transaction (the outbox in `api/cases.py`)."""
    conn.execute(
        f"""
        INSERT INTO cases ({_COLUMNS})
        VALUES (%(case_id)s, %(label)s, %(source)s, %(priority)s, %(status)s,
                %(file_count)s, %(total)s, %(pending_count)s, %(failed_count)s,
                %(cancelled_count)s, %(cancel_requested)s, %(created_at)s,
                %(started_at)s, %(finished_at)s, %(report_key)s)
        """,
        case.model_dump(),
    )


class CasesRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get(self, case_id: str) -> CaseItem | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM cases WHERE case_id = %s", (case_id,)
            ).fetchone()
        return CaseItem.model_validate(row) if row else None

    def update_cancel_requested(
        self, case_id: str, *, actor: str = "coordinator"
    ) -> CaseItem:
        """Set `cancel_requested = true`. Raises CaseNotFound / CaseAlreadyTerminal.

        `pending_count > 0`: a case whose barrier already reached zero is
        finishing; cancelling it would turn a completed case into cancelled."""
        with self._pool.connection() as conn:
            conn.execute("SELECT set_config('app.actor', %s, true)", (actor,))
            row = conn.execute(
                f"""
                UPDATE cases SET cancel_requested = true
                WHERE case_id = %s AND status <> ALL(%s) AND pending_count > 0
                RETURNING {_COLUMNS}
                """,
                (case_id, TERMINAL_CASE_VALUES),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM cases WHERE case_id = %s", (case_id,)
                ).fetchone()
                raise CaseAlreadyTerminal(case_id) if exists else CaseNotFound(case_id)
        return CaseItem.model_validate(row)

    def finalize_write(
        self, case_id: str, status: CaseStatus, finished_at: datetime, report_key: str
    ) -> bool:
        """Write the final status unless the case is already terminal.

        Returns False when another `finalize()` won the race; that is why
        `barrier/finalize.py` can run concurrently from several threads.
        """
        with self._pool.connection() as conn:
            cur = conn.execute(
                """
                UPDATE cases SET status = %s, finished_at = %s, report_key = %s
                WHERE case_id = %s AND status <> ALL(%s)
                """,
                (status.value, finished_at, report_key, case_id, TERMINAL_CASE_VALUES),
            )
            return cur.rowcount == 1

    def mark_processing_if_needed(self, case_id: str) -> None:
        """queued/retrying -> processing the first time a sub-task starts."""
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE cases
                SET status = %s, started_at = COALESCE(started_at, %s)
                WHERE case_id = %s AND status IN (%s, %s)
                """,
                (
                    CaseStatus.PROCESSING.value,
                    utcnow(),
                    case_id,
                    CaseStatus.QUEUED.value,
                    CaseStatus.RETRYING.value,
                ),
            )

    def mark_retrying_if_not_terminal(
        self, case_id: str, conn: Connection[Any] | None = None
    ) -> None:
        sql = "UPDATE cases SET status = %s WHERE case_id = %s AND status <> ALL(%s)"
        params = (CaseStatus.RETRYING.value, case_id, TERMINAL_CASE_VALUES)
        if conn is not None:
            conn.execute(sql, params)
            return
        with self._pool.connection() as own:
            own.execute(sql, params)

    def list_open(self) -> list[CaseItem]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM cases
                WHERE status <> ALL(%s) ORDER BY case_id
                """,
                (TERMINAL_CASE_VALUES,),
            ).fetchall()
        return [CaseItem.model_validate(row) for row in rows]

    def list_stuck_barriers(self) -> list[str]:
        """Open cases whose barrier already reached zero (finalize never ran)."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT case_id FROM cases WHERE pending_count = 0 AND status <> ALL(%s)",
                (TERMINAL_CASE_VALUES,),
            ).fetchall()
        return [row["case_id"] for row in rows]

    def list_recent(self, status: str | None, limit: int) -> list[CaseItem]:
        """Most recent first (`case_id` is a ULID, sortable by creation)."""
        with self._pool.connection() as conn:
            if status is None:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM cases ORDER BY case_id DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT {_COLUMNS} FROM cases WHERE status = %s
                    ORDER BY case_id DESC LIMIT %s
                    """,
                    (status, limit),
                ).fetchall()
        return [CaseItem.model_validate(row) for row in rows]

    def list_recent_terminal(self, limit: int) -> list[CaseItem]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM cases WHERE status = ANY(%s)
                ORDER BY case_id DESC LIMIT %s
                """,
                (TERMINAL_CASE_VALUES, limit),
            ).fetchall()
        return [CaseItem.model_validate(row) for row in rows]
