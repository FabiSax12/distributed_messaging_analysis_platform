"""REST API contracts of the coordinator (/api/*).

FastAPI generates the OpenAPI spec from these models; the dashboard derives its
types from it with `openapi-typescript`, so the contracts are written once.

Planned endpoints:
  GET    /api/dataset              -> list[DatasetEntry]
  POST   /api/uploads              UploadUrlRequest -> UploadUrlResponse
  POST   /api/cases                CreateCaseRequest -> CreateCaseResponse
  GET    /api/cases/{case_id}      -> CaseDetailResponse
  DELETE /api/cases/{case_id}      -> CaseItem (cancel_requested = true)
  GET    /api/cases/{case_id}/report -> ReportUrlResponse
  GET    /api/cases/{case_id}/outputs -> CaseOutputsResponse
  GET    /api/state                -> StateResponse   (polled every 2 s)
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .analysis import AnalysisOperation, CasePriority, ContentType
from .messages import utcnow
from .models import CaseItem, CaseSource, LoadSample, SubTaskItem, WorkerItem

# ---- Dataset and manual upload ---------------------------------------------- #

class DatasetEntry(BaseModel):
    input_key: str
    size_bytes: int
    media_type: ContentType | None        # None = unsupported extension
    metadata: dict[str, Any] = Field(default_factory=dict)  # from the dataset JSON


class UploadUrlRequest(BaseModel):
    filenames: list[str] = Field(min_length=1, max_length=200)


class UploadUrl(BaseModel):
    filename: str
    input_key: str
    url: str                  # presigned PUT
    expires_in: int


class UploadUrlResponse(BaseModel):
    upload_id: str
    urls: list[UploadUrl]


# ---- Cases ------------------------------------------------------------------ #

class CaseFileIn(BaseModel):
    input_key: str
    operations: list[AnalysisOperation] | None = None  # None = the coordinator decides
    params: dict[str, Any] = Field(default_factory=dict)


class CreateCaseRequest(BaseModel):
    files: list[CaseFileIn] = Field(min_length=1, max_length=1000)
    priority: CasePriority = CasePriority.MEDIUM
    label: str | None = Field(default=None, max_length=200)
    source: CaseSource = CaseSource.MANUAL


class CreateCaseResponse(BaseModel):
    case_id: str
    file_count: int
    subtask_count: int
    prefailed_count: int      # sub-tasks created already failed (unsupported format)


class CaseDetailResponse(BaseModel):
    case: CaseItem
    subtasks: list[SubTaskItem]


class ReportUrlResponse(BaseModel):
    case_id: str
    url: str                  # presigned GET of report.json
    expires_in: int


class SubTaskOutputUrl(BaseModel):
    key: str
    url: str                  # presigned GET of the file
    expires_in: int


class SubTaskOutputs(BaseModel):
    subtask_id: str
    outputs: list[SubTaskOutputUrl]
    input: SubTaskOutputUrl | None = None  # presigned GET of the sub-task's input_key


class CaseOutputsResponse(BaseModel):
    """Presigned URLs of the input file and `output_keys` of each sub-task.

    Unlike `ReportUrlResponse`, it does not require the case to have finished:
    a sub-task can have `output_keys` while the case is still running.
    It includes every sub-task of the case (all have an `input_key`);
    `outputs` is empty until the worker writes results. The input URL forces
    a `Content-Type` from the extension so the browser can play media that
    was uploaded without one.
    """

    case_id: str
    subtasks: list[SubTaskOutputs]


# ---- System state ----------------------------------------------------- #

class QueueDepth(BaseModel):
    queue: str                # "work.text", "work.image", "work.audio", "results", "dead-letter"
    visible: int              # ready messages in the broker
    in_flight: int            # work queues: sub-tasks assigned/running; others: 0


class WorkerView(WorkerItem):
    alive: bool


class CaseSummary(BaseModel):
    """Case + sub-task count per state, for the dashboard overview."""

    case: CaseItem
    by_status: dict[str, int]         # {"pending": 3, "running": 2, ...}


class Alert(BaseModel):
    """Operational alert (e.g. stalled case, overflowing queue) for the dashboard."""

    level: str  # "info" | "warning" | "critical"
    pool: str  # affected pool, or "-" if it does not apply to a particular pool
    message: str
    ts: datetime = Field(default_factory=utcnow)


class StateResponse(BaseModel):
    generated_at: datetime = Field(default_factory=utcnow)
    cases: list[CaseSummary]
    workers: list[WorkerView]
    queues: list[QueueDepth]
    alerts: list[Alert] = Field(default_factory=list)


# ---- Metrics ----------------------------------------------------------------- #


class MetricsResponse(BaseModel):
    samples: list[LoadSample]
