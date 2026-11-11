"""Build the consolidated `CaseReport` that `barrier/finalize.py` uploads to S3."""

from __future__ import annotations

from datetime import datetime

from shared.analysis import AnalysisOperation, AnalysisResult, ContentType
from shared.messages import ErrorCode
from shared.models import (
    CaseItem,
    CaseReport,
    ReportGroup,
    ReportSubTask,
    SubTaskItem,
)
from shared.states import CaseStatus, SubTaskStatus

_OPERATION_LABELS: dict[AnalysisOperation, str] = {
    AnalysisOperation.TEXT_ANALYZE: "texts analyzed",
    AnalysisOperation.IMAGE_ANALYZE: "images analyzed",
    AnalysisOperation.AUDIO_ANALYZE: "audios analyzed",
}

_ERROR_LABELS: dict[ErrorCode, str] = {
    ErrorCode.UNSUPPORTED_FORMAT: "unsupported format",
    ErrorCode.CORRUPT_INPUT: "corrupt file",
    ErrorCode.PROCESSING_ERROR: "processing error",
    ErrorCode.EXTERNAL_API_ERROR: "external API error",
    ErrorCode.TIMEOUT: "processing timeout",
    ErrorCode.RETRIES_EXHAUSTED: "retries exhausted",
    ErrorCode.WORKER_LOST: "worker lost",
    ErrorCode.DEAD_LETTERED: "dead-lettered message",
    ErrorCode.CANCELLED: "cancellation",
    ErrorCode.INTERNAL: "internal worker error",
}


def build_report(
    case: CaseItem,
    subtasks: list[SubTaskItem],
    *,
    status: CaseStatus,
    finished_at: datetime,
) -> CaseReport:
    """Build a case's consolidated report.

    `status` and `finished_at` are passed explicitly instead of being read from
    `case.status`/`case.finished_at`: by the time `finalize()` calls
    this, it has not yet written the final state to PostgreSQL (the report is
    uploaded to S3 BEFORE the case state, see the docstring of
    `barrier/finalize.py`), so `case` still carries the previous state
    (`processing`, `retrying`, ...).
    """
    groups = _build_groups(subtasks)
    report_subtasks = [_build_subtask_report(subtask) for subtask in subtasks]
    workers_used = sorted(
        {subtask.worker_id for subtask in subtasks if subtask.worker_id}
    )
    duration_s = (finished_at - case.created_at).total_seconds()
    summary = _build_summary(case, subtasks)

    findings = [
        finding
        for subtask in report_subtasks
        if subtask.analysis
        for finding in subtask.analysis.findings
    ]
    categories = {
        str(f.category): sum(other.category == f.category for other in findings)
        for f in findings
    }
    timeline = sorted(
        [
            {
                "type": "processing",
                "subtask_id": task.subtask_id,
                "status": task.status.value,
                "time": (
                    task.finished_at or task.started_at or task.created_at
                ).isoformat(),
                "input_key": task.input_key,
            }
            for task in subtasks
        ],
        key=lambda item: item["time"],
    )
    for subtask in report_subtasks:
        if subtask.analysis is None:
            continue
        for finding in subtask.analysis.findings:
            for evidence in finding.evidence:
                timeline.append(
                    {
                        "type": "finding",
                        "subtask_id": subtask.subtask_id,
                        "time": evidence.timestamp.isoformat()
                        if evidence.timestamp
                        else None,
                        "category": finding.category.value,
                        "severity": finding.severity.value,
                        "evidence": evidence.model_dump(mode="json", exclude_none=True),
                    }
                )
    timeline.sort(key=lambda event: event["time"] or "9999")
    return CaseReport(
        findings=findings,
        categories=categories,
        timeline=timeline,
        case_id=case.case_id,
        label=case.label,
        status=status,
        priority=case.priority,
        created_at=case.created_at,
        started_at=case.started_at,
        finished_at=finished_at,
        duration_s=duration_s,
        file_count=case.file_count,
        subtask_count=len(subtasks),
        groups=groups,
        subtasks=report_subtasks,
        workers_used=workers_used,
        summary=summary,
    )


def _build_groups(subtasks: list[SubTaskItem]) -> list[ReportGroup]:
    """Group by (media_type, operation); prefailed ones fall into (None, None)."""
    buckets: dict[
        tuple[ContentType | None, AnalysisOperation | None], dict[str, int]
    ] = {}
    for subtask in subtasks:
        key = (subtask.media_type, subtask.operation)
        bucket = buckets.setdefault(
            key, {"total": 0, "completed": 0, "failed": 0, "cancelled": 0}
        )
        bucket["total"] += 1
        if subtask.status is SubTaskStatus.COMPLETED:
            bucket["completed"] += 1
        elif subtask.status is SubTaskStatus.FAILED:
            bucket["failed"] += 1
        elif subtask.status is SubTaskStatus.CANCELLED:
            bucket["cancelled"] += 1

    return [
        ReportGroup(media_type=media_type, operation=operation, **counts)
        for (media_type, operation), counts in buckets.items()
    ]


def _build_subtask_report(subtask: SubTaskItem) -> ReportSubTask:
    duration_s = None
    if subtask.started_at is not None and subtask.finished_at is not None:
        duration_s = (subtask.finished_at - subtask.started_at).total_seconds()

    analysis = (
        AnalysisResult.from_meta(subtask.output_meta)
        if "analysis" in subtask.output_meta
        else None
    )
    return ReportSubTask(
        analysis=analysis,
        subtask_id=subtask.subtask_id,
        input_key=subtask.input_key,
        media_type=subtask.media_type,
        operation=subtask.operation,
        status=subtask.status,
        worker_id=subtask.worker_id,
        attempt=subtask.attempt,
        started_at=subtask.started_at,
        finished_at=subtask.finished_at,
        duration_s=duration_s,
        output_keys=subtask.output_keys,
        error=subtask.error,
    )


def _build_summary(case: CaseItem, subtasks: list[SubTaskItem]) -> str:
    """English text, e.g.:

    'of 55 files — 30 texts analyzed, 15 images analyzed,
    8 audios analyzed, 2 failed due to unsupported format'
    """
    completed_counts: dict[AnalysisOperation, int] = {}
    error_counts: dict[ErrorCode, int] = {}

    for subtask in subtasks:
        if subtask.status is SubTaskStatus.COMPLETED and subtask.operation is not None:
            completed_counts[subtask.operation] = (
                completed_counts.get(subtask.operation, 0) + 1
            )
        elif subtask.status is SubTaskStatus.FAILED and subtask.error is not None:
            error_counts[subtask.error.code] = (
                error_counts.get(subtask.error.code, 0) + 1
            )

    parts = [
        f"{count} {_OPERATION_LABELS[op]}" for op, count in completed_counts.items()
    ]
    parts += [
        f"{count} failed due to {_ERROR_LABELS[code]}"
        for code, count in error_counts.items()
    ]

    if not parts:
        return f"of {case.file_count} files, no results recorded yet"

    return f"of {case.file_count} files — " + ", ".join(parts)
