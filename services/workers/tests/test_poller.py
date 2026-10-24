"""`runner/poller.py`: priority consumption order and per-queue time limit.

`POLL_ORDER` is the diagram's strict priority: first the pool's high queue,
then the normal one, and only then the queues of other pools it can help.
"""

from __future__ import annotations

from shared.routing import POLL_ORDER, Pool
from workers.runner.poller import op_timeouts, receive_next

from .conftest import QUEUE_URLS


def _send(aws, queue: str, body: str) -> None:
    aws["sqs"].send_message(QueueUrl=QUEUE_URLS[queue], MessageBody=body)


def test_high_priority_queue_wins(aws) -> None:
    _send(aws, "video-normal", "normal")
    _send(aws, "video-alta", "alta")

    queue, message = receive_next(
        aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0
    )

    assert (queue, message["Body"]) == ("video-alta", "alta")


def test_helps_other_pool_only_when_own_queues_are_empty(aws) -> None:
    _send(aws, "audio-normal", "help")

    queue, message = receive_next(
        aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0
    )

    assert queue == "audio-normal"
    # The attempt travels as an attribute: the worker needs it for `attempt`.
    assert message["Attributes"]["ApproximateReceiveCount"] == "1"


def test_ignores_queues_outside_its_poll_order(aws) -> None:
    # video never takes metadata: it is not in its POLL_ORDER.
    _send(aws, "metadatos-alta", "not-mine")

    assert (
        receive_next(aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0) is None
    )


def test_op_timeout_is_below_each_queue_visibility_timeout(aws) -> None:
    timeouts = op_timeouts(aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.METADATA])

    # metadata 120 s and audio 300 s (legacy SQS queue layout), minus a 30 s margin
    # to publish the result before SQS redelivers the message.
    assert timeouts == {
        "metadatos-alta": 90,
        "metadatos-normal": 90,
        "audio-normal": 270,
    }
