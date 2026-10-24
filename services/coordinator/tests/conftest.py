"""Shared fixtures: a fresh `moto` environment (SQS + DynamoDB + S3) for every test.

Important note on import order: `coordinator.config.get_settings()` is
`@lru_cache` and reads environment variables; the `_env` fixture (autouse,
session) sets them ONCE at the start of the session. No module in this package
imports `coordinator.main` (which calls `get_settings()` on import, to build
the middleware) at module level -- always inside a fixture or a test, so that
import does not run before `_env` has run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shared.models import TABLE_CASES, TABLE_SUBTASKS, TABLE_WORKERS
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE

AWS_REGION = "us-east-1"
DATASET_BUCKET = "test-dataset-bucket"
RESULTS_BUCKET = "test-results-bucket"

_ALL_QUEUE_NAMES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)


def _queue_url(name: str) -> str:
    # Deterministic moto format (fixed dummy account 123456789012): this lets
    # us set QUEUE_URLS in the environment BEFORE creating the queues.
    return f"https://sqs.{AWS_REGION}.amazonaws.com/123456789012/{name}"


@pytest.fixture(scope="session", autouse=True)
def _env() -> None:
    """Explicitly set ALL the variables that `Settings` can read.

    Important: if the repo has a real `.env.local` (for running
    against real AWS resources from a laptop),
    pydantic-settings reads it with priority *above* each field's default
    value -- only an explicit environment variable beats it.
    Without this fixture, the tests would end up pointing at the real
    tables/queues/buckets from `.env.local` instead of the test names used
    here, and would fail with `ResourceNotFoundException` (moto's "Cases"
    table is not the real table a real `.env.local` would name).
    """
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["QUEUE_URLS"] = json.dumps(
        {name: _queue_url(name) for name in _ALL_QUEUE_NAMES}
    )
    os.environ["TABLE_CASES"] = TABLE_CASES
    os.environ["TABLE_SUBTASKS"] = TABLE_SUBTASKS
    os.environ["TABLE_WORKERS"] = TABLE_WORKERS
    os.environ["DATASET_BUCKET"] = DATASET_BUCKET
    os.environ["RESULTS_BUCKET"] = RESULTS_BUCKET
    os.environ["ORIGIN_VERIFY_SECRET"] = ""
    os.environ["API_PORT"] = "8000"


def _create_table(
    dynamodb: Any, table_name: str, hash_key: str, range_key: str | None = None
) -> None:
    key_schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    attr_defs = [{"AttributeName": hash_key, "AttributeType": "S"}]
    if range_key:
        key_schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
        attr_defs.append({"AttributeName": range_key, "AttributeType": "S"})
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attr_defs,
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(_env: None) -> Iterator[dict[str, Any]]:
    """Fresh moto backend per test: newly created queues/tables/buckets.

    The cached clients in `coordinator.aws` (created once, the first time a
    test asks for them) keep working against this new backend without issue:
    moto patches boto3 at the transport level, not per client instance, so a
    client created outside a `mock_aws()` (or inside a previous, already
    closed one) still talks to whichever backend is active at call time.
    """
    with mock_aws():
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        s3 = boto3.client("s3", region_name=AWS_REGION)

        for name in _ALL_QUEUE_NAMES:
            sqs.create_queue(QueueName=name)

        _create_table(dynamodb, TABLE_CASES, "case_id")
        _create_table(dynamodb, TABLE_SUBTASKS, "case_id", "subtask_id")
        _create_table(dynamodb, TABLE_WORKERS, "worker_id")

        s3.create_bucket(Bucket=DATASET_BUCKET)
        s3.create_bucket(Bucket=RESULTS_BUCKET)

        yield {"sqs": sqs, "dynamodb": dynamodb, "s3": s3}


@pytest.fixture
def settings(aws: dict[str, Any]):
    from coordinator.config import get_settings

    return get_settings()


@pytest.fixture
def repos(aws: dict[str, Any], settings):
    from coordinator.repo.cases import CasesRepo
    from coordinator.repo.subtasks import SubTasksRepo
    from coordinator.repo.workers import WorkersRepo

    return {
        "cases": CasesRepo(aws["dynamodb"], settings.TABLE_CASES),
        "subtasks": SubTasksRepo(aws["dynamodb"], settings.TABLE_SUBTASKS),
        "workers": WorkersRepo(aws["dynamodb"], settings.TABLE_WORKERS),
    }
