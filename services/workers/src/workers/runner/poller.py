"""Queue consumption in the order of `shared.routing.POLL_ORDER`.

Strict priority: on each pass the list is walked from the start and the first
message found is taken, so a `high` sub-task never waits behind a `normal` one
in the same pool. Queues from other pools (cross-pool help) go at the end of
the list and are only consumed if the worker's own queues are empty.

The work queues have `receive_wait_time_seconds = 0` in Terraform, so the
worker requests long polling on every `receive_message` (`wait_s`), just like
`services/coordinator/scripts/fake_worker.py`. With an empty queue, that also keeps the
loop from spinning.
"""

from __future__ import annotations

from typing import Any

RECEIVE_WAIT_S = 2

# Margin between the operation limit and its queue's visibility timeout: the
# time it takes to upload the output to S3 and publish the result before SQS
# hands the message to another worker again.
_PUBLISH_MARGIN_S = 30


def receive_next(
    sqs: Any,
    queue_urls: dict[str, str],
    poll_order: tuple[str, ...],
    *,
    wait_s: int = RECEIVE_WAIT_S,
) -> tuple[str, dict[str, Any]] | None:
    """First available message following `poll_order`, or `None` if all are empty."""
    for queue_name in poll_order:
        resp = sqs.receive_message(
            QueueUrl=queue_urls[queue_name],
            WaitTimeSeconds=wait_s,
            MaxNumberOfMessages=1,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        if messages:
            return queue_name, messages[0]
    return None


def op_timeouts(
    sqs: Any, queue_urls: dict[str, str], poll_order: tuple[str, ...]
) -> dict[str, int]:
    """Execution limit per source queue, read once at startup.

    It depends on the queue, not on the worker's pool: a metadata worker that
    helps with `audio-normal` inherits audio's 300 s, not its own 120 s.
    """
    timeouts: dict[str, int] = {}
    for queue_name in poll_order:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_urls[queue_name], AttributeNames=["VisibilityTimeout"]
        )["Attributes"]
        visibility = int(attrs["VisibilityTimeout"])
        timeouts[queue_name] = max(visibility - _PUBLISH_MARGIN_S, visibility // 2)
    return timeouts
