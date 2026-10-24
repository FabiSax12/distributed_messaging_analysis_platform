"""`Broadcaster`: bridge between the background threads and the asyncio event loop
that serves `WS /api/ws` (see `api/ws.py`).

`monitor/snapshot.py` calls `publish()` from the `snapshot-loop` thread (see
`main.py`), never from a coroutine. `publish()` is thread-safe but NEVER
sends anything to a WebSocket directly; that would violate asyncio's
concurrency model (`WebSocket`/`asyncio.Queue` objects are not
thread-safe). Instead it builds the message once and schedules its actual delivery
on the loop with `loop.call_soon_threadsafe(...)`; only `_deliver`, which runs
inside the loop, touches the clients' queues.

Each connected client has an `asyncio.Queue(maxsize=1)`: a slow client (a
background dashboard tab, a high-latency connection) never
accumulates a backlog of stale states. If a new state arrives before the
client has read the previous one, the old one is dropped and the new one kept, so the
client always ends up seeing the latest state, never a stale one.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import WebSocket
from shared.api import StateResponse

logger = logging.getLogger(__name__)

_ENVELOPE_TYPE = "state"


def _offer(queue: asyncio.Queue[str], message: str) -> None:
    """Deliver without ever blocking: if the (size 1) queue is full, drop the
    old item and keep the new one."""
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        # This always runs on the loop (only one thread touches it at a time), so
        # it should not happen between the `full()` above and this point; just
        # in case, we drop instead of breaking delivery to other clients.
        logger.debug("WS client queue full after draining it, dropping the message")


class Broadcaster:
    """Registry of connected WS clients + last published state."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[asyncio.Queue[str], WebSocket] = {}
        self._lock = threading.Lock()
        self._last_comparable: dict[str, Any] | None = None
        self.latest: str | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once from the `lifespan`, with `asyncio.get_running_loop()`,
        before starting the background threads (the snapshot thread needs this
        reference ready in order to call `publish`)."""
        self._loop = loop

    def publish(self, state: StateResponse) -> None:
        """Thread-safe: meant to be called from the snapshot thread.

        Only publishes if the content changed relative to the last successful
        send, ignoring fields that regenerate on every snapshot pass
        even if nothing relevant changed: `generated_at` always, and also
        `alerts[].ts` (an alert that stays active is re-emitted with a new
        `ts` on every pass) and `workers[].last_seen` (changes with every
        heartbeat of a live worker). Without excluding these last two, as soon as
        there is an active alert or a connected worker the comparison always says
        "changed" and deduplication is broken in the real case.
        """
        comparable = state.model_dump(
            mode="json",
            exclude={
                "generated_at": True,
                "alerts": {"__all__": {"ts"}},
                "workers": {"__all__": {"last_seen"}},
            },
        )
        with self._lock:
            if comparable == self._last_comparable:
                return
            self._last_comparable = comparable

        # Serialized ONCE here. Each client receives this same prebuilt text,
        # with nobody re-parsing/re-serializing per connection.
        state_json = state.model_dump_json()
        message = f'{{"type": "{_ENVELOPE_TYPE}", "data": {state_json}}}'

        loop = self._loop
        if loop is None:
            # Should not happen in production (the lifespan binds the loop before
            # starting the snapshot thread); we still keep the message
            # so the next client that connects is not left with nothing.
            self.latest = message
            return
        loop.call_soon_threadsafe(self._deliver, message)

    def _deliver(self, message: str) -> None:
        """Runs on the loop: stores `latest` and offers the message to each client."""
        self.latest = message
        with self._lock:
            queues = list(self._clients)
        for queue in queues:
            _offer(queue, message)

    def register(self, websocket: WebSocket) -> asyncio.Queue[str]:
        """Register a new client and return its dedicated queue (size 1)."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        with self._lock:
            self._clients[queue] = websocket
        return queue

    def unregister(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._clients.pop(queue, None)

    @property
    def client_count(self) -> int:
        """For `GET /healthz`."""
        with self._lock:
            return len(self._clients)

    async def close_all(self, code: int = 1001) -> None:
        """Close all open connections. Used in the lifespan shutdown."""
        with self._lock:
            websockets = list(self._clients.values())
        for websocket in websockets:
            try:
                await websocket.close(code=code)
            except Exception:
                logger.debug(
                    "could not close a websocket during shutdown", exc_info=True
                )
