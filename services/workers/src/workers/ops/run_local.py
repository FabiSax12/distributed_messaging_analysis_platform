"""Run one analysis operation on a local file, without broker, database or S3.

    uv run --package workers python -m workers.ops.run_local <operation> <file> \
        [--out DIR] [--timeout S] [--param KEY=VALUE ...]

It builds a minimal ``SubTaskMessage``, calls the op registered in
``ANALYSIS_OPERATIONS`` through the ``Operation`` protocol, validates its
``meta`` with ``AnalysisResult.from_meta`` and prints one JSON document on
stdout: the result plus wall time and peak RSS (for M1). Progress and errors
go to stderr.

Exit codes: 0 ok, 1 the op failed (``OpError``) or broke the contract, 2 usage
error (unknown operation, missing file).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shared.analysis import (
    ANALYSIS_META_KEY,
    OPERATION_CONTENT_TYPE,
    AnalysisOperation,
    AnalysisResult,
    CasePriority,
)
from shared.messages import SubTaskMessage
from shared.models import result_prefix
from shared.routing import detect_content_type

from .analysis import ANALYSIS_OPERATIONS
from .base import OpError

DEFAULT_TIMEOUT_S = 300.0
LOCAL_CASE_ID = "local"


class UnknownOperation(LookupError):
    """The operation name is not in ``ANALYSIS_OPERATIONS``."""


class ContractViolation(ValueError):
    """The op returned ``meta`` that is not a valid ``AnalysisResult`` for it."""


@dataclass(frozen=True)
class LocalRunReport:
    operation: AnalysisOperation
    src: Path
    out_dir: Path
    files: list[Path]
    result: AnalysisResult
    wall_time_s: float
    peak_rss_bytes: int  # this process, whole lifetime (includes interpreter + imports)
    peak_rss_children_bytes: int  # largest finished child process (e.g. ffmpeg)
    progress: list[int]

    def to_json(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "file": str(self.src),
            "out_dir": str(self.out_dir),
            "files": [str(path) for path in self.files],
            "wall_time_s": round(self.wall_time_s, 6),
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_rss_children_bytes": self.peak_rss_children_bytes,
            "result": self.result.to_meta()[ANALYSIS_META_KEY],
        }


def build_task(
    operation: AnalysisOperation, src: Path, params: Mapping[str, Any] | None = None
) -> SubTaskMessage:
    """A validated ``SubTaskMessage`` like the one the coordinator publishes;
    ``input_key`` is the local path. ``media_type`` comes from the file
    extension (a video run through ``audio_analyze`` keeps ``video``)."""
    subtask_id = f"{LOCAL_CASE_ID}-{uuid.uuid4().hex[:12]}"
    return SubTaskMessage(
        subtask_id=subtask_id,
        case_id=LOCAL_CASE_ID,
        input_key=str(src),
        media_type=detect_content_type(src.name) or OPERATION_CONTENT_TYPE[operation],
        operation=operation,
        priority=CasePriority.MEDIUM,
        output_prefix=result_prefix(LOCAL_CASE_ID, subtask_id),
        params=dict(params or {}),
    )


def resolve(name: str) -> tuple[AnalysisOperation, Any]:
    registered = sorted(op.value for op in ANALYSIS_OPERATIONS)
    if not registered:
        raise UnknownOperation(
            f"{name!r}: no analysis operations registered "
            "(add them in workers/ops/analysis/__init__.py)"
        )
    try:
        operation = AnalysisOperation(name)
        return operation, ANALYSIS_OPERATIONS[operation]
    except (ValueError, KeyError):
        raise UnknownOperation(
            f"unknown operation {name!r}; registered: {', '.join(registered)}"
        ) from None


def run(
    operation_name: str,
    src: Path,
    *,
    out_dir: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    params: Mapping[str, Any] | None = None,
) -> LocalRunReport:
    """Run the op and validate its result. Raises ``UnknownOperation``,
    ``FileNotFoundError``, ``OpError`` (from the op) or ``ContractViolation``."""
    operation, op = resolve(operation_name)
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"input file not found: {src}")
    out_dir = (
        Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix=f"{operation}-"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    progress: list[int] = []

    def on_progress(pct: int) -> None:
        progress.append(pct)
        print(f"progress: {pct}%", file=sys.stderr)

    task = build_task(operation, src, params)
    started = time.perf_counter()
    output = op(task, src, out_dir, timeout_s=timeout_s, on_progress=on_progress)
    wall_time_s = time.perf_counter() - started

    try:
        result = AnalysisResult.from_meta(output.meta)
    except ValidationError as exc:
        raise ContractViolation(f"meta is not a valid AnalysisResult:\n{exc}") from exc
    except ValueError as exc:
        raise ContractViolation(f"meta is not a valid AnalysisResult: {exc}") from exc
    if result.operation is not operation:
        raise ContractViolation(
            f"ran {operation} but the result says operation={result.operation}"
        )

    return LocalRunReport(
        operation=operation,
        src=src,
        out_dir=out_dir,
        files=list(output.files),
        result=result,
        wall_time_s=wall_time_s,
        peak_rss_bytes=_peak_rss_bytes(False),
        peak_rss_children_bytes=_peak_rss_bytes(True),
        progress=progress,
    )


def _peak_rss_bytes(children: bool) -> int:
    if sys.platform == "win32":
        import psutil

        return 0 if children else psutil.Process().memory_info().peak_wset
    import resource

    maxrss = resource.getrusage(
        resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    ).ru_maxrss
    return maxrss if sys.platform == "darwin" else maxrss * 1024


def _parse_param(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {raw!r}")
    return key, value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.ops.run_local",
        description="Run one analysis operation on a local file and print its result as JSON.",
    )
    parser.add_argument(
        "operation", help="e.g. text_analyze, image_analyze, audio_analyze"
    )
    parser.add_argument("file", type=Path, help="input file")
    parser.add_argument(
        "--out", type=Path, help="output directory (default: a new temp dir)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="timeout_s given to the op",
    )
    parser.add_argument(
        "--param",
        type=_parse_param,
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="SubTaskMessage.params entry (repeatable)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = run(
            args.operation,
            args.file,
            out_dir=args.out,
            timeout_s=args.timeout,
            params=dict(args.param),
        )
    except (UnknownOperation, FileNotFoundError) as exc:
        parser.error(str(exc))  # exits with code 2
    except OpError as exc:
        print(f"operation failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except ContractViolation as exc:
        print(f"contract violation: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
