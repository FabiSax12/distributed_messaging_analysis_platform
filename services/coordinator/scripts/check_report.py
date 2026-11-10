"""Assert report findings, evidence chronology and processing timeline contract."""

from datetime import UTC, datetime

from coordinator.reports.builder import build_report
from coordinator.routing.planner import plan_case
from shared.analysis import (
    AnalysisOperation,
    AnalysisResult,
    CasePriority,
    Evidence,
    Finding,
)
from shared.api import CaseFileIn
from shared.models import CaseItem
from shared.states import CaseStatus, SubTaskStatus


def main():
    stamp = datetime(2024, 3, 1, tzinfo=UTC)
    now = datetime.now(UTC)
    case = CaseItem(
        case_id="report-check",
        priority=CasePriority.LOW,
        file_count=1,
        total=1,
        pending_count=0,
    )
    tasks, _ = plan_case(
        [CaseFileIn(input_key="chat.txt")], case.case_id, case.priority
    )
    result = AnalysisResult(
        operation=AnalysisOperation.TEXT_ANALYZE,
        findings=[
            Finding(
                category="threat",
                severity="high",
                confidence=0.65,
                label="Synthetic threat",
                evidence=[
                    Evidence(
                        source_key="chat.txt",
                        kind="message",
                        message_index=0,
                        timestamp=stamp,
                        excerpt="Synthetic",
                    )
                ],
            )
        ],
    )
    tasks[0] = tasks[0].model_copy(
        update={
            "status": SubTaskStatus.COMPLETED,
            "output_meta": result.to_meta(),
            "started_at": now,
            "finished_at": now,
        }
    )
    report = build_report(case, tasks, status=CaseStatus.COMPLETED, finished_at=now)
    assert report.categories == {"threat": 1} and len(report.findings) == 1
    event = next(item for item in report.timeline if item.get("type") == "finding")
    assert (
        event["time"] == stamp.isoformat() and event["evidence"]["message_index"] == 0
    )
    assert sum(item.get("type") == "processing" for item in report.timeline) == 1
    assert report.subtasks[0].analysis == result
    print(
        "Report aggregation, evidence chronology and processing timeline checks passed"
    )


if __name__ == "__main__":
    main()
