"""ffprobe and ffmpeg as subprocesses.

The heavy work runs in a separate process, so the worker threads do not
compete for the GIL: each thread just waits for its ffmpeg. The binaries must be
available in PATH (planned: installed in the worker Docker image).
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.messages import ErrorCode

from .base import OpError

_PROBE_TIMEOUT_S = 60
# Audio output format -> codec arguments, for the future audio ops (inherited
# from Project I, where audio_extract and audio_convert used them).
# (video pool) and audio_convert (audio pool).
AUDIO_CODECS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


def probe(src: Path, timeout_s: float = _PROBE_TIMEOUT_S) -> dict[str, Any]:
    """ffprobe as JSON. If it cannot read the file, the file is corrupt: the
    coordinator only filtered by extension."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(src),
            ],
            capture_output=True,
            text=True,
            timeout=min(timeout_s, _PROBE_TIMEOUT_S),
            check=False,  # the exit code is checked below
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise OpError(ErrorCode.TIMEOUT, "ffprobe exceeded the maximum time") from exc
    info = json.loads(proc.stdout or "{}")
    if proc.returncode != 0 or not info.get("streams"):
        raise OpError(
            ErrorCode.CORRUPT_INPUT,
            f"ffprobe could not read the file: {proc.stderr.strip() or 'no streams'}",
        )
    return info


def duration_s(info: dict[str, Any]) -> float | None:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def ms(seconds: float | None) -> int | None:
    """Seconds -> integer ms for `output_meta` (DynamoDB does not accept float)."""
    return None if seconds is None else round(seconds * 1000)


def has_stream(info: dict[str, Any], codec_type: str) -> bool:
    return any(s.get("codec_type") == codec_type for s in info["streams"])


def run(
    args: list[str],
    *,
    timeout_s: float,
    duration: float | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    """Run ffmpeg and translate `-progress` into a percentage (0-99).

    The 100 comes from the `completed` result, not from ffmpeg. If `timeout_s`
    elapses, a Timer kills the process and `OpError(TIMEOUT)` is raised.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats", *args,
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_s, _kill)
    timer.start()
    last = -1
    try:
        # With -loglevel error, stderr is small and does not fill the pipe while
        # stdout is read line by line.
        for line in proc.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            if key != "out_time_us" or not duration or on_progress is None:
                continue
            if value.isdigit():
                pct = min(99, int(int(value) / 1_000_000 / duration * 100))
                if pct > last:
                    last = pct
                    on_progress(pct)
        stderr = proc.stderr.read()  # type: ignore[union-attr]
        returncode = proc.wait()
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise OpError(ErrorCode.TIMEOUT, f"ffmpeg exceeded {timeout_s:.0f} s")
    if returncode != 0:
        raise OpError(
            ErrorCode.PROCESSING_ERROR,
            f"ffmpeg exited with code {returncode}: {stderr.strip()}",
        )
