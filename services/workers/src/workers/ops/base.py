"""Types shared by all operations.

An operation receives the `SubTaskMessage`, the already-downloaded input file
and an empty output directory, and returns the files to upload plus a metadata
dict that travels in `ResultMessage.output_meta`. It never touches AWS: the
runner downloads, uploads and reports.

Expected errors (corrupt file, requested format that does not exist, external
API down) are raised as `OpError` with their `ErrorCode`: the runner reports
them as `failed` and deletes the message. Any other exception is a bug or a
transient failure and is left for SQS to retry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from shared.messages import ErrorCode, SubTaskMessage


@dataclass(frozen=True)
class OpOutput:
    files: list[Path]
    meta: dict[str, Any] = field(default_factory=dict)


class OpError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[-2000:]  # ErrorInfo.message has max_length=2000


class Operation(Protocol):
    def __call__(
        self,
        task: SubTaskMessage,
        src: Path,
        out_dir: Path,
        *,
        timeout_s: float,
        on_progress: Callable[[int], None],
    ) -> OpOutput: ...


def output_name(task: SubTaskMessage, extension: str) -> str:
    """`uploads/u1/Mi clip.mov` + `mp4` -> `Mi clip.mp4`."""
    return f"{Path(task.input_key).stem}.{extension}"


def choose(params: dict[str, Any], key: str, default: str, allowed: set[str]) -> str:
    value = str(params.get(key, default)).lower().lstrip(".")
    if value not in allowed:
        raise OpError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"{key}={value!r} not supported, options: {sorted(allowed)}",
        )
    return value
