"""Access to the `Cases` table (PK `case_id`).

All writes that touch the barrier (`pending_count`, `closed`, failed/cancelled
counts) are conditional so that two duplicate threads/messages
never overwrite each other. See `barrier/close.py` and `barrier/finalize.py` for the
reason behind each condition.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import utcnow
from shared.models import CaseItem, from_item, to_item
from shared.states import TERMINAL_CASE, CaseStatus

from ..aws import deserialize_item, serialize_item

# Reusable placeholders for "status IN (<terminal states>)".
_TERMINAL_VALUES: dict[str, dict[str, str]] = {
    f":term{i}": {"S": status.value} for i, status in enumerate(TERMINAL_CASE)
}
_TERMINAL_IN = ", ".join(_TERMINAL_VALUES)


class CaseNotFound(Exception):
    """No case exists with that `case_id`."""


class CaseAlreadyTerminal(Exception):
    """The case has already reached a terminal state; the requested operation no longer applies."""


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return (
        exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )


class CasesRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def put_new(self, case: CaseItem) -> None:
        """PutItem conditioned on the case not existing.

        We do not catch `ConditionalCheckFailedException`: it propagates as is
        (as `botocore.exceptions.ClientError`) so the caller decides
        what a duplicate `case_id` means (it should not happen, they are ULIDs).
        """
        self._client.put_item(
            TableName=self._table,
            Item=serialize_item(to_item(case)),
            ConditionExpression="attribute_not_exists(case_id)",
        )

    def get(self, case_id: str, consistent: bool = True) -> CaseItem | None:
        resp = self._client.get_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}},
            ConsistentRead=consistent,
        )
        item = resp.get("Item")
        if item is None:
            return None
        return from_item(CaseItem, deserialize_item(item))

    def update_cancel_requested(self, case_id: str) -> CaseItem:
        """Set `cancel_requested = true`. 404 if it does not exist, 409 if already terminal.

        We do a prior read to be able to tell "does not exist" apart from "already
        terminal" with a clear message (the API translates them to 404/409), and
        we also protect the write with the same condition: if the case
        became terminal right between the read and the update, the
        `ConditionalCheckFailedException` is also translated to
        `CaseAlreadyTerminal` (the condition is the source of truth, the
        prior read is only to give a better message in the common case).
        """
        existing = self.get(case_id, consistent=True)
        if existing is None:
            raise CaseNotFound(case_id)
        if existing.status in TERMINAL_CASE:
            raise CaseAlreadyTerminal(case_id)

        try:
            resp = self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET cancel_requested = :true",
                ConditionExpression=(
                    f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))"
                ),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":true": {"BOOL": True}, **_TERMINAL_VALUES},
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise CaseAlreadyTerminal(case_id) from exc
            raise
        return from_item(CaseItem, deserialize_item(resp["Attributes"]))

    def build_close_update(
        self, case_id: str, subtask_id: str, *, failed: bool, cancelled: bool
    ) -> dict[str, Any]:
        """Build the Cases `Update` for the barrier's `TransactWriteItems`.

        `barrier/close.py` combines this with `SubTasksRepo.build_close_update`
        in a single transaction: both tables are updated atomically or
        neither is. `ADD closed :idSet` is a string-set add: idempotent (adding
        the same `subtask_id` twice changes nothing), and the condition
        `NOT contains(closed, :id)` is what makes a duplicate result
        (the same `subtask_id` already closed earlier) fail the transaction instead
        of subtracting `pending_count` twice.
        """
        return {
            "Update": {
                "TableName": self._table,
                "Key": {"case_id": {"S": case_id}},
                "UpdateExpression": (
                    "ADD pending_count :minus_one, failed_count :f, "
                    "cancelled_count :c, closed :id_set"
                ),
                "ConditionExpression": "attribute_exists(case_id) AND NOT contains(closed, :id)",
                "ExpressionAttributeValues": {
                    ":minus_one": {"N": "-1"},
                    ":f": {"N": "1" if failed else "0"},
                    ":c": {"N": "1" if cancelled else "0"},
                    ":id_set": {"SS": [subtask_id]},
                    ":id": {"S": subtask_id},
                },
            }
        }

    def finalize_write(
        self, case_id: str, status: CaseStatus, finished_at: datetime, report_key: str
    ) -> bool:
        """UpdateItem conditioned on the case not already being terminal.

        Returns `False` (not an error) if the condition failed: it means
        another concurrent call to `finalize()` already closed the case first;
        `barrier/finalize.py` is idempotent on purpose so that the sweeper and
        the normal barrier can overlap without problems.
        """
        values = serialize_item(
            {
                "status": status.value,
                "finished_at": finished_at.isoformat(),
                "report_key": report_key,
            }
        )
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET #st = :status, finished_at = :finished_at, report_key = :report_key",
                ConditionExpression=f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":status": values["status"],
                    ":finished_at": values["finished_at"],
                    ":report_key": values["report_key"],
                    **_TERMINAL_VALUES,
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return False
            raise
        return True

    def mark_processing_if_needed(self, case_id: str) -> None:
        """The case moves to `processing` the first time a subtask starts.

        Only applies if the case is `queued` or `retrying`; if it is already
        `processing` (another result already set it) or terminal, the condition
        fails and we do nothing; it is an expected race, not an error.
        `started_at` is set only once with `if_not_exists`.
        """
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression=(
                    "SET #st = :processing, "
                    "started_at = if_not_exists(started_at, :now)"
                ),
                ConditionExpression="#st IN (:queued, :retrying)",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":processing": {"S": CaseStatus.PROCESSING.value},
                    ":queued": {"S": CaseStatus.QUEUED.value},
                    ":retrying": {"S": CaseStatus.RETRYING.value},
                    ":now": {"S": utcnow().isoformat()},
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return
            raise

    def mark_retrying_if_not_terminal(self, case_id: str) -> None:
        """The case moves to `retrying` as long as it is not terminal (same logic as above)."""
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET #st = :retrying",
                ConditionExpression=f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":retrying": {"S": CaseStatus.RETRYING.value},
                    **_TERMINAL_VALUES,
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return
            raise

    def scan_non_terminal(self) -> Iterator[CaseItem]:
        """Paginated Scan of cases that have not reached a terminal state.

        At the scale of hundreds of cases a Scan+filter is acceptable; in
        production this would be a GSI on `status` (Scan is O(entire table)
        even if it filters afterwards).
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": f"NOT (#st IN ({_TERMINAL_IN}))",
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": _TERMINAL_VALUES,
        }
        while True:
            resp = self._client.scan(**kwargs)
            for item in resp.get("Items", []):
                yield from_item(CaseItem, deserialize_item(item))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return
            kwargs["ExclusiveStartKey"] = last_key

    def list_recent(self, status: str | None, limit: int) -> list[CaseItem]:
        """Most recent cases, optionally filtered by `status`.

        Without a date GSI, we do a Scan + in-memory sort by `case_id`:
        `case_id` is a ULID (time + random, lexicographically sortable
        by creation), so sorting strings in descending order gives the most
        recent first. At this project's scale (hundreds of cases) a full
        Scan is acceptable; with thousands a GSI
        `status -> created_at` would be advisable.
        """
        kwargs: dict[str, Any] = {"TableName": self._table}
        if status is not None:
            kwargs["FilterExpression"] = "#st = :status"
            kwargs["ExpressionAttributeNames"] = {"#st": "status"}
            kwargs["ExpressionAttributeValues"] = {":status": {"S": status}}

        items: list[CaseItem] = []
        while True:
            resp = self._client.scan(**kwargs)
            items.extend(
                from_item(CaseItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        items.sort(key=lambda c: c.case_id, reverse=True)
        return items[:limit]
