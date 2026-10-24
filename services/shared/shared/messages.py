"""Messages that travel through SQS.

- SubTaskMessage: coordinator -> work queues (and whatever lands in the DLQ).
- ResultMessage: worker -> results queue, one per state change.

Workers never write Cases or SubTasks: they only publish ResultMessage.
The coordinator is the only writer of that state.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .routing import MediaType, Operation, Pool, Priority
from .states import SubTaskStatus, state_order

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class ErrorCode(StrEnum):
    UNSUPPORTED_FORMAT = "unsupported_format"   # unsupported type or operation
    CORRUPT_INPUT = "corrupt_input"             # ffprobe cannot read the file
    PROCESSING_ERROR = "processing_error"       # ffmpeg finished with an error
    EXTERNAL_API_ERROR = "external_api_error"   # lyrics / catalog API
    TIMEOUT = "timeout"                         # exceeded the operation's maximum time
    RETRIES_EXHAUSTED = "retries_exhausted"     # arrived through the DLQ (worker down)
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str = Field(max_length=2000)


class _Message(BaseModel):
    # extra="ignore": an old consumer tolerates new fields.
    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: int = SCHEMA_VERSION

    def to_body(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_body(cls, body: str):
        return cls.model_validate_json(body)


class SubTaskMessage(_Message):
    """Message body in video-alta, audio-normal, etc."""

    subtask_id: str
    case_id: str
    input_key: str                 # key in the dataset bucket
    media_type: MediaType
    operation: Operation
    priority: Priority
    output_prefix: str             # results/{case_id}/{subtask_id}/
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"target_format": "mp4"}
    enqueued_at: datetime = Field(default_factory=utcnow)


class ResultMessage(_Message):
    """Message body in the results queue."""

    subtask_id: str
    case_id: str
    status: SubTaskStatus
    attempt: int = Field(ge=1)     # ApproximateReceiveCount of the work message
    worker_id: str | None = None   # None only when it comes from the DLQ
    pool: Pool | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_meta: dict[str, Any] = Field(default_factory=dict)  # duration, codec, tags…
    error: ErrorInfo | None = None
    emitted_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.status is SubTaskStatus.FAILED and self.error is None:
            raise ValueError("un resultado failed necesita error")
        if self.status is SubTaskStatus.COMPLETED and self.finished_at is None:
            raise ValueError("un resultado completed necesita finished_at")
        if self.status is SubTaskStatus.PENDING:
            raise ValueError("workers do not report pending")
        return self

    @property
    def order(self) -> int:
        return state_order(self.status, self.attempt)


def result_from_dlq(msg: SubTaskMessage, receive_count: int) -> ResultMessage:
    """What the coordinator records when consuming the DLQ."""
    return ResultMessage(
        subtask_id=msg.subtask_id,
        case_id=msg.case_id,
        status=SubTaskStatus.FAILED,
        attempt=max(receive_count, 1),
        finished_at=utcnow(),
        error=ErrorInfo(
            code=ErrorCode.RETRIES_EXHAUSTED,
            message=f"exhausted {receive_count} attempts (worker down or process aborted)",
        ),
    )