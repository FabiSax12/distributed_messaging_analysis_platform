"""Analysis output and deadline helpers shared by the three pools."""

import hashlib
import json
import time

from shared.messages import ErrorCode

from ..base import OpError, OpOutput


def deadline(timeout_s):
    if timeout_s <= 0:
        raise OpError(ErrorCode.TIMEOUT, "timeout_s must be positive")
    return time.monotonic() + timeout_s


def remaining(end):
    seconds = end - time.monotonic()
    if seconds <= 0:
        raise OpError(ErrorCode.TIMEOUT, "Analysis deadline exceeded")
    return seconds


def digest(src):
    with src.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def output(result, out_dir, on_progress):
    path = out_dir / "analysis.json"
    path.write_text(
        json.dumps(result.to_meta()["analysis"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    on_progress(100)
    return OpOutput(files=[path], meta=result.to_meta())
