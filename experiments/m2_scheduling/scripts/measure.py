"""Run one labeled scheduling campaign; restart coordinator to switch its policy."""

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))
from coordinator.scheduler import ordered, priority
from load_client.run import run
from shared.analysis import CasePriority


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--policy", choices=["fifo", "aging"], default="aging")
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/m2_scheduling/results/cases.csv")
    )
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    now = datetime.now(UTC)
    low = SimpleNamespace(
        priority=CasePriority.LOW,
        created_at=now - timedelta(seconds=120),
        subtask_id="old",
    )
    high = SimpleNamespace(
        priority=CasePriority.CRITICAL, created_at=now, subtask_id="new"
    )
    assert priority(low, "aging", 30, now) == 4
    assert ordered([high, low], "aging", 30)[0] is low
    assert ordered([high, low], "fifo", 30)[0] is low
    if args.self_check:
        print("FIFO and priority aging/starvation checks passed")
        return
    if args.manifest is None:
        parser.error("manifest is required")
    args.cases, args.concurrency, args.timeout, args.cancel_after = 40, 20, 600, None
    args.priority_pattern = ["low", "critical", "medium", "high"]
    asyncio.run(run(args))
    args.out.with_suffix(".json").write_text(
        json.dumps(
            {
                "policy_label": args.policy,
                "note": "Set SCHEDULER_POLICY on coordinator and restart before running; label does not change server config",
            }
        )
    )
    with args.out.open() as file:
        assert len(list(csv.DictReader(file))) == args.cases


if __name__ == "__main__":
    main()
