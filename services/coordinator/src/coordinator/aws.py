"""S3 client shared across the whole coordinator.

S3 is the byte store (dataset inputs, analysis outputs, reports, metrics);
state lives in PostgreSQL and messages in RabbitMQ. One client per process:
boto3 clients are thread-safe and creating one per request wastes
connections. `S3_ENDPOINT_URL` points it at MinIO locally (path-style
addressing, so presigned URLs work without virtual-host DNS).
"""

from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config

from .config import get_settings


@lru_cache
def s3_client():
    settings = get_settings()
    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        s3={"addressing_style": "path"} if settings.S3_ENDPOINT_URL else {},
        signature_version="s3v4",
    )
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        config=config,
    )
