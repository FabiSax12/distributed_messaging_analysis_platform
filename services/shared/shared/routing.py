"""Routing by type: file -> content type -> operation -> pool -> queue.

This is the table worth showing in the architecture document.

| Extension                                   | Content type | Operation       | Pool  |
|---------------------------------------------|--------------|-----------------|-------|
| .txt (WhatsApp export), .json (Telegram)    | text         | text_analyze    | text  |
| .jpg .jpeg .png .webp .gif .bmp             | image        | image_analyze   | image |
| .mp3 .wav .ogg .opus .m4a .aac .flac        | audio        | audio_analyze   | audio |
| .mp4 .mkv .mov .webm .avi                   | video        | audio_analyze   | audio |

Video has no operation of its own: the audio worker extracts the audio track
with ffmpeg and analyzes it like a voice note.

Files with an unknown extension or an operation that is not valid for their
type: the coordinator creates the sub-task already in `failed` (without
publishing it) with UNSUPPORTED_FORMAT, and starts the case with
pending_count = N - prefailed and failed_count = prefailed. This way they
appear in the report ("2 failed due to unsupported format") without using up a
worker.

Queue topology (RabbitMQ, declared by `shared.broker.declare_topology`): one
direct exchange, one durable work queue per pool with `x-max-priority`, one
results queue and a dead-letter exchange + queue.
"""

from enum import StrEnum
from pathlib import PurePosixPath

from .analysis import AnalysisOperation, CasePriority, ContentType


class Pool(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"  # also video: the audio track is extracted with ffmpeg


EXTENSIONS: dict[str, ContentType] = {
    ".txt": ContentType.TEXT,  # WhatsApp chat export
    ".json": ContentType.TEXT,  # Telegram chat export (result.json)
    ".jpg": ContentType.IMAGE,
    ".jpeg": ContentType.IMAGE,
    ".png": ContentType.IMAGE,
    ".webp": ContentType.IMAGE,
    ".gif": ContentType.IMAGE,
    ".bmp": ContentType.IMAGE,
    ".mp3": ContentType.AUDIO,
    ".wav": ContentType.AUDIO,
    ".ogg": ContentType.AUDIO,
    ".opus": ContentType.AUDIO,  # WhatsApp voice notes
    ".m4a": ContentType.AUDIO,
    ".aac": ContentType.AUDIO,
    ".flac": ContentType.AUDIO,
    ".mp4": ContentType.VIDEO,
    ".mkv": ContentType.VIDEO,
    ".mov": ContentType.VIDEO,
    ".webm": ContentType.VIDEO,
    ".avi": ContentType.VIDEO,
}

# Valid operations per content type. Each type has exactly one today; the
# table stays a set so a type can gain a second operation without touching
# the planner.
ALLOWED_OPERATIONS: dict[ContentType, frozenset[AnalysisOperation]] = {
    ContentType.TEXT: frozenset({AnalysisOperation.TEXT_ANALYZE}),
    ContentType.IMAGE: frozenset({AnalysisOperation.IMAGE_ANALYZE}),
    ContentType.AUDIO: frozenset({AnalysisOperation.AUDIO_ANALYZE}),
    ContentType.VIDEO: frozenset({AnalysisOperation.AUDIO_ANALYZE}),
}

# Operations the coordinator picks when the case does not specify them.
DEFAULT_OPERATIONS: dict[ContentType, tuple[AnalysisOperation, ...]] = {
    ContentType.TEXT: (AnalysisOperation.TEXT_ANALYZE,),
    ContentType.IMAGE: (AnalysisOperation.IMAGE_ANALYZE,),
    ContentType.AUDIO: (AnalysisOperation.AUDIO_ANALYZE,),
    ContentType.VIDEO: (AnalysisOperation.AUDIO_ANALYZE,),
}

# Pool that owns each operation (heterogeneous compute, M5).
OPERATION_POOL: dict[AnalysisOperation, Pool] = {
    AnalysisOperation.TEXT_ANALYZE: Pool.TEXT,
    AnalysisOperation.IMAGE_ANALYZE: Pool.IMAGE,
    AnalysisOperation.AUDIO_ANALYZE: Pool.AUDIO,
}

# ---- Broker names ----------------------------------------------------------- #

WORK_EXCHANGE = "work"  # direct; routing key = pool for work, RESULTS_QUEUE for results
RESULTS_QUEUE = "results"
DEAD_LETTER_EXCHANGE = "dead-letter"  # fanout
DLQ = "dead-letter"

# AMQP message priority per case priority. The work queues are declared with
# `x-max-priority = MAX_MESSAGE_PRIORITY`; RabbitMQ delivers higher numbers
# first within a queue. M2 replaces this static mapping with its aging policy
# (see `message_priority`).
MESSAGE_PRIORITY: dict[CasePriority, int] = {
    CasePriority.LOW: 1,
    CasePriority.MEDIUM: 2,
    CasePriority.HIGH: 3,
    CasePriority.CRITICAL: 4,
}
MAX_MESSAGE_PRIORITY = max(MESSAGE_PRIORITY.values())


def work_queue(pool: Pool) -> str:
    """Queue name of a pool, e.g. 'work.text'. Its routing key is the pool value."""
    return f"{WORK_EXCHANGE}.{pool.value}"


def message_priority(priority: CasePriority) -> int:
    """Priority stamped on a work message. Seam for the M2 scheduler."""
    return MESSAGE_PRIORITY[priority]


ALL_WORK_QUEUES: tuple[str, ...] = tuple(work_queue(pool) for pool in Pool)


class UnsupportedFile(ValueError):
    """Unknown extension or operation not valid for the file type."""


def detect_content_type(filename: str) -> ContentType | None:
    return EXTENSIONS.get(PurePosixPath(filename).suffix.lower())


def plan_operations(
    filename: str, requested: list[AnalysisOperation] | None = None
) -> list[AnalysisOperation]:
    """Operations that will run on a file.

    Raises UnsupportedFile if the type is not recognized or if any requested
    operation does not apply to that type. The worker still validates the
    content: a corrupt .mp3 passes this filter and fails in the worker.
    """
    content_type = detect_content_type(filename)
    if content_type is None:
        raise UnsupportedFile(f"unsupported extension: {filename}")
    if not requested:
        return list(DEFAULT_OPERATIONS[content_type])
    invalid = [op for op in requested if op not in ALLOWED_OPERATIONS[content_type]]
    if invalid:
        raise UnsupportedFile(f"invalid operations for {content_type}: {invalid}")
    return list(dict.fromkeys(requested))  # no duplicates, in order
