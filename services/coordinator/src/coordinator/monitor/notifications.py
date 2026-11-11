"""Rate-limited operational alerts and application metrics; best effort only."""

import json
import logging
import threading
import time

import boto3
from botocore.config import Config
from shared.analysis import AnalysisResult, Severity
from shared.models import CaseReport
from shared.states import TERMINAL_SUBTASK

from ..config import get_settings

logger = logging.getLogger(__name__)
_sent = {}
_lock = threading.Lock()
_SEVERITY_RANK = {severity: rank for rank, severity in enumerate(Severity)}


def client(service, settings):
    return boto3.client(
        service,
        region_name=settings.AWS_REGION,
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
    )


def findings_alert(result):
    settings = get_settings()
    if not settings.ALERTS_TOPIC_ARN or "analysis" not in result.output_meta:
        return
    analysis = AnalysisResult.from_meta(result.output_meta)
    severe = [
        finding
        for finding in analysis.findings
        if finding.severity in {Severity.HIGH, Severity.CRITICAL}
    ]
    if not severe:
        return
    key = ("case", result.case_id)
    with _lock:
        if key in _sent:
            return
        try:
            client("sns", settings).publish(
                TopicArn=settings.ALERTS_TOPIC_ARN,
                Subject="Messaging analysis: severe findings",
                Message=json.dumps(
                    {
                        "case_id": result.case_id,
                        "subtask_id": result.subtask_id,
                        "count": len(severe),
                        "categories": sorted(
                            {str(finding.category) for finding in severe}
                        ),
                        "note": "Review source evidence in the authenticated report; scores are heuristic.",
                    }
                ),
            )
            if len(_sent) >= 1024:
                _sent.pop(next(iter(_sent)))
            _sent[key] = time.monotonic()
        except Exception:
            logger.exception("Severe-finding SNS publish failed")


def case_finished_alert(report: CaseReport, report_key: str) -> None:
    """Publish the case-finished message. Called only by the `finalize()` that
    won the guarded terminal UPDATE, after it committed, so a case notifies at
    most once (best-effort: a crash or SNS failure here drops it). Never raises."""
    try:
        settings = get_settings()
        if not settings.ALERTS_TOPIC_ARN:
            return
        counts = {status.value: 0 for status in sorted(TERMINAL_SUBTASK)}
        for subtask in report.subtasks:
            if subtask.status in TERMINAL_SUBTASK:
                counts[subtask.status.value] += 1
        max_severity = max(
            (finding.severity for finding in report.findings),
            key=_SEVERITY_RANK.__getitem__,
            default=None,
        )
        status = report.status.value
        client("sns", settings).publish(
            TopicArn=settings.ALERTS_TOPIC_ARN,
            Subject=f"Case {report.case_id[-8:]} {status}",
            Message=json.dumps(
                {
                    "event": "case_finished",
                    "case_id": report.case_id,
                    "label": (report.label or "")[:200],
                    "status": status,
                    "subtasks": {"total": report.subtask_count, **counts},
                    "duration_s": round(report.duration_s, 1),
                    "findings": len(report.findings),
                    "max_severity": max_severity.value if max_severity else None,
                    "report_key": report_key,
                    "summary": report.summary[:500],
                }
            ),
            # Lets subscribers filter by event type on the shared alerts topic.
            MessageAttributes={
                "event": {"DataType": "String", "StringValue": "case_finished"}
            },
        )
    except Exception:
        logger.exception(
            "Case-finished SNS publish failed", extra={"case_id": report.case_id}
        )


def emit(snapshot):
    settings = get_settings()
    if not settings.ALERTS_TOPIC_ARN:
        return
    now = time.monotonic()
    with _lock:
        for alert in snapshot.alerts:
            key = (alert.pool, alert.level)
            if alert.level != "critical" or now - _sent.get(key, -300) < 300:
                continue
            try:
                client("sns", settings).publish(
                    TopicArn=settings.ALERTS_TOPIC_ARN,
                    Subject="Messaging analysis: " + alert.level,
                    Message=alert.model_dump_json(),
                )
                _sent[key] = now
            except Exception:
                logger.exception("SNS alert failed; continuing snapshot processing")
        if now - _sent.get("metrics", -60) < 60:
            return
        metrics = [
            {
                "MetricName": "QueueDepth",
                "Value": queue.visible,
                "Unit": "Count",
                "Dimensions": [{"Name": "Queue", "Value": queue.queue}],
            }
            for queue in snapshot.queues
        ]
        metrics += [
            {
                "MetricName": "WorkerMemoryPercent",
                "Value": worker.mem_percent,
                "Unit": "Percent",
                "Dimensions": [{"Name": "Pool", "Value": worker.pool.value}],
            }
            for worker in snapshot.workers
            if worker.alive
        ]
        try:
            client("cloudwatch", settings).put_metric_data(
                Namespace="MessagingAnalysis", MetricData=metrics
            )
            _sent["metrics"] = now
        except Exception:
            logger.exception(
                "CloudWatch metrics failed; continuing snapshot processing"
            )
