"""Translate the files of a `CreateCaseRequest` into planned subtasks.

Does no I/O (neither S3 nor DynamoDB): the API layer (batch 2) validates with `head_object`
that `input_key` exists in the dataset bucket BEFORE calling `plan_case`.
"""

from __future__ import annotations

from shared.api import CaseFileIn
from shared.messages import ErrorCode, ErrorInfo, utcnow
from shared.models import SubTaskItem
from shared.routing import (
    OPERATION_POOL,
    Priority,
    UnsupportedFile,
    detect_media_type,
    plan_operations,
)
from shared.states import SubTaskStatus, state_order


def plan_case(
    files: list[CaseFileIn], case_id: str, priority: Priority
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
            # operation): we do not know which pool to assign it to because not even the media
            # type could be determined, so it is never enqueued.
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

        # plan_operations already validated the media type, so it is never None here.
        media_type = detect_media_type(file.input_key)
        assert media_type is not None

        for operation in operations:
            subtask_id = f"{case_id}-{counter:04d}"
            counter += 1
            subtasks.append(
                SubTaskItem.planned(
                    case_id=case_id,
                    subtask_id=subtask_id,
                    input_key=file.input_key,
                    media_type=media_type,
                    operation=operation,
                    pool=OPERATION_POOL[operation],
                    priority=priority,
                    params=file.params,
                )
            )

    return subtasks, prefailed_count
