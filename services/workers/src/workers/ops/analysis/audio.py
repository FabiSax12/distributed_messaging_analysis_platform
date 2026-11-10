"""Extract audio from any supported media, transcribe, then apply text rules."""

import os
import threading
import time
from functools import lru_cache

from shared.analysis import (
    AnalysisOperation,
    AnalysisResult,
    Evidence,
    EvidenceKind,
    ModelInfo,
    Transcript,
    TranscriptSegment,
)
from shared.messages import ErrorCode

from .. import ffmpeg
from ..base import OpError
from .common import deadline, digest, output, remaining
from .text import findings_for, summaries

LOCK = threading.Lock()


@lru_cache(maxsize=1)
def load_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        os.environ.get("AUDIO_MODEL", "tiny"),
        device=os.environ.get("MODEL_DEVICE", "cpu"),
        compute_type=os.environ.get("AUDIO_COMPUTE_TYPE", "int8"),
        cpu_threads=int(os.environ.get("MODEL_THREADS", "2")),
    )


def audio_analyze(task, src, out_dir, *, timeout_s, on_progress):
    end = deadline(timeout_s)
    wav = out_dir / "extracted.wav"
    started = time.perf_counter()
    try:
        info = ffmpeg.probe(src, remaining(end))
        if not ffmpeg.has_stream(info, "audio"):
            raise OpError(ErrorCode.CORRUPT_INPUT, "No audio stream")
        ffmpeg.run(
            [
                "-i",
                str(src),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav),
            ],
            timeout_s=remaining(end),
        )
        extract_s = time.perf_counter() - started
        if not LOCK.acquire(timeout=remaining(end)):
            raise OpError(ErrorCode.TIMEOUT, "Waiting for audio model")
        try:
            started = time.perf_counter()
            model = load_model()
            load_s = time.perf_counter() - started
            remaining(end)
            started = time.perf_counter()
            segments, info = model.transcribe(
                str(wav), language=task.params.get("lang"), beam_size=1
            )
            transcript_segments = []
            for seg in segments:
                remaining(end)
                transcript_segments.append(
                    TranscriptSegment(start_s=seg.start, end_s=seg.end, text=seg.text)
                )
            infer_s = time.perf_counter() - started
        finally:
            LOCK.release()
    except FileNotFoundError as exc:
        raise OpError(
            ErrorCode.PROCESSING_ERROR, "Install ffmpeg and ffprobe on PATH"
        ) from exc
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise OpError(ErrorCode.PROCESSING_ERROR, f"Whisper failed: {exc}") from exc
    finally:
        wav.unlink(missing_ok=True)
    source_hash = digest(src)
    findings = []
    for seg in transcript_segments:
        findings.extend(
            findings_for(
                seg.text,
                Evidence(
                    source_key=task.input_key,
                    kind=EvidenceKind.AUDIO_SEGMENT,
                    audio_start_s=seg.start_s,
                    audio_end_s=seg.end_s,
                    excerpt=seg.text,
                    sha256=source_hash,
                ),
            )
        )
    text = " ".join(seg.text.strip() for seg in transcript_segments)
    keywords, topics, sentiment = summaries(text)
    return output(
        AnalysisResult(
            operation=AnalysisOperation.AUDIO_ANALYZE,
            findings=findings,
            keywords=keywords,
            topics=topics,
            sentiment=sentiment,
            transcript=Transcript(
                text=text, language=info.language, segments=transcript_segments
            ),
            model=ModelInfo(
                name=f"faster-whisper-{os.environ.get('AUDIO_MODEL', 'tiny')}-{os.environ.get('AUDIO_COMPUTE_TYPE', 'int8')}"
            ),
            timings_s={"extract": extract_s, "load_model": load_s, "infer": infer_s},
        ),
        out_dir,
        on_progress,
    )
