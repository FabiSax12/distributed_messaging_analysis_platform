"""Dead-letter queue handler.

Messages land here when a consumer rejects them without requeue: a worker
that cannot parse a work message, or the coordinator rejecting an unreadable
result. A dead-lettered `SubTaskMessage` is recorded as `failed` /
`dead_lettered` (not retried: the message itself is the problem), so its
case can still close. Anything else is logged and dropped.
"""

from __future__ import annotations

import logging

from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties
from pydantic import ValidationError
from shared.broker import OnMessage
from shared.messages import SubTaskMessage, result_from_dead_letter

from ..barrier.close import Barrier

logger = logging.getLogger(__name__)


def build_dlq_handler(barrier: Barrier) -> OnMessage:
    def handle(
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        try:
            subtask = SubTaskMessage.from_body(body)
        except ValidationError:
            logger.warning(
                "dead-lettered message is not a SubTaskMessage, dropping it",
                extra={
                    "headers": properties.headers,
                    "body": body[:500].decode(errors="replace"),
                },
            )
            channel.basic_ack(method.delivery_tag)
            return

        barrier.apply_terminal(result_from_dead_letter(subtask))
        channel.basic_ack(method.delivery_tag)

    return handle
