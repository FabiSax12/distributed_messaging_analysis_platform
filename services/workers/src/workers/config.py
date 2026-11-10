"""Worker configuration, read from environment variables.

Locally the values come from `infra/docker/dev.env` (compose stack) plus
`POOL`, which is per process. On AWS they come from `terraform output` and
the credentials in Secrets Manager (see `services/workers/README.md`). No
credential has a default here.

`coordinator.config` is not imported: the worker must not drag FastAPI or
uvicorn onto the worker machines just to read environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.models import DEFAULT_HEARTBEAT_INTERVAL_S
from shared.routing import Pool

# Upper bound handed to each op as `timeout_s`. Audio covers transcribing a
# long voice note or the audio track of a video.
DEFAULT_OP_TIMEOUT_S: dict[Pool, float] = {
    Pool.TEXT: 120.0,
    Pool.IMAGE: 120.0,
    Pool.AUDIO: 600.0,
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists() or (parent / "infra").is_dir():
            return parent
    return here.parents[3]  # workers/src/workers/config.py -> repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_repo_root() / ".env.local",  # optional, gitignored
        env_file_encoding="utf-8",
        extra="ignore",
    )

    POOL: Pool

    # amqp://user:pass@localhost:5673/ locally, amqps://user:pass@host:5671
    # on Amazon MQ (URL-encode the password).
    AMQP_URL: str
    # postgresql://user:pass@host:5432/db (add ?sslmode=require for RDS).
    DATABASE_URL: str

    S3_BUCKET: str
    S3_ENDPOINT_URL: str | None = None  # MinIO locally; unset on AWS
    AWS_REGION: str = "us-east-1"

    # Consumer threads per process. Each one owns an AMQP connection with
    # prefetch=1, so this is how many sub-tasks the worker runs at once.
    WORKER_CONCURRENCY: int = Field(default=1, ge=1)
    OP_TIMEOUT_S: float | None = Field(default=None, gt=0)
    HEARTBEAT_INTERVAL_S: float = Field(default=DEFAULT_HEARTBEAT_INTERVAL_S, gt=0)

    @property
    def op_timeout_s(self) -> float:
        return self.OP_TIMEOUT_S or DEFAULT_OP_TIMEOUT_S[self.POOL]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env
