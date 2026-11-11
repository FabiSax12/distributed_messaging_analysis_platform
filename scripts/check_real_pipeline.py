"""Linux Docker real-model smoke and concurrent load, with isolated temporary fixtures.

Requires the four local images tagged :handoff; only containers created here are removed.
"""

import argparse
import asyncio
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import wave
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
from load_client.run import run


def command(args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def png(path):
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data))
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 320, 320, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x80" * 960) * 320))
        + chunk(b"IEND", b"")
    )


async def campaign(args, results, hostnames):
    task = asyncio.create_task(run(args))
    rows = []
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=5) as client:
        while not task.done():
            response = await client.get(args.url + "/api/state")
            if response.status_code == 200:
                for worker in response.json()["workers"]:
                    if worker["alive"] and any(
                        "-" + hostname + "-" in worker["worker_id"]
                        for hostname in hostnames
                    ):
                        rows.append(
                            {
                                "seconds": time.monotonic() - started,
                                "worker_id": worker["worker_id"],
                                "pool": worker["pool"],
                                "memory_percent": worker["mem_percent"],
                            }
                        )
            await asyncio.sleep(0.5)
    outcome = await task
    import csv

    if rows:
        with (results / "worker-memory.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "experiments/fault_tests/burst/results"
    )
    cli = parser.parse_args()
    if cli.cases < 1:
        parser.error("--cases must be positive")
    results = cli.out
    results.mkdir(parents=True, exist_ok=True)
    prefix = "madp-check-" + uuid.uuid4().hex[:8]
    containers = []
    try:
        for pool in ("coordinator", "text", "image", "audio"):
            name = prefix + "-" + pool
            image = "coordinator" if pool == "coordinator" else "worker-" + pool
            args = [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "--env-file",
                str(ROOT / "infra/docker/dev.env"),
                "--memory",
                "256m" if pool == "text" else "1g",
                "--memory-swap",
                "256m" if pool == "text" else "1g",
                "--cpus",
                "2",
                "--pids-limit",
                "128",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-e",
                "OMP_NUM_THREADS=2",
                "-e",
                "MKL_NUM_THREADS=2",
            ]
            if pool != "coordinator":
                args += ["-e", "POOL=" + pool]
            command(args + ["madp-" + image + ":handoff"])
            containers.append(name)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if (
                    httpx.get("http://127.0.0.1:8000/healthz", timeout=2).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Coordinator did not become healthy")
        with tempfile.TemporaryDirectory(prefix="madp-fixtures-") as directory:
            root = Path(directory)
            (root / "chat.txt").write_text(
                "01/03/24, 21:15 - Synthetic: te voy a matar\n", encoding="utf-8"
            )
            png(root / "blank.png")
            with wave.open(str(root / "silence.wav"), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00" * 64000)
            manifest = {
                "priority": "high",
                "sources": [
                    {
                        "path": name,
                        "sha256": hashlib.sha256(
                            (root / name).read_bytes()
                        ).hexdigest(),
                    }
                    for name in ("chat.txt", "blank.png", "silence.wav")
                ],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            args = SimpleNamespace(
                manifest=path,
                url="http://127.0.0.1:8000",
                cases=cli.cases,
                concurrency=20,
                timeout=180,
                cancel_after=None,
                out=results / "real-model-cases.csv",
            )
            hostnames = [
                command(["docker", "inspect", "--format", "{{.Config.Hostname}}", name])
                for name in containers[1:]
            ]
            rows = asyncio.run(campaign(args, results, hostnames))
            assert all(row["outcome"] == "completed" for row in rows), rows
            for row in rows:
                completed_case = httpx.get(
                    args.url + "/api/cases/" + row["case_id"]
                ).json()
                assert all(
                    task["output_meta"]["analysis"]["model"]["name"] != "fake-worker"
                    for task in completed_case["subtasks"]
                ), "Fake worker consumed a real-model campaign task"
            detail = httpx.get(args.url + "/api/cases/" + rows[0]["case_id"]).json()
            report_url = httpx.get(
                args.url + "/api/cases/" + rows[0]["case_id"] + "/report"
            ).json()["url"]
            report = httpx.get(report_url).json()
            assert report["findings"] and report["categories"]["threat"] >= 1
            assert len(report["timeline"]) >= 3
            assert (
                sum(event.get("type") == "processing" for event in report["timeline"])
                == 3
            )
            assert all(task["output_meta"]["analysis"] for task in detail["subtasks"])
            (results / "real-model-summary.json").write_text(
                json.dumps(
                    {
                        "cases": len(rows),
                        "findings": len(report["findings"]),
                        "timeline_entries": len(report["timeline"]),
                        "note": "real CPU models, synthetic fixtures, Docker cgroup limits; not AWS or dataset accuracy",
                    },
                    indent=2,
                )
            )
        for name in containers:
            metadata = json.loads(command(["docker", "inspect", name]))[0]
            state = {
                "name": name,
                "user": metadata["Config"]["User"],
                "memory_limit": metadata["HostConfig"]["Memory"],
                "oom_killed": metadata["State"]["OOMKilled"],
            }
            with (results / "containers.jsonl").open("a") as file:
                file.write(json.dumps(state) + "\n")
        print("Real model pipeline, aggregate findings and concurrent cases passed")
    finally:
        for name in containers:
            logs = command(["docker", "logs", name])
            (results / (name + ".log")).write_text(logs)
            subprocess.run(
                ["docker", "rm", "-f", name], check=False, capture_output=True
            )


if __name__ == "__main__":
    main()
