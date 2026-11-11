"""RabbitMQ helpers shared by the coordinator and the workers (pika).

Threading rule: pika's `BlockingConnection` is not thread-safe, so every
thread that talks to the broker owns its own connection and channels and
never hands them to another thread. `Publisher` and `consume` both follow it.

- Topology is declared idempotently by whoever connects first
  (`declare_topology`): the coordinator and every worker call it on each
  (re)connection, so start order does not matter.
- Messages are persistent and published with publisher confirms
  (`confirm_delivery`): `basic_publish` returns only after the broker has the
  message, and raises if it was nacked or unroutable.
- Consumers use manual acks. A connection loss is handled by a reconnect loop
  with exponential backoff; unacked messages go back to their queue and are
  redelivered with `redelivered=True`.
- `amqps://` URLs (Amazon MQ) use TLS with certificate and hostname
  verification; `amqp://` is plain (local RabbitMQ).
"""

from __future__ import annotations

import logging
import random
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

import pika
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection
from pika.exceptions import AMQPError
from pika.spec import Basic, BasicProperties

from .routing import (
    DEAD_LETTER_EXCHANGE,
    DLQ,
    MAX_MESSAGE_PRIORITY,
    RESULTS_QUEUE,
    WORK_EXCHANGE,
    Pool,
    work_queue,
)

logger = logging.getLogger(__name__)

# Errors after which the connection is discarded and rebuilt.
BROKER_ERRORS: tuple[type[BaseException], ...] = (AMQPError, OSError)

HEARTBEAT_S = 30
_RECONNECT_BASE_S = 1.0
_RECONNECT_CAP_S = 30.0

OnMessage = Callable[[BlockingChannel, Basic.Deliver, BasicProperties, bytes], None]


def connection_parameters(url: str, *, name: str) -> pika.URLParameters:
    params = pika.URLParameters(url)
    if params.ssl_options is not None:
        # Verify the broker certificate and its hostname (Amazon MQ uses a
        # public CA, so the default trust store is enough).
        params.ssl_options = pika.SSLOptions(
            ssl.create_default_context(), server_hostname=params.host
        )
    params.heartbeat = HEARTBEAT_S
    params.blocked_connection_timeout = 60
    params.connection_attempts = 1  # retries are done by the callers' loops
    params.socket_timeout = 10
    params.client_properties = {"connection_name": name}
    return params


def connect(url: str, *, name: str) -> BlockingConnection:
    return pika.BlockingConnection(connection_parameters(url, name=name))


def declare_topology(channel: BlockingChannel) -> None:
    """Exchanges, queues and bindings. Idempotent while the arguments do not
    change; changing them (e.g. `x-max-priority`) needs the queue deleted
    first, RabbitMQ rejects a redeclare with different arguments."""
    dead_letter = {"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE}

    channel.exchange_declare(WORK_EXCHANGE, exchange_type="direct", durable=True)
    channel.exchange_declare(DEAD_LETTER_EXCHANGE, exchange_type="fanout", durable=True)
    channel.queue_declare(DLQ, durable=True)
    channel.queue_bind(DLQ, DEAD_LETTER_EXCHANGE)

    for pool in Pool:
        queue = work_queue(pool)
        channel.queue_declare(
            queue,
            durable=True,
            arguments={**dead_letter, "x-max-priority": MAX_MESSAGE_PRIORITY},
        )
        channel.queue_bind(queue, WORK_EXCHANGE, routing_key=pool.value)

    channel.queue_declare(RESULTS_QUEUE, durable=True, arguments=dead_letter)
    channel.queue_bind(RESULTS_QUEUE, WORK_EXCHANGE, routing_key=RESULTS_QUEUE)


def publish(
    channel: BlockingChannel,
    routing_key: str,
    body: str | bytes,
    *,
    priority: int | None = None,
    message_id: str | None = None,
    headers: dict[str, Any] | None = None,
) -> None:
    """Persistent publish to the work exchange. On a confirm channel it
    blocks until the broker acks and raises `NackError`/`UnroutableError`."""
    channel.basic_publish(
        exchange=WORK_EXCHANGE,
        routing_key=routing_key,
        body=body,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            priority=priority,
            message_id=message_id,
            headers=headers,
        ),
        mandatory=True,
    )


def backoff_delay(
    attempt: int, *, base: float = _RECONNECT_BASE_S, cap: float = _RECONNECT_CAP_S
) -> float:
    """Exponential backoff with jitter: base * 2^attempt, capped."""
    return min(cap, base * 2**attempt) * random.uniform(0.8, 1.2)


