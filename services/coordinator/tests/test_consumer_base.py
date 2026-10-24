"""`consumers/base.py::SqsConsumerThread`: no prior coverage.

Uses an `sqs` stub (real moto is not needed: `receive_message`/
`delete_message_batch` are enough) and calls `_poll_once()` directly instead
of starting the real thread, so the tests are deterministic.
"""

from __future__ import annotations

import threading
from typing import Any

from coordinator.consumers.base import SqsConsumerThread


class _FakeSqs:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.deleted_receipt_handles: list[str] = []
        self.delete_batch_calls = 0

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        if self._messages:
            return {"Messages": self._messages}
        return {}

    def delete_message_batch(
        self, *, QueueUrl: str, Entries: list[dict[str, str]]
    ) -> dict[str, Any]:
        self.delete_batch_calls += 1
        self.deleted_receipt_handles.extend(e["ReceiptHandle"] for e in Entries)
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}


def _message(msg_id: str) -> dict[str, Any]:
    return {"MessageId": msg_id, "ReceiptHandle": f"rh-{msg_id}", "Body": "{}"}


def _make_thread(sqs: _FakeSqs, handler, stop_event: threading.Event | None = None):
    return SqsConsumerThread(
        sqs=sqs,
        queue_url="https://example.com/queue",
        handler=handler,
        stop_event=stop_event or threading.Event(),
        name="test-consumer",
    )


def test_handler_returning_true_deletes_message() -> None:
    sqs = _FakeSqs([_message("1")])
    thread = _make_thread(sqs, handler=lambda message: True)

    thread._poll_once()

    assert sqs.deleted_receipt_handles == ["rh-1"]
    assert sqs.delete_batch_calls == 1


def test_handler_raising_exception_does_not_delete_message() -> None:
    sqs = _FakeSqs([_message("1")])

    def _boom(message: dict[str, Any]) -> bool:
        raise RuntimeError("falla transitoria")

    thread = _make_thread(sqs, handler=_boom)

    thread._poll_once()

    assert sqs.deleted_receipt_handles == []
    assert sqs.delete_batch_calls == 0


def test_handler_returning_false_does_not_delete_message() -> None:
    sqs = _FakeSqs([_message("1")])
    thread = _make_thread(sqs, handler=lambda message: False)

    thread._poll_once()

    assert sqs.deleted_receipt_handles == []
    assert sqs.delete_batch_calls == 0


def test_stop_event_set_mid_batch_still_processes_whole_batch() -> None:
    """A batch that was already received is processed in full even if
    `stop_event` fires midway (see the `_poll_once` docstring)."""
    sqs = _FakeSqs([_message("1"), _message("2"), _message("3")])
    stop_event = threading.Event()
    processed: list[str] = []

    def handler(message: dict[str, Any]) -> bool:
        processed.append(message["MessageId"])
        if message["MessageId"] == "2":
            stop_event.set()
        return True

    thread = _make_thread(sqs, handler=handler, stop_event=stop_event)

    thread._poll_once()

    assert processed == ["1", "2", "3"]  # none is skipped because of stop_event
    assert sqs.deleted_receipt_handles == ["rh-1", "rh-2", "rh-3"]
