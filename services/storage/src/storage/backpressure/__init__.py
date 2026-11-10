"""Bounded queue with backpressure (interface only).

A producer that is faster than its consumers must not grow memory without
limit. ``BoundedQueue`` holds at most ``maxsize`` items and, when full,
applies its ``OverflowPolicy``: ``BLOCK`` makes the producer wait (pushes the
pressure upstream), ``REJECT`` fails fast so the caller can answer
"try later" (e.g. HTTP 429/503). M3 compares queue depth and response time
during a burst with and without this limit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class OverflowPolicy(StrEnum):
    BLOCK = "block"
    REJECT = "reject"


class QueueFull(Exception):
    """Raised by ``put`` under ``REJECT`` (or ``BLOCK`` after its timeout)."""


class QueueClosed(Exception):
    """Raised by ``put`` after ``close``, and by ``get`` once closed and drained."""


class BoundedQueue[T](Protocol):
    maxsize: int
    policy: OverflowPolicy

    def put(self, item: T, timeout: float | None = None) -> None:
        """Enqueue ``item``. When full: ``BLOCK`` waits up to ``timeout``
        seconds (``None`` = forever) and then raises ``QueueFull``; ``REJECT``
        raises ``QueueFull`` immediately and counts a rejection."""
        ...

    def get(self, timeout: float | None = None) -> T:
        """Dequeue the oldest item, waiting up to ``timeout`` seconds.
        Raises ``TimeoutError`` if nothing arrives in time."""
        ...

    def depth(self) -> int:
        """Items currently queued (sampled over time for the burst evidence)."""
        ...

    def rejected(self) -> int:
        """How many ``put`` calls raised ``QueueFull`` so far."""
        ...

    def close(self) -> None:
        """Stop accepting items; consumers drain what is left."""
        ...