class Publisher:
    """One connection + one confirm channel, rebuilt on failure.

    Owned by ONE thread. `keepalive()` services AMQP heartbeats while the
    owner is idle; if the broker closed an idle connection anyway, the next
    `publish` reconnects and retries.
    """

    def __init__(self, url: str, *, name: str, retries: int = 3) -> None:
        self._url = url
        self._name = name
        self._retries = retries
        self._connection: BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def _ensure_channel(self) -> BlockingChannel:
        if self._channel is None or not self._channel.is_open:
            self.close()
            self._connection = connect(self._url, name=self._name)
            channel = self._connection.channel()
            channel.confirm_delivery()
            declare_topology(channel)
            self._channel = channel
        return self._channel

    def publish(
        self,
        routing_key: str,
        body: str | bytes,
        *,
        priority: int | None = None,
        message_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        for attempt in range(self._retries + 1):
            try:
                publish(
                    self._ensure_channel(),
                    routing_key,
                    body,
                    priority=priority,
                    message_id=message_id,
                    headers=headers,
                )
                return
            except BROKER_ERRORS:
                self.close()
                if attempt == self._retries:
                    raise
                delay = backoff_delay(attempt)
                logger.warning(
                    "publish failed, reconnecting",
                    extra={"publisher": self._name, "retry_in_s": round(delay, 2)},
                )
                time.sleep(delay)

    def depth(self, pool: Pool) -> int:
        return (
            self._ensure_channel()
            .queue_declare(work_queue(pool), passive=True)
            .method.message_count
        )

    def keepalive(self) -> None:
        if self._connection is None or not self._connection.is_open:
            return
        try:
            self._connection.process_data_events(time_limit=0)
        except BROKER_ERRORS:
            self.close()

    def close(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is not None and connection.is_open:
            try:
                connection.close()
            except BROKER_ERRORS:
                pass


def consume(
    url: str,
    queue: str,
    on_message: OnMessage,
    stop_event: threading.Event,
    *,
    name: str,
    prefetch: int,
    confirm: bool = False,
    transient: tuple[type[BaseException], ...] = (),
) -> None:
    """Consume `queue` until `stop_event`, reconnecting with backoff.

    `on_message(channel, method, properties, body)` must ack, nack or reject
    the message itself. With `confirm=True` the same channel can publish
    with confirms (the worker publishes its results this way, so all its
    AMQP traffic stays on the consuming thread). An exception from
    `on_message` that is not a broker error is logged and then:

    - one of `transient` (e.g. the database is down): requeued after an
      exponential pause, with no cap, because the message itself is fine;
    - anything else: requeued once; if the message was already redelivered
      it is rejected without requeue (dead-letter queue), so a poison
      message cannot loop forever.
    """
    attempt = 0
    while not stop_event.is_set():
        connection: BlockingConnection | None = None
        try:
            connection = connect(url, name=name)
            channel = connection.channel()
            declare_topology(channel)
            channel.basic_qos(prefetch_count=prefetch)
            if confirm:
                channel.confirm_delivery()
            attempt = 0
            transient_failures = 0
            logger.info("consuming", extra={"queue": queue, "consumer": name})
            for method, properties, body in channel.consume(
                queue, inactivity_timeout=1.0
            ):
                if stop_event.is_set():
                    break
                if method is None:
                    continue
                try:
                    on_message(channel, method, properties, body)
                    transient_failures = 0
                except BROKER_ERRORS:
                    raise
                except transient:
                    delay = backoff_delay(transient_failures)
                    transient_failures += 1
                    logger.warning(
                        "transient error in message handler, requeueing",
                        extra={
                            "queue": queue,
                            "consumer": name,
                            "retry_in_s": round(delay, 2),
                        },
                        exc_info=True,
                    )
                    connection.sleep(delay)  # keeps servicing AMQP heartbeats
                    channel.basic_nack(method.delivery_tag, requeue=True)
                except Exception:
                    requeue = not method.redelivered
                    logger.exception(
                        "unhandled error in message handler, %s",
                        "requeueing once" if requeue else "dead-lettering it",
                        extra={"queue": queue, "consumer": name},
                    )
                    if requeue:
                        connection.sleep(1.0)
                    channel.basic_nack(method.delivery_tag, requeue=requeue)
            channel.cancel()  # requeues anything prefetched and not acked
        except BROKER_ERRORS:
            delay = backoff_delay(attempt)
            attempt += 1
            logger.warning(
                "broker connection lost, reconnecting",
                extra={"queue": queue, "consumer": name, "retry_in_s": round(delay, 2)},
                exc_info=True,
            )
            stop_event.wait(delay)
        finally:
            if connection is not None and connection.is_open:
                try:
                    connection.close()
                except BROKER_ERRORS:
                    pass


def queue_depth(channel: BlockingChannel, queue: str) -> int:
    """Ready messages in `queue` (passive declare, does not create it)."""
    frame: Any = channel.queue_declare(queue, passive=True)
    return int(frame.method.message_count)
