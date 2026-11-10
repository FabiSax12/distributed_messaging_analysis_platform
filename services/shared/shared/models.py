"""PostgreSQL rows, consolidated report, load samples and S3 keys.

Tables (see `services/coordinator/src/coordinator/db/schema.sql`):
- cases     PK case_id
- subtasks  PK subtask_id, index on case_id
- workers   PK worker_id (each worker upserts its own heartbeat row)

Each model maps one table row: column names equal field names, so a
`dict_row` from psycopg validates directly with `Model.model_validate(row)`.
Columns that only the coordinator uses internally (`closed`) are not in the
models; `extra="ignore"` drops them.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analysis import (
    AnalysisOperation,
    AnalysisResult,
    CasePriority,
    ContentType,
    Finding,
)
from .messages import ErrorInfo, SubTaskMessage, utcnow
from .routing import Pool, work_queue
from .states import CaseStatus, SubTaskStatus

DEFAULT_HEARTBEAT_INTERVAL_S = 5
DEFAULT_HEARTBEAT_TIMEOUT_S = (
    60  # 12 missed beats: tolerates GC pauses and short DB blips
)


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")  # ignores internal and future columns


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


class CaseSource(StrEnum):
    MANUAL = "manual"  # submitted from the dashboard or the API
    AUTO = "auto"  # grouped by the generator (folder / metadata JSON)


class CaseItem(_Item):
    case_id: str
    label: str | None = None
    source: CaseSource = CaseSource.MANUAL
    priority: CasePriority = CasePriority.MEDIUM
    status: CaseStatus = CaseStatus.QUEUED

    file_count: int = Field(ge=1)
    total: int = Field(ge=1)  # sub-tasks, not files
    pending_count: int = Field(ge=0)  # barrier counter
    failed_count: int = 0
    cancelled_count: int = 0
    cancel_requested: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report_key: str | None = None


# --------------------------------------------------------------------------- #
# SubTasks
# --------------------------------------------------------------------------- #


class SubTaskItem(_Item):
    case_id: str
    subtask_id: str
    input_key: str

    # None only in prefailed sub-tasks (unsupported format, never published).
    media_type: ContentType | None = None
    operation: AnalysisOperation | None = None
    pool: Pool | None = None
    queue: str | None = None
    priority: CasePriority = CasePriority.MEDIUM

    status: SubTaskStatus = SubTaskStatus.PENDING
    state_order: int = 0  # write condition: only a greater value is accepted
    attempt: int = 1  # attempt of the latest dispatch (0 if prefailed)
    enqueued: bool = False  # outbox: False until the broker confirmed the publish
    retry_at: datetime | None = None  # when a `retrying` sub-task is republished

    worker_id: str | None = None
    progress: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_meta: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def planned(
        cls,
        *,
        case_id: str,
        subtask_id: str,
        input_key: str,
        media_type: ContentType,
        operation: AnalysisOperation,
        pool: Pool,
        priority: CasePriority,
        params: dict[str, Any] | None = None,
    ) -> "SubTaskItem":
        return cls(
            case_id=case_id,
            subtask_id=subtask_id,
            input_key=input_key,
            media_type=media_type,
            operation=operation,
            pool=pool,
            queue=work_queue(pool),
            priority=priority,
            params=params or {},
        )

    def to_message(self) -> SubTaskMessage:
        if self.operation is None or self.media_type is None:
            raise ValueError("a prefailed sub-task is not published")
        return SubTaskMessage(
            subtask_id=self.subtask_id,
            case_id=self.case_id,
            attempt=self.attempt,
            input_key=self.input_key,
            media_type=self.media_type,
            operation=self.operation,
            priority=self.priority,
            output_prefix=result_prefix(self.case_id, self.subtask_id),
            params=self.params,
        )


# --------------------------------------------------------------------------- #
# Workers (heartbeat: each worker upserts its own row)
# --------------------------------------------------------------------------- #


class WorkerStatus(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"  # set by the coordinator sweeper when the heartbeat is stale


class WorkerItem(_Item):
    worker_id: str  # e.g. "audio-i-0abc123" (pool-instance) or pool-host-pid
    pool: Pool
    instance_type: str
    hostname: str
    concurrency: int = Field(ge=1)
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    active_subtasks: list[str] = Field(default_factory=list)
    status: WorkerStatus = WorkerStatus.ALIVE
    started_at: datetime
    last_seen: datetime

    def is_alive(
        self,
        now: datetime | None = None,
        timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
    ) -> bool:
        now = now or utcnow()
        fresh = now - self.last_seen <= timedelta(seconds=timeout_s)
        return self.status is WorkerStatus.ALIVE and fresh


# --------------------------------------------------------------------------- #
# Load samples (evidence for the report; stored in S3 as JSON Lines)
# --------------------------------------------------------------------------- #


class WorkerLoad(BaseModel):
    pool: Pool
    alive: bool
    cpu_percent: float
    mem_percent: float
    active: int


class LoadSample(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    queue_visible: dict[str, int]  # per logical queue
    queue_in_flight: dict[str, int]
    workers: dict[str, WorkerLoad]  # per worker_id
    cases_open: int


# --------------------------------------------------------------------------- #
# Consolidated report per case (results/{case_id}/report.json)
# --------------------------------------------------------------------------- #


class ReportSubTask(BaseModel):
    analysis: AnalysisResult | None = None
    subtask_id: str
    input_key: str
    media_type: ContentType | None
    operation: AnalysisOperation | None
    status: SubTaskStatus
    worker_id: str | None
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    output_keys: list[str]
    error: ErrorInfo | None


class ReportGroup(BaseModel):
    """A summary row: type + operation."""

    media_type: ContentType | None  # None = unsupported
    operation: AnalysisOperation | None
    total: int
    completed: int
    failed: int
    cancelled: int


class CaseReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    categories: dict[str, int] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    schema_version: int = 1
    case_id: str
    label: str | None
    status: CaseStatus
    priority: CasePriority
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime
    duration_s: float
    file_count: int
    subtask_count: int
    groups: list[ReportGroup]
    subtasks: list[ReportSubTask]
    workers_used: list[str]
    summary: str  # "of 55 files — 30 texts analyzed, 20 images analyzed, 2 failed due to ..."


# --------------------------------------------------------------------------- #
# S3 keys
# --------------------------------------------------------------------------- #

# Dataset inputs live under arbitrary prefixes of the bucket; the coordinator
# writes only under these two, so `GET /api/dataset` skips them.
RESERVED_PREFIXES: tuple[str, ...] = ("results/", "metrics/", "content/")


def upload_key(upload_id: str, filename: str) -> str:
    """Files uploaded manually from the dashboard."""
    return f"uploads/{upload_id}/{filename}"


def result_prefix(case_id: str, subtask_id: str) -> str:
    return f"results/{case_id}/{subtask_id}/"


def report_key(case_id: str) -> str:
    return f"results/{case_id}/report.json"


def load_samples_key(day: str) -> str:
    """day in YYYY-MM-DD format."""
    return f"metrics/{day}.jsonl"


def load_samples_chunk_key(ts: datetime) -> str:
    """Per-minute chunk key, to flush metrics without rewriting the whole day."""
    return f"metrics/{ts:%Y-%m-%d}/{ts:%H-%M}.jsonl"
