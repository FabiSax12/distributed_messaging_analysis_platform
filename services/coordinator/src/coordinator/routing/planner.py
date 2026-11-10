"""Translate the files of a `CreateCaseRequest` into planned subtasks.

Does no I/O (neither S3 nor PostgreSQL): the API layer validates with
`head_object` that `input_key` exists in the bucket BEFORE calling `plan_case`.
"""

from __future__ import annotations

from shared.analysis import CasePriority
from shared.api import CaseFileIn
from shared.messages import ErrorCode, ErrorInfo, utcnow
from shared.models import SubTaskItem
from shared.routing import (
    OPERATION_POOL,
    UnsupportedFile,
    detect_content_type,
    plan_operations,
)
from shared.states import SubTaskStatus, state_order


def plan_case(
    files: list[CaseFileIn], case_id: str, priority: CasePriority
) -> tuple[list[SubTaskItem], int]:
    """Planned subtasks + how many were born already failed (unsupported format).

    The `subtask_id`s are consecutive and global to the case (`{case_id}-0001`,
    `{case_id}-0002`, ...), without resetting the counter per file, so the
    order in the report reflects the order in which the files were received.
    """
    subtasks: list[SubTaskItem] = []
    prefailed_count = 0
    counter = 1

    for file in files:
        try:
            operations = plan_operations(file.input_key, file.operations)
        except UnsupportedFile as exc:
            subtask_id = f"{case_id}-{counter:04d}"
            counter += 1
            # A single prefailed subtask per FILE (not one per requested
            # operation): there is no pool for it, so it is never published.
            subtasks.append(
                SubTaskItem(
                    case_id=case_id,
                    subtask_id=subtask_id,
                    input_key=file.input_key,
                    priority=priority,
                    status=SubTaskStatus.FAILED,
                    state_order=state_order(SubTaskStatus.FAILED, attempt=1),
                    attempt=0,
                    error=ErrorInfo(
                        code=ErrorCode.UNSUPPORTED_FORMAT, message=str(exc)
                    ),
                    finished_at=utcnow(),
                    enqueued=False,
                )
            )
            prefailed_count += 1
            continue

        # plan_operations already validated the content type, so it is never None here.
        content_type = detect_content_type(file.input_key)
        assert content_type is not None

        for operation in operations:
            subtask_id = f"{case_id}-{counter:04d}"
            counter += 1
            subtasks.append(
                SubTaskItem.planned(
                    case_id=case_id,
                    subtask_id=subtask_id,
                    input_key=file.input_key,
                    media_type=content_type,
                    operation=operation,
                    pool=OPERATION_POOL[operation],
                    priority=priority,
                    params=file.params,
                )
            )

    return subtasks, prefailed_count
