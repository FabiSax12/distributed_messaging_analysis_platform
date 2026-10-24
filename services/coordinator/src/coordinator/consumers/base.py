"""Generic SQS consumer thread, reusable for results and the DLQ.

The `handler` processes ONE raw SQS message and returns `True` if it must be
deleted (already applied, or unrecoverable garbage) or `False` if not
(transient failure: let the visibility timeout expire and SQS redeliver
it).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], bool]


class SqsConsumerThread(threading.Thread):
    def __init__(
        self,
        *,
        sqs: Any,
        queue_url: str,
        handler: Handler,
        stop_event: threading.Event,
        wait_time: int = 20,
        max_messages: int = 10,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._sqs = sqs
        self._queue_url = queue_url
        self._handler = handler
        self._stop_event = stop_event
        self._wait_time = wait_time
        self._max_messages = max_messages

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()

    def _poll_once(self) -> None:
        try:
            resp = self._sqs.receive_message(
                QueueUrl=self._queue_url,
                WaitTimeSeconds=self._wait_time,
                MaxNumberOfMessages=self._max_messages,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except Exception:
            logger.exception(
                "ReceiveMessage failed", extra={"queue_url": self._queue_url}
            )
            return

        messages = resp.get("Messages", [])
        if not messages:
            return

        # The batch we already started receiving is processed in full even if
        # stop_event fires midway: we do not want to leave messages
        # half-handled (already invisible from the receive) without deciding
        # whether they are deleted or not.
        to_delete: list[dict[str, Any]] = []
        for message in messages:
            try:
                should_delete = self._handler(message)
            except Exception:
                logger.exception(
                    "unhandled exception in the handler, leaving it for SQS to retry",
                    extra={
                        "queue_url": self._queue_url,
                        "message_id": message.get("MessageId"),
                    },
                )
                should_delete = False
            if should_delete:
                to_delete.append(message)

        if to_delete:
            self._delete_batch(to_delete)

    def _delete_batch(self, messages: list[dict[str, Any]]) -> None:
        entries = [
            {"Id": str(i), "ReceiptHandle": message["ReceiptHandle"]}
            for i, message in enumerate(messages)
        ]
        for start in range(0, len(entries), 10):  # DeleteMessageBatch limit
            chunk = entries[start : start + 10]
            try:
                self._sqs.delete_message_batch(QueueUrl=self._queue_url, Entries=chunk)
            except Exception:
                logger.exception(
                    "DeleteMessageBatch failed, messages will reappear and be reprocessed",
                    extra={"queue_url": self._queue_url},
                )
