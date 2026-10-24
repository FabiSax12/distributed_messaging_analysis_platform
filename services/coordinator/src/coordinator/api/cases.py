"""`/api/cases`: create, list, inspect, cancel cases and request their report.

Every endpoint that calls boto3/DynamoDB is declared `def` (not
`async def`) on purpose: FastAPI runs them in its threadpool instead of the
asyncio loop, so a blocking boto3 call does not stall the rest of the
API while it waits for AWS.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
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

from ..barrier.finalize import finalize
from ..repo.cases import CaseAlreadyTerminal, CaseNotFound
from ..routing.enqueue import enqueue_case
from ..routing.planner import plan_case
from .deps import CasesRepoDep, S3Dep, SettingsDep, SqsDep, SubTasksRepoDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases"])

_HEAD_OBJECT_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


@router.post("/cases", response_model=CreateCaseResponse, status_code=201)
def create_case(
    payload: CreateCaseRequest,
    settings: SettingsDep,
    s3: S3Dep,
    sqs: SqsDep,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CreateCaseResponse:
    input_keys = [f.input_key for f in payload.files]
    missing = _missing_input_keys(s3, settings.DATASET_BUCKET, input_keys)
    if missing:
        raise HTTPException(status_code=422, detail={"missing": missing})

    case_id = str(ULID())
    subtasks, prefailed_count = plan_case(payload.files, case_id, payload.priority)
    total = len(subtasks)
    pending_count = total - prefailed_count

    # Outbox: subtasks (BatchWriteItem) -> case (PutItem) -> enqueue (SQS).
    # If the process dies between the BatchWriteItem and the case PutItem,
    # orphaned subtasks are left in DynamoDB with no `Cases` item referencing
    # them; since they never get enqueued (the case PutItem, which happens
    # later, did not run), nobody processes them or reports results on them;
    # they remain inert garbage until a manual cleanup. Acceptable: the client
    # retries the POST with a new `case_id`.
    subtasks_repo.batch_put_planned(subtasks)
    cases_repo.put_new(
        CaseItem(
            case_id=case_id,
            label=payload.label,
            source=payload.source,
            priority=payload.priority,
            file_count=len(payload.files),
            total=total,
            pending_count=pending_count,
            failed_count=prefailed_count,
        )
    )

    try:
        enqueue_case(
            subtasks,
            sqs=sqs,
            queue_urls=settings.QUEUE_URLS,
            subtasks_repo=subtasks_repo,
        )
    except Exception as exc:
        # The case and its subtasks are already persisted in DynamoDB (outbox
        # above); if enqueueing fails COMPLETELY (not the partial "Failed"
        # case that `enqueue_case` already handles itself, but an exception
        # that propagates, e.g. SQS down), the client must not lose the
        # `case_id`: the periodic sweep (`barrier/sweeper.py`) will re-enqueue
        # the subtasks left with `enqueued=false`.
        logger.exception(
            "enqueue_case failed completely for case %s, the recovery sweep "
            "will retry it",
            case_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "case_id": case_id,
                "message": (
                    "the case was created but could not be fully enqueued; "
                    "the recovery sweep will retry it"
                ),
            },
        ) from exc

    if pending_count == 0:
        # All subtasks were born prefailed (unsupported formats): no worker
        # will send a ResultMessage to trigger the barrier, so we close the
        # case right here, in the same request.
        finalize(
            case_id,
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            s3=s3,
            results_bucket=settings.RESULTS_BUCKET,
        )

    return CreateCaseResponse(
        case_id=case_id,
        file_count=len(payload.files),
        subtask_count=total,
        prefailed_count=prefailed_count,
    )


def _missing_input_keys(s3: Any, bucket: str, input_keys: list[str]) -> list[str]:
    """`head_object` for each `input_key`, in parallel (I/O-bound, not CPU-bound)."""

    def _check(key: str) -> str | None:
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in _HEAD_OBJECT_NOT_FOUND_CODES:
                return key
            raise
        return None

    with ThreadPoolExecutor(max_workers=min(16, len(input_keys))) as pool:
        results = list(pool.map(_check, input_keys))
    return [key for key in results if key is not None]


@router.get("/cases", response_model=list[CaseItem])
def list_cases(
    cases_repo: CasesRepoDep,
    status: CaseStatus | None = None,
    limit: int = 50,
) -> list[CaseItem]:
    return cases_repo.list_recent(status.value if status else None, limit)


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(
    case_id: str,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CaseDetailResponse:
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    subtasks = subtasks_repo.query_by_case(case_id)
    return CaseDetailResponse(case=case, subtasks=subtasks)


@router.delete("/cases/{case_id}", response_model=CaseItem)
def cancel_case(case_id: str, cases_repo: CasesRepoDep) -> CaseItem:
    try:
        return cases_repo.update_cancel_requested(case_id)
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
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if case.status not in TERMINAL_CASE:
        raise HTTPException(status_code=409, detail="the case has not finished yet")

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.RESULTS_BUCKET, "Key": report_key(case_id)},
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
    """Presign the `output_keys` of each subtask, without requiring the case to be finished.

    Unlike `get_case_report`, an individual subtask can have outputs ready
    while other subtasks of the same case are still processing.
    """
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    subtasks = subtasks_repo.query_by_case(case_id)
    result = [
        SubTaskOutputs(
            subtask_id=s.subtask_id,
            outputs=[
                SubTaskOutputUrl(
                    key=k,
                    url=s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": settings.RESULTS_BUCKET, "Key": k},
                        ExpiresIn=settings.PRESIGN_EXPIRES_S,
                    ),
                    expires_in=settings.PRESIGN_EXPIRES_S,
                )
                for k in s.output_keys
            ],
        )
        for s in subtasks
        if s.output_keys
    ]
    return CaseOutputsResponse(case_id=case_id, subtasks=result)
