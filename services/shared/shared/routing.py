"""Routing by type: file -> media type -> operation(s) -> pool -> queue.

This is the table worth showing in the architecture document.

A file can produce several sub-tasks (a video: conversion + audio +
thumbnail), so the barrier's N is the number of sub-tasks, not of files.

Files with an unknown extension or an operation that is not valid for their
type: the coordinator creates the sub-task already in `failed` (without
enqueueing it) with UNSUPPORTED_FORMAT, and starts the case with
pending_count = N - prefailed and failed_count = prefailed. This way they
appear in the report ("2 failed due to unsupported format") without using up a
worker.
"""

from enum import StrEnum
from pathlib import PurePosixPath


class MediaType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class Operation(StrEnum):
    VIDEO_CONVERT = "video_convert"      # video format conversion
    AUDIO_EXTRACT = "audio_extract"      # audio extraction from video
    VIDEO_THUMBNAIL = "video_thumbnail"  # cover / thumbnail from a frame
    AUDIO_CONVERT = "audio_convert"      # audio transcoding
    IMAGE_THUMBNAIL = "image_thumbnail"  # image thumbnail
    METADATA = "metadata"                # ffprobe + external catalogs
    LYRICS = "lyrics"                    # lyrics from an external API
    CLASSIFY = "classify"                # classification / organization of the result


class Pool(StrEnum):
    VIDEO = "video"          # c7i-flex.large · ffmpeg, compute-intensive
    AUDIO = "audio"          # m7i-flex.large · light ffmpeg, images
    METADATA = "metadatos"   # t3.micro · network-bound, not CPU-bound


class Priority(StrEnum):
    HIGH = "alta"
    NORMAL = "normal"


EXTENSIONS: dict[str, MediaType] = {
    ".mp4": MediaType.VIDEO,
    ".mkv": MediaType.VIDEO,
    ".mov": MediaType.VIDEO,
    ".avi": MediaType.VIDEO,
    ".webm": MediaType.VIDEO,
    ".mp3": MediaType.AUDIO,
    ".wav": MediaType.AUDIO,
    ".flac": MediaType.AUDIO,
    ".ogg": MediaType.AUDIO,
    ".m4a": MediaType.AUDIO,
    ".aac": MediaType.AUDIO,
    ".jpg": MediaType.IMAGE,
    ".jpeg": MediaType.IMAGE,
    ".png": MediaType.IMAGE,
    ".webp": MediaType.IMAGE,
}

# Valid operations per media type.
ALLOWED_OPERATIONS: dict[MediaType, frozenset[Operation]] = {
    MediaType.VIDEO: frozenset(
        {
            Operation.VIDEO_CONVERT,
            Operation.AUDIO_EXTRACT,
            Operation.VIDEO_THUMBNAIL,
            Operation.METADATA,
            Operation.CLASSIFY,
        }
    ),
    MediaType.AUDIO: frozenset(
        {Operation.AUDIO_CONVERT, Operation.METADATA, Operation.LYRICS, Operation.CLASSIFY}
    ),
    MediaType.IMAGE: frozenset(
        {Operation.IMAGE_THUMBNAIL, Operation.METADATA, Operation.CLASSIFY}
    ),
}

# Operations the coordinator picks when the case does not specify them.
DEFAULT_OPERATIONS: dict[MediaType, tuple[Operation, ...]] = {
    MediaType.VIDEO: (Operation.VIDEO_CONVERT, Operation.AUDIO_EXTRACT, Operation.VIDEO_THUMBNAIL),
    MediaType.AUDIO: (Operation.AUDIO_CONVERT, Operation.METADATA),
    MediaType.IMAGE: (Operation.IMAGE_THUMBNAIL,),
}

# Pool that owns each operation (compute heterogeneity, Unit 1).
OPERATION_POOL: dict[Operation, Pool] = {
    Operation.VIDEO_CONVERT: Pool.VIDEO,
    Operation.AUDIO_EXTRACT: Pool.VIDEO,
    Operation.VIDEO_THUMBNAIL: Pool.VIDEO,
    Operation.AUDIO_CONVERT: Pool.AUDIO,
    Operation.IMAGE_THUMBNAIL: Pool.AUDIO,
    Operation.METADATA: Pool.METADATA,
    Operation.LYRICS: Pool.METADATA,
    Operation.CLASSIFY: Pool.METADATA,
}


def queue_key(pool: Pool, priority: Priority) -> str:
    """Logical queue name, e.g. 'video-alta'. The real URL comes from Terraform."""
    return f"{pool.value}-{priority.value}"


ALL_WORK_QUEUES: tuple[str, ...] = tuple(queue_key(p, pr) for p in Pool for pr in Priority)
RESULTS_QUEUE = "resultados"
DLQ = "dlq"

# Consumption order of each pool: strict priority and, if its queues are
# empty, help with another type its machine can handle (Diagram 4).
POLL_ORDER: dict[Pool, tuple[str, ...]] = {
    Pool.VIDEO: ("video-alta", "video-normal", "audio-normal"),
    Pool.AUDIO: (
        "audio-alta",
        "audio-normal",
        "metadatos-alta",
        "metadatos-normal",
        "video-normal",
    ),
    Pool.METADATA: ("metadatos-alta", "metadatos-normal", "audio-normal"),
}


class UnsupportedFile(ValueError):
    """Unknown extension or operation not valid for the file type."""


def detect_media_type(filename: str) -> MediaType | None:
    return EXTENSIONS.get(PurePosixPath(filename).suffix.lower())


def plan_operations(filename: str, requested: list[Operation] | None = None) -> list[Operation]:
    """Operations that will run on a file.

    Raises UnsupportedFile if the type is not recognized or if any requested
    operation does not apply to that type. The worker still validates with
    ffprobe: a corrupt .mp4 passes this filter and fails in the worker.
    """
    media = detect_media_type(filename)
    if media is None:
        raise UnsupportedFile(f"unsupported extension: {filename}")
    if not requested:
        return list(DEFAULT_OPERATIONS[media])
    invalid = [op for op in requested if op not in ALLOWED_OPERATIONS[media]]
    if invalid:
        raise UnsupportedFile(f"invalid operations for {media}: {invalid}")
    return list(dict.fromkeys(requested))  # no duplicates, in order