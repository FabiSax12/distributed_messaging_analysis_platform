"""Outbox relay: the only code that publishes work messages.

`POST /api/cases` commits the case and its sub-tasks (`enqueued = false`) in
one PostgreSQL transaction and calls `wake()`; this thread then publishes
every dispatchable sub-task with publisher confirms and marks it
`enqueued = true` only after the broker acked it. "Dispatchable" means:

- `pending` and never confirmed: a new case, or one whose publish was
  interrupted (broker down, coordinator crash between commit and publish);
- `retrying` whose `retry_at` has passed (the retry backoff, see
  `barrier/close.py`).

So a crash anywhere between the commit and the confirmation only delays the
publish: the relay finds the rows on its next pass (it also polls every
`OUTBOX_POLL_S`). The opposite gap, a publish confirmed but not yet marked,
can publish the same attempt twice; workers drop the second copy
(`workers/runner/process.py`) and the barrier counts a sub-task once anyway.

A single thread owns the broker connection (pika is not thread-safe), which
is also why API threads do not publish directly. M1 admission control and
the M2 scheduler plug in here: they decide which dispatchable rows are
published now and with what priority (`shared.routing.message_priority`).
"""

from __future__ import annotations

import logging
import threading

from shared.broker import BROKER_ERRORS, Publisher, backoff_delay
from shared.routing import message_priority

from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)

_BATCH = 500


class OutboxRelay:
    def __init__(
        self, *, subtasks_repo: SubTasksRepo, amqp_url: str, poll_s: float
    ) -> None:
        self._subtasks = subtasks_repo
        self._amqp_url = amqp_url
        self._poll_s = poll_s
        self._wakeup = threading.Event()

    def wake(self) -> None:
        """Called by API threads after committing new sub-tasks."""
        self._wakeup.set()

    def run(self, stop_event: threading.Event) -> None:
        publisher = Publisher(self._amqp_url, name="coordinator-outbox")
        failures = 0
        try:
            while not stop_event.is_set():
                try:
                    while self._publish_batch(publisher) == _BATCH:
                        pass  # more rows waiting
                    failures = 0
                except BROKER_ERRORS:
                    delay = backoff_delay(failures)
                    failures += 1
                    logger.warning(
                        "outbox relay cannot reach the broker, retrying",
                        extra={"retry_in_s": round(delay, 2)},
                    )
                    stop_event.wait(delay)
                    continue
                except Exception:
                    logger.exception("outbox relay pass failed, retrying")
                    stop_event.wait(self._poll_s)
                    continue
                self._wakeup.wait(self._poll_s)
                self._wakeup.clear()
                publisher.keepalive()
        finally:
            publisher.close()

    def _publish_batch(self, publisher: Publisher) -> int:
        subtasks = self._subtasks.list_dispatchable(_BATCH)
        published: list[tuple[str, int]] = []
        try:
            for subtask in subtasks:
                # list_dispatchable skips prefailed rows, which have no pool.
                assert subtask.pool is not None
                publisher.publish(
                    subtask.pool.value,
                    subtask.to_message().to_body(),
                    priority=message_priority(subtask.priority),
                    message_id=f"{subtask.subtask_id}:{subtask.attempt}",
                )
                published.append((subtask.subtask_id, subtask.attempt))
        finally:
            # Mark what the broker confirmed even if the batch broke midway.
            self._subtasks.mark_enqueued(published)
        if published:
            logger.info("published sub-tasks", extra={"count": len(published)})
        return len(subtasks)
