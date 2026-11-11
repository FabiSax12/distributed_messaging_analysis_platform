"""Cross-process memory admission, inside the case/outbox transaction."""

from typing import Any

from fastapi import HTTPException
from shared.models import SubTaskItem

from ..config import Settings
from ..repo.cases import TERMINAL_CASE_VALUES

LOCK_ID = 6600_0002
RESERVATION_KEY = "_coordinator_m1"


def reserve(
    conn: Any,
    settings: Settings,
    subtasks: list[SubTaskItem],
    input_bytes: int,
    sizes: dict[str, int],
) -> None:
    """Check limits and attach server-owned reservation before either INSERT.

    Reservation = base(op) once per distinct operation in the case
    + input size * multiplier per queued subtask. Each pool runs one analysis
    at a time, so a case's subtasks of one operation load its model
    sequentially, not side by side. Across cases it stays conservative: every
    active case reserves its own model bases.

    Reservation lives in the first subtask's existing JSON params. No schema
    migration or separate release write: a terminal case stops counting.
    """
    if (
        settings.M1_MAX_CASE_INPUT_BYTES
        and input_bytes > settings.M1_MAX_CASE_INPUT_BYTES
    ):
        raise HTTPException(
            413,
            detail={
                "reason": "case input exceeds limit",
                "input_bytes": input_bytes,
                "limit_bytes": settings.M1_MAX_CASE_INPUT_BYTES,
            },
        )
    # Never trust a caller-supplied reservation, including when limits are off.
    for task in subtasks:
        task.params = {k: v for k, v in task.params.items() if k != RESERVATION_KEY}
    if not (settings.M1_MAX_ACTIVE_CASES or settings.M1_MEMORY_BUDGET_BYTES):
        return
    queued = [task for task in subtasks if task.queue is not None]
    memory = sum(
        settings.M1_OPERATION_BASE_BYTES.get(operation, settings.M1_TASK_BASE_BYTES)
        for operation in {str(task.operation) for task in queued}
    ) + sum(
        sizes[task.input_key] * settings.M1_INPUT_MEMORY_MULTIPLIER for task in queued
    )

    def unavailable(reason: str, **details: Any) -> None:
        raise HTTPException(
            503,
            detail={"reason": reason, **details},
            headers={"Retry-After": str(settings.M1_RETRY_AFTER_S)},
        )

    if settings.M1_MEMORY_BUDGET_BYTES and memory > settings.M1_MEMORY_BUDGET_BYTES:
        raise HTTPException(
            413,
            detail={
                "reason": "case reservation exceeds memory budget",
                "reservation_bytes": memory,
                "budget_bytes": settings.M1_MEMORY_BUDGET_BYTES,
            },
        )
    # Try-lock bounds overload latency; the transaction retains it through commit.
    locked = conn.execute(
        "SELECT pg_try_advisory_xact_lock(%s) AS locked", (LOCK_ID,)
    ).fetchone()
    if not locked["locked"]:
        unavailable("admission busy; retry request")
    # READ COMMITTED gives this statement a fresh snapshot after lock acquisition.
    usage = conn.execute(
        """
        SELECT count(*) AS active,
               count(*) FILTER (WHERE r.memory IS NULL) AS unreserved,
               COALESCE(sum(r.memory), 0) AS memory
        FROM cases c
        LEFT JOIN LATERAL (
            SELECT (params -> %s ->> 'memory_bytes')::bigint AS memory
            FROM subtasks WHERE case_id = c.case_id
            ORDER BY subtask_id LIMIT 1
        ) r ON true
        WHERE c.status <> ALL(%s)
        """,
        (RESERVATION_KEY, TERMINAL_CASE_VALUES),
    ).fetchone()
    if settings.M1_MAX_ACTIVE_CASES and usage["active"] >= settings.M1_MAX_ACTIVE_CASES:
        unavailable(
            "active case limit reached",
            active_cases=usage["active"],
            limit=settings.M1_MAX_ACTIVE_CASES,
        )
    if settings.M1_MEMORY_BUDGET_BYTES:
        if usage["unreserved"]:
            unavailable(
                "drain pre-admission cases before enabling memory budget",
                unreserved_cases=usage["unreserved"],
            )
        if usage["memory"] + memory > settings.M1_MEMORY_BUDGET_BYTES:
            unavailable(
                "memory reservations exhausted",
                reserved_bytes=usage["memory"],
                requested_bytes=memory,
                budget_bytes=settings.M1_MEMORY_BUDGET_BYTES,
            )
    min(subtasks, key=lambda task: task.subtask_id).params[RESERVATION_KEY] = {
        "memory_bytes": memory,
        "input_bytes": input_bytes,
    }
