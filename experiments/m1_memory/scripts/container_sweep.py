"""Run from repo root after docker build; records OOMKilled before removing containers."""

import argparse
import csv
import json
import subprocess
import uuid
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("pool", choices=["text", "image", "audio"])
parser.add_argument("inputs", type=Path)
parser.add_argument(
    "--out", type=Path, default=Path("experiments/m1_memory/results/linux")
)
parser.add_argument(
    "--limits", nargs="+", default=["64m", "128m", "256m", "512m", "1g", "2g"]
)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
file = {"text": "chat.txt", "image": "blank.png", "audio": "silence.wav"}[args.pool]
rows = []
for limit in args.limits:
    name = "m1-" + uuid.uuid4().hex[:12]
    dest = args.out / args.pool / limit
    dest.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "--name",
        name,
        "--memory",
        limit,
        "--memory-swap",
        limit,
        "-v",
        f"{args.inputs.resolve()}:/inputs:ro",
        "-v",
        f"{dest.resolve()}:/results",
        "madp-m1",
        args.pool,
        "/inputs/" + file,
        "--out",
        "/results",
    ]
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=900
        )
        (dest / "run.txt").write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        inspected = subprocess.run(
            ["docker", "inspect", name], check=True, capture_output=True, text=True
        )
        state = json.loads(inspected.stdout)[0]["State"]
        rows.append(
            {
                "pool": args.pool,
                "limit": limit,
                "exit_code": state["ExitCode"],
                "oom_killed": state["OOMKilled"],
                "command": json.dumps(command),
            }
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
with (args.out / f"{args.pool}-limits.csv").open("w", newline="") as output:
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
