"""Executable checks for hard timeout, in-flight cancellation and child crash."""

import os
import time
from pathlib import Path

from shared.messages import ErrorCode
from workers.ops.base import OpError
from workers.runner.isolation import run_isolated


def slow(task, src, out_dir, *, timeout_s, on_progress):
    while True:
        time.sleep(0.1)


def abrupt(task, src, out_dir, *, timeout_s, on_progress):
    os._exit(137)


def main():
    for code, timeout, cancel in (
        (ErrorCode.TIMEOUT, 0.3, False),
        (ErrorCode.CANCELLED, 10, True),
    ):
        started = time.monotonic()
        try:
            run_isolated(
                slow,
                None,
                Path("."),
                Path("."),
                timeout_s=timeout,
                on_progress=lambda _: None,
                cancelled=lambda cancel=cancel, started=started: (
                    cancel and time.monotonic() - started > 0.3
                ),
            )
        except OpError as exc:
            assert exc.code == code and time.monotonic() - started < 5
        else:
            raise AssertionError("Process was not stopped")
    try:
        run_isolated(
            abrupt,
            None,
            Path("."),
            Path("."),
            timeout_s=5,
            on_progress=lambda _: None,
            cancelled=lambda: False,
        )
    except OpError as exc:
        assert exc.code == ErrorCode.WORKER_LOST
    else:
        raise AssertionError("Child crash was accepted")
    print("Hard deadline and in-flight cancellation checks passed")


if __name__ == "__main__":
    main()
