"""FIFO or priority with aging; waiting tasks reach critical priority."""

from datetime import UTC, datetime

from shared.routing import MAX_MESSAGE_PRIORITY, message_priority


def priority(task, policy, aging_s, now=None):
    if policy == "fifo":
        return 0
    now = now or datetime.now(UTC)
    age = max(0, (now - task.created_at).total_seconds())
    return min(
        MAX_MESSAGE_PRIORITY, message_priority(task.priority) + int(age / aging_s)
    )


def ordered(tasks, policy, aging_s):
    now = datetime.now(UTC)
    return sorted(
        tasks,
        key=lambda task: (
            -priority(task, policy, aging_s, now),
            task.created_at,
            task.subtask_id,
        ),
    )
