"""Sequential cgroup/PSS/OOM and sharing campaign using production CPU images."""

import argparse
import csv
import json
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/m1_memory/results/linux")
    )
    parser.add_argument(
        "--pools",
        nargs="+",
        choices=["text", "image", "audio"],
        default=["text", "image", "audio"],
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    args.out.mkdir(parents=True, exist_ok=True)
    args.out = args.out.resolve()
    rows = []
    with tempfile.TemporaryDirectory(prefix="madp-m1-fixtures-") as temp:
        inputs = Path(temp)
        inputs.chmod(0o755)
        (inputs / "chat.txt").write_text("Synthetic: te voy a matar\n")
        # Build an RGB PNG with stdlib only.
        import struct
        import zlib

        def chunk(kind, data):
            return (
                struct.pack("!I", len(data))
                + kind
                + data
                + struct.pack("!I", zlib.crc32(kind + data))
            )

        (inputs / "blank.png").write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!2I5B", 320, 320, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80" * 960) * 320))
            + chunk(b"IEND", b"")
        )
        with wave.open(str(inputs / "silence.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0" * 64000)
        for pool in args.pools:
            filename = {
                "text": "chat.txt",
                "image": "blank.png",
                "audio": "silence.wav",
            }[pool]
            limits = (
                ["16m", "32m", "64m", "128m", "256m"]
                if pool == "text"
                else ["64m", "128m", "256m", "512m", "1g"]
            )
            campaigns = [(limit, None) for limit in limits] + [
                ("2g", mode) for mode in ("own", "shared")
            ]
            for limit, mode in campaigns:
                name = "m1-" + uuid.uuid4().hex[:10]
                dest = args.out / pool / (mode or limit)
                dest.mkdir(parents=True, exist_ok=True)
                # Mounted result directory must be writable by production uid 10001.
                dest.chmod(0o777)
                command = [
                    "docker",
                    "run",
                    "--name",
                    name,
                    "--memory",
                    limit,
                    "--memory-swap",
                    limit,
                    "--cpus",
                    "2",
                    "--pids-limit",
                    "128",
                    "-e",
                    "OMP_NUM_THREADS=2",
                    "-e",
                    "MKL_NUM_THREADS=2",
                    "-v",
                    f"{root.resolve()}:/source:ro",
                    "-v",
                    f"{inputs}:/inputs:ro",
                    "-v",
                    f"{dest}:/results",
                    "--entrypoint",
                    "python",
                    f"madp-worker-{pool}:handoff",
                    "/source/experiments/m1_memory/scripts/measure.py",
                    pool,
                    "/inputs/" + filename,
                    "--out",
                    "/results",
                ]
                if mode:
                    command += ["--sharing", mode]
                try:
                    completed = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )
                    (dest / "run.txt").write_text(completed.stdout + completed.stderr)
                    inspected = subprocess.run(
                        ["docker", "inspect", name],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    state = json.loads(inspected.stdout)[0]["State"]
                    rows.append(
                        {
                            "pool": pool,
                            "limit": limit,
                            "mode": mode or "inference",
                            "exit_code": state["ExitCode"],
                            "oom_killed": state["OOMKilled"],
                            "input_bytes": (inputs / filename).stat().st_size,
                        }
                    )
                    print(json.dumps(rows[-1]), flush=True)
                finally:
                    subprocess.run(
                        ["docker", "rm", "-f", name], check=False, capture_output=True
                    )
    with (args.out / "limits.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert any(row["oom_killed"] for row in rows), "No cgroup OOM observed"
    assert all(
        any(row["pool"] == pool and row["exit_code"] == 0 for row in rows)
        for pool in args.pools
    )
    print("Cgroup campaign completed; limits.csv includes failures and successes")


if __name__ == "__main__":
    main()
