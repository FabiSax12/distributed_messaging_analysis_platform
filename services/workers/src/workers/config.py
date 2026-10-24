"""Worker configuration, read from environment variables.

On an instance they are provided by the deployment (planned: Docker Compose
per node, with the values taken from `terraform output`), including `POOL`.
On a laptop a repo root `.env.local` (gitignored) can be used, the same one as
the coordinator, and `POOL` is exported by hand: it is per instance.

`coordinator.config` is not imported: the worker must not drag FastAPI or
uvicorn onto the worker machines just to read environment variables.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.models import TABLE_CASES, TABLE_WORKERS
from shared.routing import POLL_ORDER, RESULTS_QUEUE, Pool

# Consumer threads per process. Video and audio are CPU-bound (ffmpeg on
# 2-vCPU instances); metadata spends almost all its time waiting on external
# APIs, so a t3.micro can handle more sub-tasks at once.
DEFAULT_CONCURRENCY: dict[Pool, int] = {Pool.VIDEO: 2, Pool.AUDIO: 2, Pool.METADATA: 4}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists() or (parent / "infra").is_dir():
            return parent
    return here.parents[3]  # workers/src/workers/config.py -> repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_repo_root() / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    POOL: Pool
    AWS_REGION: str

    # Same JSON as the coordinator (the 8 logical queues). The worker only requires
    # those in its POLL_ORDER plus `resultados` (the results queue).
    QUEUE_URLS: dict[str, str]

    TABLE_CASES: str = TABLE_CASES
    TABLE_WORKERS: str = TABLE_WORKERS

    DATASET_BUCKET: str
    RESULTS_BUCKET: str

    WORKER_CONCURRENCY: int | None = Field(default=None, ge=1)

    @field_validator("QUEUE_URLS", mode="before")
    @classmethod
    def _parse_queue_urls(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = json.loads(value)
        return value

    @model_validator(mode="after")
    def _check_queue_urls(self) -> Settings:
        needed = {*POLL_ORDER[self.POOL], RESULTS_QUEUE}
        missing = needed - self.QUEUE_URLS.keys()
        if missing:
            raise ValueError(
                f"QUEUE_URLS is missing the queues for pool {self.POOL.value}: {sorted(missing)}"
            )
        return self

    @property
    def concurrency(self) -> int:
        return self.WORKER_CONCURRENCY or DEFAULT_CONCURRENCY[self.POOL]

    @property
    def poll_order(self) -> tuple[str, ...]:
        return POLL_ORDER[self.POOL]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env/.env.local
