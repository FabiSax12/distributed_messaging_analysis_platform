"""Results queue handler, shared by `RESULTS_CONSUMER_THREADS` consumer threads.

Each thread has its own AMQP connection (`shared.broker.consume`) and the
broker spreads messages among them, so results can be applied out of order;
`state_order` and the barrier's `closed` flag make that safe. The message is
acked only after its effect is committed in PostgreSQL: if the coordinator
dies before the ack, the broker redelivers it and the guarded writes turn
the duplicate into a no-op.
"""

from __future__ import annotations

import logging

from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties
from shared.broker import OnMessage
from shared.messages import ResultMessage
from shared.states import TERMINAL_SUBTASK

from ..barrier.close import Barrier

logger = logging.getLogger(__name__)


def build_results_handler(barrier: Barrier) -> OnMessage:
    def handle(
        channel: BlockingChannel,
        method: Basic.Deliver,
        _properties: BasicProperties,
        body: bytes,
    ) -> None:
        try:
            result = ResultMessage.from_body(body)
        except Exception:
            logger.exception("unreadable ResultMessage, dead-lettering it")
            channel.basic_reject(method.delivery_tag, requeue=False)
            return

        if result.status in TERMINAL_SUBTASK:
            barrier.apply_terminal(result)
        elif barrier.subtasks.update_progress(result):
            barrier.cases.mark_processing_if_needed(result.case_id)
        else:
            logger.debug(
                "stale/duplicate progress, ignoring",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
        channel.basic_ack(method.delivery_tag)

    return handle
