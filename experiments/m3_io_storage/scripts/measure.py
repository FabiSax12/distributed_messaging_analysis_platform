"""Synthetic local I/O benchmark; --dataset optionally measures real dedup."""

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import queue
import statistics
import tempfile
import threading
import time
from pathlib import Path

import psutil
from storage.backpressure import OverflowPolicy, QueueClosed, QueueFull
from storage.backpressure.thread_queue import ThreadBoundedQueue
from storage.chunking.file_chunker import DiskReassembler, FileChunker
from storage.content_store.file_store import FileContentStore


def save(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def burst(policy):
    q = (
        queue.Queue()
        if policy == "unbounded"
        else ThreadBoundedQueue(8, OverflowPolicy(policy))
    )
    latencies, samples = [], []
    done = threading.Event()

    def consumer():
        while True:
            try:
                item = q.get(timeout=0.05)
            except (TimeoutError, queue.Empty):
                if done.is_set():
                    break
                continue
            except QueueClosed:
                break
            if item is None:
                break
            time.sleep(0.005)

    thread = threading.Thread(target=consumer)
    thread.start()
    rejected = 0
    started = time.perf_counter()
    process = psutil.Process()

    def sampler():
        while not done.wait(0.002):
            samples.append(
                {
                    "policy": policy,
                    "seconds": time.perf_counter() - started,
                    "depth": q.qsize() if policy == "unbounded" else q.depth(),
                    "rss_bytes": process.memory_info().rss,
                }
            )

    sampling = threading.Thread(target=sampler)
    sampling.start()
    for _ in range(200):
        # Separate payloads make the unbounded queue's memory cost observable.
        payload = os.urandom(256 * 1024)
        before = time.perf_counter()
        try:
            q.put(payload)
        except QueueFull:
            rejected += 1
        latencies.append((time.perf_counter() - before) * 1000)
    if policy == "unbounded":
        q.put(None)
    else:
        q.close()
    thread.join()
    done.set()
    sampling.join()
    return {
        "policy": policy,
        "puts": 200,
        "rejected": rejected,
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[189],
        "peak_depth": max(s["depth"] for s in samples),
        "peak_rss_bytes": max(s["rss_bytes"] for s in samples),
    }, samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/m3_io_storage/results")
    )
    parser.add_argument("--dataset", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "machine.json").write_text(
        json.dumps(
            {
                "os": platform.platform(),
                "python": platform.python_version(),
                "logical_cpus": os.cpu_count(),
                "disk_path": str(Path(tempfile.gettempdir()).resolve()),
                "source": "synthetic 200 MiB; local temporary disk; warm filesystem cache",
            },
            indent=2,
        )
    )
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "heavy.bin"
        block = os.urandom(1024 * 1024)
        with src.open("wb") as file:
            for _ in range(200):
                file.write(block)
        with src.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        for size in (256 * 1024, 4 * 1024 * 1024, 16 * 1024 * 1024):
            for repeat in range(3):
                started = time.perf_counter()
                with DiskReassembler(
                    digest, (src.stat().st_size + size - 1) // size
                ) as receiver:
                    with src.open("rb") as file:
                        for chunk in FileChunker().split(file, size):
                            receiver.add(chunk)
                    with (root / "out.bin").open("wb") as file:
                        assert receiver.finish(file) == digest
                seconds = time.perf_counter() - started
                rows.append(
                    {
                        "chunk_bytes": size,
                        "run": repeat + 1,
                        "input_bytes": src.stat().st_size,
                        "seconds": seconds,
                        "mib_s": 200 / seconds,
                    }
                )
        save(args.out / "throughput.csv", rows)
        store = FileContentStore(root / "cas")
        if args.dataset:
            for manifest in sorted(args.dataset.glob("*/manifest.json")):
                for entry in json.loads(manifest.read_text())["sources"]:
                    path = (manifest.parent / entry["path"]).resolve()
                    if not path.is_relative_to(manifest.parent.resolve()):
                        raise ValueError("Dataset path escapes case directory")
                    with path.open("rb") as file:
                        store.put(file)
            source = "dataset"
        else:
            for data in (b"forwarded image" * 1000, b"voice note" * 2000) * 5:
                store.put(io.BytesIO(data))
            source = "synthetic repeated attachments; not dataset evidence"
        from dataclasses import asdict

        stats = store.stats()
        save(
            args.out / "dedup.csv",
            [{"source": source, **asdict(stats), "dedup_ratio": stats.dedup_ratio}],
        )
    summaries, samples = [], []
    for policy in ("unbounded", "block", "reject"):
        summary, trace = burst(policy)
        summaries.append(summary)
        samples.extend(trace)
    save(args.out / "backpressure.csv", summaries)
    save(args.out / "queue_samples.csv", samples)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for size in sorted({r["chunk_bytes"] for r in rows}):
        values = [r["mib_s"] for r in rows if r["chunk_bytes"] == size]
        axes[0].bar(
            str(size // 1024), statistics.mean(values), yerr=statistics.stdev(values)
        )
    axes[0].set(
        xlabel="Chunk KiB", ylabel="MiB/s", title="Local verified transfer (n=3)"
    )
    for policy in ("unbounded", "block", "reject"):
        trace = [s for s in samples if s["policy"] == policy]
        axes[1].plot(
            [s["seconds"] for s in trace], [s["depth"] for s in trace], label=policy
        )
        axes[2].plot(
            [s["seconds"] for s in trace],
            [s["rss_bytes"] / 2**20 for s in trace],
            label=policy,
        )
    axes[1].set(xlabel="Seconds", ylabel="Queue depth")
    axes[2].set(xlabel="Seconds", ylabel="Process RSS MiB")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(args.out / "measurements.png", dpi=150)
    plt.close(fig)
    print(
        json.dumps({"throughput_runs": len(rows), "backpressure": summaries}, indent=2)
    )


if __name__ == "__main__":
    main()
