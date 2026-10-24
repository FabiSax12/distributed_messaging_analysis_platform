"""DynamoDB items, consolidated report, load samples and S3 keys.

Tables:
- Cases     PK case_id
- SubTasks  PK case_id, SK subtask_id  (one Query returns all the sub-tasks of the case)
- Workers   PK worker_id

Barrier attributes in Cases that are NOT in the model:
- `closed` (string set with the subtask_ids already counted). It is not
  initialized: DynamoDB does not accept empty sets; the first
  `ADD closed :idSet` creates it and `NOT contains(closed, :id)` is true
  while it does not exist.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .messages import ErrorInfo, SubTaskMessage, utcnow
from .routing import MediaType, Operation, Pool, Priority, queue_key
from .states import CaseStatus, SubTaskStatus

TABLE_CASES = "Cases"
TABLE_SUBTASKS = "SubTasks"
TABLE_WORKERS = "Workers"

HEARTBEAT_INTERVAL_S = 5
WORKER_DEAD_AFTER_S = 15


# --------------------------------------------------------------------------- #
# Pydantic <-> DynamoDB conversion (boto3 resource uses Decimal, not float)
# --------------------------------------------------------------------------- #


def _floats_to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


def to_item(model: BaseModel) -> dict[str, Any]:
    """Model -> item for put_item. Dates as ISO 8601 (sortable), no None."""
    return _floats_to_decimal(model.model_dump(mode="json", exclude_none=True))


def from_item[M: BaseModel](cls: type[M], item: dict[str, Any]) -> M:
    return cls.model_validate(item)


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")  # ignores `closed` and future attributes


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

class CaseSource(StrEnum):
    MANUAL = "manual"   # submitted from the dashboard or the API
    AUTO = "auto"       # grouped by the generator (folder / metadata JSON)


class CaseItem(_Item):
    case_id: str
    label: str | None = None
    source: CaseSource = CaseSource.MANUAL
    priority: Priority = Priority.NORMAL
    status: CaseStatus = CaseStatus.QUEUED

    file_count: int = Field(ge=1)
    total: int = Field(ge=1)          # sub-tasks, not files
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

    # None only in prefailed sub-tasks (unsupported format, never enqueued).
    media_type: MediaType | None = None
    operation: Operation | None = None
    pool: Pool | None = None
    queue: str | None = None
    priority: Priority = Priority.NORMAL

    status: SubTaskStatus = SubTaskStatus.PENDING
    state_order: int = 0     # write condition: only a greater value is accepted
    attempt: int = 0
    enqueued: bool = False   # outbox: False until SendMessage confirmed

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
        media_type: MediaType,
        operation: Operation,
        pool: Pool,
        priority: Priority,
        params: dict[str, Any] | None = None,
    ) -> "SubTaskItem":
        return cls(
            case_id=case_id,
            subtask_id=subtask_id,
            input_key=input_key,
            media_type=media_type,
            operation=operation,
            pool=pool,
            queue=queue_key(pool, priority),
            priority=priority,
            params=params or {},
        )

    def to_message(self) -> SubTaskMessage:
        if self.operation is None or self.media_type is None:
            raise ValueError("a prefailed sub-task is not enqueued")
        return SubTaskMessage(
            subtask_id=self.subtask_id,
            case_id=self.case_id,
            input_key=self.input_key,
            media_type=self.media_type,
            operation=self.operation,
            priority=self.priority,
            output_prefix=result_prefix(self.case_id, self.subtask_id),
            params=self.params,
        )


# --------------------------------------------------------------------------- #
# Workers (heartbeat: each worker writes it directly)
# --------------------------------------------------------------------------- #

class WorkerItem(_Item):
    worker_id: str          # e.g. "video-i-0abc123-4711" (pool-instance-pid)
    pool: Pool
    instance_type: str
    hostname: str
    concurrency: int = Field(ge=1)
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    active_subtasks: list[str] = Field(default_factory=list)
    started_at: datetime
    last_seen: datetime

    def is_alive(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return now - self.last_seen <= timedelta(seconds=WORKER_DEAD_AFTER_S)


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
    queue_visible: dict[str, int]      # per logical queue
    queue_in_flight: dict[str, int]
    workers: dict[str, WorkerLoad]     # per worker_id
    cases_open: int


# --------------------------------------------------------------------------- #
# Consolidated report per case (results/{case_id}/report.json)
# --------------------------------------------------------------------------- #

class ReportSubTask(BaseModel):
    subtask_id: str
    input_key: str
    media_type: MediaType | None
    operation: Operation | None
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

    media_type: MediaType | None   # None = unsupported
    operation: Operation | None
    total: int
    completed: int
    failed: int
    cancelled: int


class CaseReport(BaseModel):
    schema_version: int = 1
    case_id: str
    label: str | None
    status: CaseStatus
    priority: Priority
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime
    duration_s: float
    file_count: int
    subtask_count: int
    groups: list[ReportGroup]
    subtasks: list[ReportSubTask]
    workers_used: list[str]
    summary: str   # "of 55 files — 30 audios converted, 10 videos…, 2 failed due to format"


# --------------------------------------------------------------------------- #
# S3 keys
# --------------------------------------------------------------------------- #

def upload_key(upload_id: str, filename: str) -> str:
    """Files uploaded manually from the dashboard (dataset bucket)."""
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
