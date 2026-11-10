"""One killable process per operation; the parent retains broker/DB ownership."""

import multiprocessing
import time

import psutil

from shared.messages import ErrorCode

from ..ops.base import OpError


def _child(pipe, op, task, src, out_dir, timeout_s):
    try:
        result = op(
            task,
            src,
            out_dir,
            timeout_s=timeout_s,
            on_progress=lambda value: pipe.send(("progress", value)),
        )
        pipe.send(("output", result))
    except OpError as exc:
        pipe.send(("error", (exc.code.value, exc.message)))
    except Exception as exc:  # noqa: BLE001 -- transport all child failures to the supervisor
        pipe.send(("error", (ErrorCode.INTERNAL.value, repr(exc))))
    finally:
        pipe.close()


def _stop(process):
    if process.is_alive():
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
    process.join()


def run_isolated(op, task, src, out_dir, *, timeout_s, on_progress, cancelled):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(sender, op, task, src, out_dir, timeout_s)
    )
    started = time.monotonic()
    process.start()
    sender.close()
    try:
        while True:
            if cancelled():
                raise OpError(ErrorCode.CANCELLED, "Case cancelled during analysis")
            if time.monotonic() - started >= timeout_s:
                raise OpError(ErrorCode.TIMEOUT, "Analysis process exceeded deadline")
            if receiver.poll(0.25):
                try:
                    kind, value = receiver.recv()
                except EOFError:
                    break
                if kind == "progress":
                    on_progress(value)
                elif kind == "error":
                    raise OpError(ErrorCode(value[0]), value[1])
                else:
                    return value
            elif not process.is_alive():
                break
        raise OpError(
            ErrorCode.WORKER_LOST,
            f"Analysis child exited without result ({process.exitcode}); possible OOM",
        )
    finally:
        _stop(process)
        receiver.close()
