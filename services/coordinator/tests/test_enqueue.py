"""`routing/enqueue.py::enqueue_case`: partial failure of `SendMessageBatch`.

Uses a hand-mocked SQS client (not moto: moto does not give fine control
over which entries of a `SendMessageBatch` fail) that returns a non-empty
`Failed` for some entries, and confirms that those sub-tasks stay
`enqueued=false` (`mark_enqueued` is never called for them) while the
`Successful` ones become `enqueued=true`. It does use moto for `SubTasksRepo`
(via the `repos` fixture), because that is real DynamoDB.
"""

from __future__ import annotations

from typing import Any

from coordinator.routing.enqueue import enqueue_case

from shared.models import SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority


class _PartialFailureSqs:
    """`send_message_batch` that fails the entries whose Id is in `fail_ids`."""

    def __init__(self, fail_ids: set[str]) -> None:
        self._fail_ids = fail_ids
        self.calls: list[list[str]] = []

    def send_message_batch(
        self, *, QueueUrl: str, Entries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.calls.append([e["Id"] for e in Entries])
        successful = []
        failed = []
        for entry in Entries:
            if entry["Id"] in self._fail_ids:
                failed.append(
                    {"Id": entry["Id"], "Code": "InternalError", "Message": "boom"}
                )
            else:
                successful.append({"Id": entry["Id"]})
        return {"Successful": successful, "Failed": failed}


def test_partial_send_message_batch_failure_leaves_failed_entries_not_enqueued(
    repos, settings
) -> None:
    case_id = "partialcase"
    subtasks = [
        SubTaskItem.planned(
            case_id=case_id,
            subtask_id=f"{case_id}-000{i}",
            input_key=f"dataset/{case_id}-{i}.mp3",
            media_type=MediaType.AUDIO,
            operation=Operation.AUDIO_CONVERT,
            pool=Pool.AUDIO,
            priority=Priority.NORMAL,
        )
        for i in range(1, 4)
    ]
    repos["subtasks"].batch_put_planned(subtasks)

    ok_subtask, fail_subtask_a, fail_subtask_b = subtasks
    sqs = _PartialFailureSqs({fail_subtask_a.subtask_id, fail_subtask_b.subtask_id})

    enqueue_case(
        subtasks,
        sqs=sqs,
        queue_urls=settings.QUEUE_URLS,
        subtasks_repo=repos["subtasks"],
    )

    by_id = {s.subtask_id: s for s in repos["subtasks"].query_by_case(case_id)}
    assert by_id[ok_subtask.subtask_id].enqueued is True
    assert by_id[fail_subtask_a.subtask_id].enqueued is False
    assert by_id[fail_subtask_b.subtask_id].enqueued is False
