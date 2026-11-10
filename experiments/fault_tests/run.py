"""B.6 load + optional local Docker/remote SSM fault injection, one CSV per run."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
from load_client.run import run


async def experiment(args):
    task = asyncio.create_task(run(args))
    injected = None
    samples = []

    async def sample():
        import os

        import httpx

        headers = (
            {"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]}
            if os.environ.get("ACCESS_TOKEN")
            else {}
        )
        if os.environ.get("ORIGIN_VERIFY_SECRET"):
            headers["X-Origin-Verify"] = os.environ["ORIGIN_VERIFY_SECRET"]
        async with httpx.AsyncClient(headers=headers, timeout=5) as client:
            while not task.done():
                try:
                    response = await client.get(args.url.rstrip("/") + "/api/state")
                    response.raise_for_status()
                    samples.append({"time": time.time(), "state": response.json()})
                except (httpx.HTTPError, ValueError):
                    samples.append({"time": time.time(), "state": None})
                await asyncio.sleep(0.5)

    sampler = asyncio.create_task(sample())
    if args.scenario in {"worker_crash", "coordinator_restart", "memory_saturation"}:
        await asyncio.sleep(args.inject_after)
        action = "kill" if args.scenario == "worker_crash" else "restart"
        if args.scenario == "memory_saturation":
            # The configured limit is applied before this run; never resize a live cgroup.
            action = "inspect"
        command = ["docker", action, args.container]
        if action == "inspect":
            command += ["--format", "{{.HostConfig.Memory}}"]
        before = time.time()
        if args.instance:
            script = " ".join(__import__("shlex").quote(part) for part in command)
            # dispatch and polling are bounded; credentials stay in AWS CLI config/SSM.
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    "aws",
                    "ssm",
                    "send-command",
                    "--instance-ids",
                    args.instance,
                    "--document-name",
                    "AWS-RunShellScript",
                    "--parameters",
                    json.dumps({"commands": [script]}),
                    "--query",
                    "Command.CommandId",
                    "--output",
                    "text",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            command_id = completed.stdout.strip()
            await asyncio.to_thread(
                subprocess.run,
                [
                    "aws",
                    "ssm",
                    "wait",
                    "command-executed",
                    "--command-id",
                    command_id,
                    "--instance-id",
                    args.instance,
                ],
                check=True,
            )
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    "aws",
                    "ssm",
                    "get-command-invocation",
                    "--command-id",
                    command_id,
                    "--instance-id",
                    args.instance,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            completed = await asyncio.to_thread(
                subprocess.run, command, check=True, capture_output=True, text=True
            )
        injected = {
            "time": before,
            "command": command,
            "result": completed.stdout.strip(),
        }
        if args.scenario == "worker_crash":
            # Restart policy recovers cloud workers. Local containers must use the same policy.
            pass
    try:
        rows = await task
    finally:
        sampler.cancel()
        try:
            await sampler
        except asyncio.CancelledError:
            pass
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".samples.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in samples)
        )
    args.out.with_suffix(".json").write_text(
        json.dumps(
            {
                "scenario": args.scenario,
                "injection": injected,
                "cases": len(rows),
                "note": "memory_saturation requires a preconfigured below-model cgroup limit; inspect confirms its value",
            },
            indent=2,
        )
    )
    if args.scenario == "cancel_inflight":
        if not any(row["outcome"] == "cancelled" for row in rows):
            raise SystemExit(
                "No cancelled case observed; the workload may have finished before cancellation"
            )
        if not any(
            sample.get("state")
            and any(
                case["case"]["status"] == "processing"
                for case in sample["state"]["cases"]
            )
            for sample in samples
        ):
            raise SystemExit(
                "No in-flight work sampled; this run does not prove in-flight cancellation"
            )
    if any(row["outcome"] in {"client_error", "client_timeout"} for row in rows):
        raise SystemExit(1)


def main(default=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--scenario",
        default=default,
        choices=[
            "burst",
            "worker_crash",
            "coordinator_restart",
            "memory_saturation",
            "cancel_inflight",
        ],
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--cancel-after", type=float)
    parser.add_argument("--inject-after", type=float, default=5)
    parser.add_argument("--container")
    parser.add_argument("--instance")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.scenario:
        parser.error("--scenario is required")
    if min(args.cases, args.concurrency, args.timeout) <= 0:
        parser.error("cases/concurrency/timeout must be positive")
    if args.scenario == "cancel_inflight":
        args.cancel_after = args.cancel_after if args.cancel_after is not None else 1
    if (
        args.scenario in {"worker_crash", "coordinator_restart", "memory_saturation"}
        and not args.container
    ):
        parser.error("--container is required for fault injection")
    asyncio.run(experiment(args))


if __name__ == "__main__":
    main()
