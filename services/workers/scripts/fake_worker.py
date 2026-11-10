#!/usr/bin/env python3
"""A real worker (`workers.main.run_worker`: RabbitMQ consumer, heartbeat,
pre-start checks, S3 upload, results) with a fake op for every analysis
operation, so a case can be driven end to end without ML models.

The fake op sleeps `--work-s` while reporting progress, then uploads a small
JSON file and returns a valid `AnalysisResult`. Fault injection:

- `--fail-rate P`: raise a transient `external_api_error`, which the
  coordinator retries with backoff (`retrying`) up to RETRY_MAX_ATTEMPTS.
- `--crash-rate P`: kill the whole process halfway through the op
  (`os._exit(137)`, like `kill -9`): the broker requeues the message and the
  coordinator's sweeper/heartbeat timeout handles the rest.

Usage (from the repo root, with the compose stack up):
    set -a; source infra/docker/dev.env; set +a
    uv run --package workers python services/workers/scripts/fake_worker.py \\
        --pool text --fail-rate 0.3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

from shared.analysis import AnalysisOperation, AnalysisResult, ModelInfo
from shared.messages import ErrorCode, SubTaskMessage
from shared.routing import Pool
from workers.config import Settings
from workers.logging import setup_logging
from workers.main import run_worker
from workers.ops.base import OpError, OpOutput

logger = logging.getLogger("fake_worker")

_STEPS = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pool", required=True, choices=[p.value for p in Pool])
    parser.add_argument("--work-s", type=float, default=2.0, help="fake op duration")
    parser.add_argument("--fail-rate", type=float, default=0.0)
    parser.add_argument("--crash-rate", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    for flag in ("fail_rate", "crash_rate"):
        if not 0.0 <= getattr(args, flag) <= 1.0:
            parser.error(f"--{flag.replace('_', '-')} must be between 0 and 1")
    return args


def fake_op(work_s, fail_rate, crash_rate):
    return partial(_fake_op, work_s, fail_rate, crash_rate)


def _fake_op(
    work_s,
    fail_rate,
    crash_rate,
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    crash_at = _STEPS // 2 if random.random() < crash_rate else None
    for step in range(_STEPS):
        if step == crash_at:
            logger.warning("simulated crash on %s", task.subtask_id)
            os._exit(137)
        time.sleep(work_s / _STEPS)
        on_progress(int((step + 1) / _STEPS * 100) - 1)
    if random.random() < fail_rate:
        raise OpError(ErrorCode.EXTERNAL_API_ERROR, "simulated transient failure")

    result = AnalysisResult(
        operation=task.operation,
        keywords=["fake"],
        model=ModelInfo(name="fake-worker"),
        timings_s={"infer": work_s},
    )
    out = out_dir / "analysis.json"
    out.write_text(json.dumps({"input_bytes": src.stat().st_size, **result.to_meta()}))
    return OpOutput([out], meta=result.to_meta())


def main() -> None:
    setup_logging()
    args = parse_args()
    settings = Settings(POOL=args.pool, WORKER_CONCURRENCY=args.concurrency)  # type: ignore[call-arg]
    op = fake_op(args.work_s, args.fail_rate, args.crash_rate)
    run_worker(settings, {operation: op for operation in AnalysisOperation})


if __name__ == "__main__":
    main()
