"""Dead-letter queue handler.

Messages land here when a consumer rejects them without requeue: a worker
that cannot parse a work message, a handler that failed twice on the same
message, or the coordinator rejecting an unreadable result. A dead-lettered
work message is recorded as `failed` / `dead_lettered` (not retried: the
message itself is the problem), so its case can still close. If its body
cannot be parsed, the sub-task is identified from the AMQP properties the
outbox relay sets (`message_id = "{subtask_id}:{attempt}"`, `case_id`
header). Anything else is logged and dropped.
"""

from __future__ import annotations

import logging

from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties
from pydantic import ValidationError
from shared.broker import OnMessage
from shared.messages import (
    ErrorCode,
    ErrorInfo,
    ResultMessage,
    SubTaskMessage,
    result_from_dead_letter,
    utcnow,
)
from shared.states import SubTaskStatus

from ..barrier.close import Barrier

logger = logging.getLogger(__name__)


def _from_properties(properties: BasicProperties) -> ResultMessage | None:
    """Failure for an unparseable work message, from its AMQP properties."""
    subtask_id, sep, attempt = (properties.message_id or "").rpartition(":")
    case_id = (properties.headers or {}).get("case_id")
    if not sep or not subtask_id or not attempt.isdigit() or not case_id:
        return None
    return ResultMessage(
        subtask_id=subtask_id,
        case_id=str(case_id),
        status=SubTaskStatus.FAILED,
        attempt=max(1, int(attempt)),
        finished_at=utcnow(),
        error=ErrorInfo(
            code=ErrorCode.DEAD_LETTERED,
            message="an unreadable work message was dead-lettered by the broker",
        ),
    )


def build_dlq_handler(barrier: Barrier) -> OnMessage:
    def handle(
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        try:
            result = result_from_dead_letter(SubTaskMessage.from_body(body))
        except ValidationError:
            result = _from_properties(properties)

        if result is None:
            logger.warning(
                "dead-lettered message is not a work message, dropping it",
                extra={
                    "headers": properties.headers,
                    "body": body[:500].decode(errors="replace"),
                },
            )
        else:
            barrier.apply_terminal(result)
        channel.basic_ack(method.delivery_tag)

    return handle
