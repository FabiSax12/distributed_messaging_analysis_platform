"""Condition-based FIFO: close wakes all waiters and preserves queued items."""

from collections import deque
from threading import Condition
from time import monotonic

from . import OverflowPolicy, QueueClosed, QueueFull


class ThreadBoundedQueue[T]:
    def __init__(self, maxsize: int, policy: OverflowPolicy = OverflowPolicy.BLOCK):
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self.policy = OverflowPolicy(policy)
        self._items: deque[T] = deque()
        self._condition = Condition()
        self._closed = False
        self._rejected = 0

    @staticmethod
    def _deadline(timeout: float | None) -> float | None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be nonnegative")
        return None if timeout is None else monotonic() + timeout

    def put(self, item: T, timeout: float | None = None) -> None:
        deadline = self._deadline(timeout)
        with self._condition:
            while True:
                if self._closed:
                    raise QueueClosed()
                if len(self._items) < self.maxsize:
                    self._items.append(item)
                    self._condition.notify_all()
                    return
                remaining = None if deadline is None else deadline - monotonic()
                if self.policy == OverflowPolicy.REJECT or (
                    remaining is not None and remaining <= 0
                ):
                    self._rejected += 1
                    raise QueueFull()
                self._condition.wait(remaining)

    def get(self, timeout: float | None = None) -> T:
        deadline = self._deadline(timeout)
        with self._condition:
            while not self._items:
                if self._closed:
                    raise QueueClosed()
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError()
                self._condition.wait(remaining)
            item = self._items.popleft()
            self._condition.notify_all()
            return item

    def depth(self) -> int:
        with self._condition:
            return len(self._items)

    def rejected(self) -> int:
        with self._condition:
            return self._rejected

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
