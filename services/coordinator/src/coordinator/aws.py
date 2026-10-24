"""boto3 clients shared across the whole coordinator.

We use low-level clients (not `boto3.resource`) for DynamoDB because they are
thread-safe and because the rest of the code combines `shared.models.to_item`/
`from_item` (which work with plain Python dicts) with `TypeSerializer`/
`TypeDeserializer` from `boto3.dynamodb.types` to convert to and from the
DynamoDB attribute format (`{"S": "..."}`, `{"N": "..."}`, etc.). One client
per service per process: boto3 documents them as thread-safe and creating a
new one per request wastes connections.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config

from .config import get_settings

_BOTO_CONFIG = Config(retries={"mode": "standard", "max_attempts": 5})

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


@lru_cache
def sqs_client():
    return boto3.client(
        "sqs", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


@lru_cache
def dynamodb_client():
    return boto3.client(
        "dynamodb", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


@lru_cache
def s3_client():
    return boto3.client(
        "s3", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


def serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Python dict (output of `shared.models.to_item`) -> low-level DynamoDB format."""
    return {k: _serializer.serialize(v) for k, v in item.items()}


def deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Low-level DynamoDB format -> Python dict (input of `shared.models.from_item`)."""
    return {k: _deserializer.deserialize(v) for k, v in item.items()}
