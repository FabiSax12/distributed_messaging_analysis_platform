"""Access to the `SubTasks` table (PK `case_id`, SK `subtask_id`).

The coordinator writes all of a subtask's state; workers never
touch this table directly, they only publish `ResultMessage` (see
`shared/messages.py`). The `state_order < :so` condition on progress/close
writes is the defense against SQS Standard's reordering/duplication
(see `shared/states.py`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ResultMessage
from shared.models import SubTaskItem, _floats_to_decimal, from_item, to_item

from ..aws import deserialize_item, serialize_item

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25
_BATCH_RETRY_MAX = 5


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return (
        exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )


class SubTasksRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def batch_put_planned(self, items: list[SubTaskItem]) -> None:
        """Write a case's planned subtasks, 25 at a time (BatchWriteItem limit).

        `UnprocessedItems` (partial throttling of the batch) are retried with a
        simple backoff; if after `_BATCH_RETRY_MAX` attempts something
        is still unwritten, we treat it as a real error (it should not happen
        with `PAY_PER_REQUEST` barring an extreme spike).
        """
        for start in range(0, len(items), _BATCH_SIZE):
            chunk = items[start : start + _BATCH_SIZE]
            requests = [
                {"PutRequest": {"Item": serialize_item(to_item(item))}}
                for item in chunk
            ]
            self._write_batch_with_retry({self._table: requests})

    def _write_batch_with_retry(
        self, request_items: dict[str, list[dict[str, Any]]]
    ) -> None:
        attempt = 0
        while request_items:
            resp = self._client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems") or {}
            if not request_items:
                return
            attempt += 1
            if attempt > _BATCH_RETRY_MAX:
                raise RuntimeError(
                    f"BatchWriteItem: items left unwritten after {_BATCH_RETRY_MAX} retries"
                )
            pending = sum(len(v) for v in request_items.values())
            logger.warning(
                "BatchWriteItem left unprocessed items, retrying",
                extra={"attempt": attempt, "pending_items": pending},
            )
            time.sleep(0.1 * (2**attempt))

    def get(self, case_id: str, subtask_id: str) -> SubTaskItem | None:
        """Targeted GetItem of one specific subtask, without a condition.

        Used by `barrier/close.py` to tell apart, when the close
        `TransactWriteItems` fails with `ConditionalCheckFailed` on the
        SubTasks side, a real duplicate (the subtask exists, it just already
        has a newer `state_order`) from an invalid reference (no
        subtask exists with that id for that case), and to compare the
        `pool` reported by a worker against the planned one.
        """
        resp = self._client.get_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
        )
        item = resp.get("Item")
        if item is None:
            return None
        return from_item(SubTaskItem, deserialize_item(item))

    def mark_enqueued(self, case_id: str, subtask_id: str) -> None:
        self._client.update_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
            UpdateExpression="SET enqueued = :true",
            ExpressionAttributeValues={":true": {"BOOL": True}},
        )

    def update_progress(
        self,
        case_id: str,
        subtask_id: str,
        *,
        status: str,
        state_order: int,
        attempt: int,
        worker_id: str | None,
        progress: int | None,
        started_at_if_missing: datetime | None,
    ) -> bool:
        """Update a NON-terminal state (assigned/running/retrying).

        Conditioned on `state_order < :so`: if a late or duplicate message
        arrives after a newer one, the condition fails and we return
        `False`; it is the expected case, not an exception (SQS can deliver
        `running` after `retrying` of the same attempt due to reordering).
        All `running` updates of the same attempt share `state_order`
        (`shared.states.state_order` only distinguishes by status/attempt, not by
        progress), so when `progress` is given `state_order == :so` is also
        accepted as long as the new progress is greater than the stored one;
        otherwise a second `running` with more progress in the same
        attempt would never be saved. `started_at` is set only once with
        `if_not_exists`.
        """
        names = {"#st": "status", "#so": "state_order"}
        values: dict[str, Any] = {
            ":st": {"S": status},
            ":so": {"N": str(state_order)},
            ":at": {"N": str(attempt)},
        }
        set_parts = ["#st = :st", "#so = :so", "attempt = :at"]

        if worker_id is not None:
            values[":wi"] = {"S": worker_id}
            set_parts.append("worker_id = :wi")
        if progress is not None:
            values[":pr"] = {"N": str(progress)}
            set_parts.append("progress = :pr")
        if started_at_if_missing is not None:
            values[":sa"] = {"S": started_at_if_missing.isoformat()}
            set_parts.append("started_at = if_not_exists(started_at, :sa)")

        condition = "attribute_exists(subtask_id) AND #so < :so"
        if progress is not None:
            names["#pr"] = "progress"
            condition = (
                "attribute_exists(subtask_id) AND "
                "(#so < :so OR (#so = :so AND (attribute_not_exists(#pr) OR #pr < :pr)))"
            )

        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return False
            raise
        return True

    def build_close_update(self, result: ResultMessage) -> dict[str, Any]:
        """Build the SubTasks `Update` for the barrier's `TransactWriteItems`.

        Same rationale as `CasesRepo.build_close_update`: `barrier/close.py`
        combines both in a single transaction. `#so` is used both in the
        condition (current `state_order` < new) and in the SET (new value)
        because it is the same substitution: the condition is evaluated on the
        value *before* the update. `output_meta` goes through `_floats_to_decimal`: workers
        already avoid sending `float` there, but DynamoDB rejects them
        outright and if one slips through the case gets stuck in `processing`
        forever (the `TransactWriteItems` never gets to execute).
        """
        raw: dict[str, Any] = {
            "status": result.status.value,
            "attempt": result.attempt,
            "output_keys": result.output_keys,
            "output_meta": _floats_to_decimal(result.output_meta),
        }
        if result.worker_id is not None:
            raw["worker_id"] = result.worker_id
        if result.error is not None:
            raw["error"] = result.error.model_dump(mode="json")
        if result.finished_at is not None:
            raw["finished_at"] = result.finished_at.isoformat()

        serialized = serialize_item(raw)
        names = {f"#{k}": k for k in raw} | {"#so": "state_order"}
        values = {f":{k}": v for k, v in serialized.items()}
        values[":so"] = {"N": str(result.order)}

        set_expr = ", ".join(f"#{k} = :{k}" for k in raw) + ", #so = :so"

        return {
            "Update": {
                "TableName": self._table,
                "Key": {
                    "case_id": {"S": result.case_id},
                    "subtask_id": {"S": result.subtask_id},
                },
                "UpdateExpression": f"SET {set_expr}",
                "ConditionExpression": "attribute_exists(subtask_id) AND #so < :so",
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }
        }

    def query_by_case(
        self, case_id: str, projection: list[str] | None = None
    ) -> list[SubTaskItem]:
        """All subtasks of a case (Query, not Scan: uses the PK).

        If you pass `projection`, make sure to include the fields required by
        `SubTaskItem` (case_id, subtask_id, input_key, priority) or
        `from_item` will fail validation; `projection` is for the case where
        the caller knows exactly what it needs, not for "fetch less and
        use it anyway as if it were complete".
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "case_id = :case_id",
            "ExpressionAttributeValues": {":case_id": {"S": case_id}},
        }
        if projection:
            names = {f"#p{i}": field for i, field in enumerate(projection)}
            kwargs["ProjectionExpression"] = ", ".join(names)
            kwargs["ExpressionAttributeNames"] = names

        items: list[SubTaskItem] = []
        while True:
            resp = self._client.query(**kwargs)
            items.extend(
                from_item(SubTaskItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    def scan_orphan_enqueue_candidates(
        self, older_than: datetime
    ) -> Iterator[SubTaskItem]:
        """`pending` subtasks, not yet enqueued, created before `older_than`.

        This is what the sweeper (`barrier/sweeper.py`) re-enqueues when a
        `SendMessageBatch` partially failed and left a subtask with
        `enqueued=false` hanging. Scan with FilterExpression: in production
        it would be a GSI on `(status, enqueued)` to avoid sweeping the whole table.
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": (
                "#st = :pending AND enqueued = :false AND created_at < :older_than"
            ),
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": {
                ":pending": {"S": "pending"},
                ":false": {"BOOL": False},
                ":older_than": {"S": older_than.isoformat()},
            },
        }
        while True:
            resp = self._client.scan(**kwargs)
            for item in resp.get("Items", []):
                yield from_item(SubTaskItem, deserialize_item(item))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return
            kwargs["ExclusiveStartKey"] = last_key
