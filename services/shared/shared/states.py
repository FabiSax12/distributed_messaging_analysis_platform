"""Sub-task and case states, and the rule for ordering them.

The results queue is SQS Standard: it can deliver duplicate or out-of-order
messages. Each sub-task state has an order number (`state_order`) that only
grows. The coordinator updates SubTasks with the condition
`state_order < :new`, so a late message ("running") never overwrites a newer
one ("completed").

The order depends on the attempt: the "retrying" of attempt 2 is newer than the
"running" of attempt 1. Terminal states always win.
"""

from enum import StrEnum


class SubTaskStatus(StrEnum):
    PENDING = "pending"        # the message is visible in its queue
    ASSIGNED = "assigned"      # a worker received it (invisible due to the visibility timeout)
    RUNNING = "running"        # the ffmpeg run / external call started
    RETRYING = "retrying"      # the message went back to the queue (ApproximateReceiveCount > 1)
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_SUBTASK: frozenset[SubTaskStatus] = frozenset(
    {SubTaskStatus.COMPLETED, SubTaskStatus.FAILED, SubTaskStatus.CANCELLED}
)

# Order within a single attempt.
_RANK_IN_ATTEMPT = {
    SubTaskStatus.RETRYING: 0,
    SubTaskStatus.ASSIGNED: 1,
    SubTaskStatus.RUNNING: 2,
}
_TERMINAL_ORDER = 1_000_000


def state_order(status: SubTaskStatus, attempt: int) -> int:
    """Monotonic number for the write condition on SubTasks.

    `attempt` is the message's ApproximateReceiveCount (1 on the first delivery).
    """
    if status in TERMINAL_SUBTASK:
        return _TERMINAL_ORDER
    if status is SubTaskStatus.PENDING:
        return 0
    if attempt < 1:
        raise ValueError("attempt must be >= 1 for states other than pending")
    return attempt * 10 + _RANK_IN_ATTEMPT[status]


class CaseStatus(StrEnum):
    QUEUED = "queued"                          # registered, no sub-task has started
    PROCESSING = "processing"                  # at least one sub-task has started
    RETRYING = "retrying"                      # some sub-task is being retried
    COMPLETED = "completed"                    # all finished successfully
    PARTIALLY_COMPLETED = "partially_completed"  # finished with at least one failure
    FAILED = "failed"                          # all failed
    CANCELLED = "cancelled"                    # the operator cancelled it


TERMINAL_CASE: frozenset[CaseStatus] = frozenset(
    {
        CaseStatus.COMPLETED,
        CaseStatus.PARTIALLY_COMPLETED,
        CaseStatus.FAILED,
        CaseStatus.CANCELLED,
    }
)


def final_case_status(total: int, failed_count: int, cancel_requested: bool) -> CaseStatus:
    """Aggregate state written when the barrier reaches pending_count = 0."""
    if cancel_requested:
        return CaseStatus.CANCELLED
    if failed_count == 0:
        return CaseStatus.COMPLETED
    if failed_count >= total:
        return CaseStatus.FAILED
    return CaseStatus.PARTIALLY_COMPLETED