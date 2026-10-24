"""Shared fixtures: a fresh moto (SQS + DynamoDB + S3) per test.

As in `services/coordinator/tests/conftest.py`, `_env` sets ALL the variables that
`workers.config.Settings` can read, so a `.env.local` in the repo root
never leaks into the tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shared.models import TABLE_CASES, TABLE_WORKERS
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE

AWS_REGION = "us-east-1"
DATASET_BUCKET = "test-dataset-bucket"
RESULTS_BUCKET = "test-results-bucket"

_ALL_QUEUE_NAMES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)

# Legacy SQS visibility timeouts per pool (the old queue layout; the RabbitMQ
# migration will replace this).
VISIBILITY_TIMEOUTS = {"video": 900, "audio": 300, "metadatos": 120}


def queue_url(name: str) -> str:
    # moto's deterministic format (dummy account 123456789012).
    return f"https://sqs.{AWS_REGION}.amazonaws.com/123456789012/{name}"


QUEUE_URLS = {name: queue_url(name) for name in _ALL_QUEUE_NAMES}


@pytest.fixture(scope="session", autouse=True)
def _env() -> None:
    os.environ["POOL"] = "video"
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["AWS_DEFAULT_REGION"] = AWS_REGION
    os.environ["QUEUE_URLS"] = json.dumps(QUEUE_URLS)
    os.environ["TABLE_CASES"] = TABLE_CASES
    os.environ["TABLE_WORKERS"] = TABLE_WORKERS
    os.environ["DATASET_BUCKET"] = DATASET_BUCKET
    os.environ["RESULTS_BUCKET"] = RESULTS_BUCKET
    os.environ.pop("WORKER_CONCURRENCY", None)
    # Keep any local endpoint (e.g. the moto server from local_e2e.py) from leaking in.
    os.environ.pop("AWS_ENDPOINT_URL", None)


@pytest.fixture
def aws(_env: None) -> Iterator[dict[str, Any]]:
    with mock_aws():
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        s3 = boto3.client("s3", region_name=AWS_REGION)

        for name in _ALL_QUEUE_NAMES:
            pool = name.split("-")[0]
            attrs = {}
            if pool in VISIBILITY_TIMEOUTS:
                attrs["VisibilityTimeout"] = str(VISIBILITY_TIMEOUTS[pool])
            sqs.create_queue(QueueName=name, Attributes=attrs)

        for table, key in ((TABLE_CASES, "case_id"), (TABLE_WORKERS, "worker_id")):
            dynamodb.create_table(
                TableName=table,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

        s3.create_bucket(Bucket=DATASET_BUCKET)
        s3.create_bucket(Bucket=RESULTS_BUCKET)

        yield {"sqs": sqs, "dynamodb": dynamodb, "s3": s3}


def make_task(operation: str, media_type: str, **params: Any):
    """Minimal SubTaskMessage to exercise an operation or the runner."""
    from shared.messages import SubTaskMessage

    return SubTaskMessage(
        subtask_id="st-1",
        case_id="case-1",
        input_key=params.pop("input_key", "uploads/u1/input.bin"),
        media_type=media_type,
        operation=operation,
        priority="normal",
        output_prefix="results/case-1/st-1/",
        params=params,
    )
