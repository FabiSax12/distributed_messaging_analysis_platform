"""Coordinator configuration, read from environment variables.

Locally the values point at `infra/docker/compose.dev.yml` (RabbitMQ,
PostgreSQL, MinIO); on AWS they come from `terraform output` plus the
credentials in Secrets Manager (see `services/coordinator/README.md`). A
gitignored `.env.local` at the repo root can hold the same variables;
`_repo_root()` finds that root by walking up from this file. No credential
has a default here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.models import DEFAULT_HEARTBEAT_TIMEOUT_S


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists() or (parent / "infra").is_dir():
            return parent
    return here.parents[3]  # coordinator/src/coordinator/config.py -> repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_repo_root() / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # amqp://user:pass@localhost:5672/ locally, amqps://user:pass@host:5671 on
    # Amazon MQ (URL-encode the password).
    AMQP_URL: str
    # postgresql://user:pass@host:5432/db (add ?sslmode=require for RDS).
    DATABASE_URL: str
    DB_POOL_MAX: int = Field(default=10, ge=2)

    # One bucket holds dataset inputs (any prefix), results/ and metrics/.
    S3_BUCKET: str
    S3_ENDPOINT_URL: str | None = None  # MinIO locally; unset on AWS
    AWS_REGION: str = "us-east-1"

    API_PORT: int = 8000
    ORIGIN_VERIFY_SECRET: str = ""

    # M1: zero disables each limit; byte estimates must be calibrated on the
    # deployed models/input envelope, not inferred from compressed file size.
    M1_MAX_ACTIVE_CASES: int = Field(default=0, ge=0)
    M1_MAX_CASE_INPUT_BYTES: int = Field(default=0, ge=0)
    M1_MEMORY_BUDGET_BYTES: int = Field(default=0, ge=0)
    M1_TASK_BASE_BYTES: int = Field(default=0, ge=0)
    M1_OPERATION_BASE_BYTES: dict[str, Annotated[int, Field(gt=0)]] = Field(
        default_factory=dict
    )
    M1_INPUT_MEMORY_MULTIPLIER: int = Field(default=4, ge=1)
    M1_RETRY_AFTER_S: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def memory_budget_has_estimate(self) -> Settings:
        if self.M1_MEMORY_BUDGET_BYTES and not self.M1_TASK_BASE_BYTES:
            raise ValueError("M1_MEMORY_BUDGET_BYTES requires M1_TASK_BASE_BYTES > 0")
        return self

    # `api/ws.py`: application ping interval (CloudFront closes idle
    # connections) and cap on simultaneous WebSocket connections.
    WS_PING_INTERVAL_S: float = 25.0
    WS_MAX_CLIENTS: int = 20

    # Retries (B.4): attempts per sub-task including the first one, and the
    # exponential backoff delay = min(MAX_DELAY, BASE * 2^(attempt - 1)).
    RETRY_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    RETRY_BASE_DELAY_S: float = Field(default=2.0, ge=0)
    RETRY_MAX_DELAY_S: float = Field(default=60.0, ge=0)

    # A worker whose last heartbeat is older than this is marked dead and its
    # in-flight sub-tasks are retried.
    HEARTBEAT_TIMEOUT_S: float = Field(default=DEFAULT_HEARTBEAT_TIMEOUT_S, gt=0)

    RESULTS_CONSUMER_THREADS: int = Field(default=4, ge=1)
    RESULTS_PREFETCH: int = Field(default=20, ge=1)
    PRESIGN_EXPIRES_S: int = 900
    SWEEP_INTERVAL_S: float = 5.0
    OUTBOX_POLL_S: float = 1.0
    SNAPSHOT_INTERVAL_S: float = 2.0
    SAMPLE_INTERVAL_S: int = 5
    METRICS_FLUSH_S: int = 60
    RECENT_CASES_IN_STATE: int = 50

    # monitor/balancer.py: ready messages in a pool queue above which it is
    # considered "saturated" (see `balancer.check_pool_saturation`).
    POOL_SATURATION_QUEUE_THRESHOLD: int = 20
    POOL_SATURATION_CPU_PERCENT: float = 85.0

    def retry_delay_s(self, attempt: int) -> float:
        """Delay before dispatching `attempt + 1` after attempt `attempt` failed."""
        return min(self.RETRY_MAX_DELAY_S, self.RETRY_BASE_DELAY_S * 2 ** (attempt - 1))


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env/.env.local
