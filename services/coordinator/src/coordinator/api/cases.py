"""`/api/cases`: create, list, inspect, cancel cases and request their report.

Every endpoint that does blocking I/O (PostgreSQL, S3) is declared `def`
(not `async def`) on purpose: FastAPI runs them in its threadpool instead of
the asyncio loop, so a slow query does not stall the rest of the API.
"""

from __future__ import annotations

import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request
from shared.api import (
    CaseDetailResponse,
    CaseOutputsResponse,
    CreateCaseRequest,
    CreateCaseResponse,
    ReportUrlResponse,
    SubTaskOutputs,
    SubTaskOutputUrl,
)
from shared.models import CaseItem, report_key
from shared.states import TERMINAL_CASE, CaseStatus
from ulid import ULID

from ..admission import reserve
from ..repo import cases as cases_table
from ..repo import subtasks as subtasks_table
from ..repo.cases import CaseAlreadyTerminal, CaseNotFound
from ..routing.planner import plan_case
from .deps import (
    BarrierDep,
    CasesRepoDep,
    DbDep,
    OutboxDep,
    S3Dep,
    SettingsDep,
    SubTasksRepoDep,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases"])

_HEAD_OBJECT_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


@router.post("/cases", response_model=CreateCaseResponse, status_code=201)
def create_case(
    payload: CreateCaseRequest,
    request: Request,
    settings: SettingsDep,
    s3: S3Dep,
    db: DbDep,
    barrier: BarrierDep,
    outbox: OutboxDep,
) -> CreateCaseResponse:
    input_keys = [f.input_key for f in payload.files]
    missing, sizes = _input_sizes(s3, settings.S3_BUCKET, input_keys)
    if missing:
        raise HTTPException(status_code=422, detail={"missing": missing})

    case_id = str(ULID())
    subtasks, prefailed_count = plan_case(payload.files, case_id, payload.priority)
    total = len(subtasks)
    pending_count = total - prefailed_count

    # Outbox: the case and all its sub-tasks in ONE transaction (all or
    # nothing, no orphans), then the relay publishes them. A crash after the
    # commit only delays the publish: the relay finds `enqueued = false` rows
    # on its next pass, including after a coordinator restart.
    with db.connection() as conn:
        conn.execute(
            "SELECT set_config('app.actor', %s, true)", (request.state.identity["sub"],)
        )
        reserve(conn, settings, subtasks, sum(sizes[key] for key in input_keys), sizes)
        cases_table.insert(
            conn,
            CaseItem(
                case_id=case_id,
                label=payload.label,
                source=payload.source,
                priority=payload.priority,
                file_count=len(payload.files),
                total=total,
                pending_count=pending_count,
                failed_count=prefailed_count,
            ),
        )
        subtasks_table.insert_many(conn, subtasks)
    outbox.wake()

    if pending_count == 0:
        # All sub-tasks were born prefailed (unsupported formats): no worker
        # will report anything, so the case is closed in this request.
        barrier.finalize(case_id)

    return CreateCaseResponse(
        case_id=case_id,
        file_count=len(payload.files),
        subtask_count=total,
        prefailed_count=prefailed_count,
    )


def _input_sizes(
    s3: Any, bucket: str, input_keys: list[str]
) -> tuple[list[str], dict[str, int]]:
    """`head_object` for each `input_key`, in parallel (I/O-bound, not CPU-bound)."""

    def _check(key: str) -> tuple[str, int | None]:
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in _HEAD_OBJECT_NOT_FOUND_CODES:
                return key, None
            raise
        return key, int(head["ContentLength"])

    with ThreadPoolExecutor(max_workers=min(16, len(input_keys))) as pool:
        results = list(pool.map(_check, input_keys))
    return (
        [key for key, size in results if size is None],
        {key: size for key, size in results if size is not None},
    )


@router.get("/cases", response_model=list[CaseItem])
def list_cases(
    cases_repo: CasesRepoDep,
    status: CaseStatus | None = None,
    limit: int = 50,
) -> list[CaseItem]:
    return cases_repo.list_recent(status.value if status else None, min(limit, 500))


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(
    case_id: str,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CaseDetailResponse:
    case = cases_repo.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    subtasks = subtasks_repo.list_by_case(case_id)
    return CaseDetailResponse(case=case, subtasks=subtasks)


@router.delete("/cases/{case_id}", response_model=CaseItem)
def cancel_case(case_id: str, cases_repo: CasesRepoDep, request: Request) -> CaseItem:
    try:
        return cases_repo.update_cancel_requested(
            case_id, actor=request.state.identity["sub"]
        )
    except CaseNotFound as exc:
        raise HTTPException(status_code=404, detail="case not found") from exc
    except CaseAlreadyTerminal as exc:
        raise HTTPException(
            status_code=409, detail="the case has already reached a terminal state"
        ) from exc


@router.get("/cases/{case_id}/report", response_model=ReportUrlResponse)
def get_case_report(
    case_id: str,
    settings: SettingsDep,
    s3: S3Dep,
    cases_repo: CasesRepoDep,
) -> ReportUrlResponse:
    case = cases_repo.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status not in TERMINAL_CASE:
        raise HTTPException(status_code=409, detail="the case has not finished yet")

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": report_key(case_id)},
        ExpiresIn=settings.PRESIGN_EXPIRES_S,
    )
    return ReportUrlResponse(
        case_id=case_id, url=url, expires_in=settings.PRESIGN_EXPIRES_S
    )


@router.get("/cases/{case_id}/outputs", response_model=CaseOutputsResponse)
def get_case_outputs(
    case_id: str,
    settings: SettingsDep,
    s3: S3Dep,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CaseOutputsResponse:
    """Presign the input file and the `output_keys` of each subtask.

    Does not require the case to be finished: an individual subtask can have
    outputs ready while other subtasks of the same case are still processing.
    Every subtask is listed, because every subtask has an `input_key`.

    The input is presigned at its original `input_key`, which is never moved
    or deleted: the worker's `content/<sha256>` CAS object is an extra copy.
    Presigning is local signing (no S3 call), so a page polled every few
    seconds does not issue one `head_object` per file.
    """
    case = cases_repo.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    def presign(key: str, content_type: str | None = None) -> SubTaskOutputUrl:
        params = {"Bucket": settings.S3_BUCKET, "Key": key}
        if content_type:
            params["ResponseContentType"] = content_type
        url = s3.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=settings.PRESIGN_EXPIRES_S
        )
        return SubTaskOutputUrl(key=key, url=url, expires_in=settings.PRESIGN_EXPIRES_S)

    result = [
        SubTaskOutputs(
            subtask_id=s.subtask_id,
            outputs=[presign(k) for k in s.output_keys],
            input=presign(s.input_key, _media_content_type(s.input_key)),
        )
        for s in subtasks_repo.list_by_case(case_id)
    ]
    return CaseOutputsResponse(case_id=case_id, subtasks=result)


# Explicit types for every extension `shared.routing.EXTENSIONS` accepts:
# `mimetypes` depends on the host's /etc/mime.types (slim images lack .opus,
# .m4a, .mkv) and returns legacy names such as audio/x-wav.
_MEDIA_CONTENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",  # WhatsApp voice notes: Opus in an Ogg container
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
}


def _media_content_type(key: str) -> str | None:
    """`Content-Type` to force on the presigned GET of an input file.

    Dashboard uploads are PUT without a `Content-Type` (it would break the
    signature), so S3 stores them as `binary/octet-stream` and some browsers
    refuse to play them inline. None leaves the stored type untouched.
    """
    suffix = PurePosixPath(key).suffix.lower()
    return _MEDIA_CONTENT_TYPES.get(suffix) or mimetypes.guess_type(key)[0]
