"""Local FIFO/aging comparison using the existing real stack and fake text ops."""

import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/workers/scripts"))
sys.path.insert(0, str(ROOT / "services"))
from load_client.run import run
from local_e2e import Stack, load_env


def main():
    active = subprocess.run(
        ["docker", "ps", "--filter", "name=madp-check-", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if active:
        raise RuntimeError(
            "Run scheduling and real-model campaigns sequentially on the shared dev broker"
        )
    out = ROOT / "experiments/m2_scheduling/results/local"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        text = b"Synthetic scheduling workload\n"
        (root / "chat.txt").write_bytes(text)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "sources": [
                        {"path": "chat.txt", "sha256": hashlib.sha256(text).hexdigest()}
                    ]
                }
            )
        )
        for policy in ("fifo", "aging"):
            env = load_env()
            env["SCHEDULER_POLICY"] = policy
            env["SCHEDULER_AGING_S"] = "2"
            logs = out / policy
            logs.mkdir(exist_ok=True)
            stack = Stack(env, logs)
            try:
                stack.start_coordinator()
                stack.start_worker("text", "--work-s", "0.5")
                args = SimpleNamespace(
                    manifest=manifest,
                    url="http://127.0.0.1:8765",
                    cases=40,
                    concurrency=20,
                    timeout=120,
                    cancel_after=None,
                    priority_pattern=["low", "critical", "medium", "high"],
                    out=out / (policy + ".csv"),
                )
                rows = asyncio.run(run(args))
                assert all(row["outcome"] == "completed" for row in rows), rows
            finally:
                stack.stop()
    (out / "method.json").write_text(
        json.dumps(
            {
                "workers": 1,
                "fake_work_s": 0.5,
                "cases": 40,
                "priorities": ["low", "critical", "medium", "high"],
                "aging_s": 2,
                "note": "Real PostgreSQL/RabbitMQ and isolated fake text work. No cloud or model-performance claim.",
            },
            indent=2,
        )
    )
    print("FIFO and aging local campaigns completed")


if __name__ == "__main__":
    main()
