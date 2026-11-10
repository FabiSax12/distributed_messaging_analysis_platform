"""Messages that travel through RabbitMQ (JSON bodies).

- SubTaskMessage: coordinator -> pool work queue (and whatever is dead-lettered).
- ResultMessage: worker -> results queue, one per state change.

Workers never write `cases` or `subtasks`: they only publish ResultMessage.
The coordinator is the only writer of that state (and decides retries).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis import AnalysisOperation, CasePriority, ContentType
from .routing import Pool
from .states import SubTaskStatus, state_order

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class ErrorCode(StrEnum):
    UNSUPPORTED_FORMAT = "unsupported_format"   # unsupported type or operation
    CORRUPT_INPUT = "corrupt_input"             # unreadable file / not a chat export
    PROCESSING_ERROR = "processing_error"       # model or ffmpeg failed
    EXTERNAL_API_ERROR = "external_api_error"   # an external API failed (transient)
    TIMEOUT = "timeout"                         # exceeded the operation's maximum time
    RETRIES_EXHAUSTED = "retries_exhausted"     # transient failures beyond the retry max
    WORKER_LOST = "worker_lost"                 # worker crashed or stopped heartbeating
    DEAD_LETTERED = "dead_lettered"             # the broker dead-lettered the message
    CANCELLED = "cancelled"
    INTERNAL = "internal"                       # unexpected worker exception (transient)


# Codes the coordinator retries with backoff (up to its configured maximum).
# The rest are final: retrying a corrupt file or a timeout gives the same result.
RETRYABLE_ERRORS: frozenset[ErrorCode] = frozenset(
    {ErrorCode.INTERNAL, ErrorCode.EXTERNAL_API_ERROR, ErrorCode.WORKER_LOST}
)


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
    """Message body in work.text, work.image and work.audio."""

    subtask_id: str
    case_id: str
    attempt: int = Field(default=1, ge=1)  # 1 on the first dispatch, +1 per retry
    input_key: str                 # key in the S3 bucket
    media_type: ContentType        # name kept from Project I for API stability
    operation: AnalysisOperation
    priority: CasePriority
    output_prefix: str             # results/{case_id}/{subtask_id}/
    params: dict[str, Any] = Field(default_factory=dict)  # e.g. {"lang": "es"}
    enqueued_at: datetime = Field(default_factory=utcnow)


class ResultMessage(_Message):
    """Message body in the results queue."""

    subtask_id: str
    case_id: str
    status: SubTaskStatus
    attempt: int = Field(ge=1)     # SubTaskMessage.attempt
    worker_id: str | None = None   # None only when the coordinator synthesizes it
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
            raise ValueError("a failed result needs an error")
        if self.status is SubTaskStatus.COMPLETED and self.finished_at is None:
            raise ValueError("a completed result needs finished_at")
        if self.status in (SubTaskStatus.PENDING, SubTaskStatus.RETRYING):
            # retrying is decided and written by the coordinator, never reported.
            raise ValueError(f"workers do not report {self.status}")
        return self

    @property
    def order(self) -> int:
        return state_order(self.status, self.attempt)


def result_from_dead_letter(msg: SubTaskMessage) -> ResultMessage:
    """What the coordinator records when a work message is dead-lettered
    (a worker rejected it as unprocessable)."""
    return ResultMessage(
        subtask_id=msg.subtask_id,
        case_id=msg.case_id,
        status=SubTaskStatus.FAILED,
        attempt=msg.attempt,
        finished_at=utcnow(),
        error=ErrorInfo(
            code=ErrorCode.DEAD_LETTERED,
            message="the work message was dead-lettered by the broker",
        ),
    )
