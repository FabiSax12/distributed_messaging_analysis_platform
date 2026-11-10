"""RSS/PSS model profiles and Linux fork sharing; invoke inside a memory-limited container."""

import argparse
import csv
import json
import multiprocessing as mp
import os
import platform
import tempfile
import threading
import time
from pathlib import Path

import psutil
from shared.analysis import AnalysisOperation
from workers.ops.analysis import ANALYSIS_OPERATIONS
from workers.ops.run_local import build_task


def load(pool):
    if pool == "image":
        from workers.ops.analysis.image import load_model

        return load_model()
    if pool == "audio":
        from workers.ops.analysis.audio import load_model

        return load_model()
    from workers.ops.analysis.text import RULES

    return RULES


def memory(pid):
    process = psutil.Process(pid)
    info = process.memory_full_info()
    return {"rss_bytes": info.rss, "pss_bytes": getattr(info, "pss", None)}


def child(pool, own, ready, stop):
    if own:
        load(pool)
    ready.set()
    stop.wait(300)


def sharing(pool, count, mode):
    if not hasattr(os, "fork"):
        raise SystemExit("fork/CoW PSS comparison requires Linux")
    if mode == "shared":
        load(pool)
    ctx = mp.get_context("fork" if mode == "shared" else "spawn")
    stop = ctx.Event()
    children = []
    try:
        for _ in range(count):
            ready = ctx.Event()
            proc = ctx.Process(target=child, args=(pool, mode == "own", ready, stop))
            proc.start()
            children.append(proc)
            if not ready.wait(180):
                raise RuntimeError("Child failed to load model")
        samples = [memory(pid) for pid in [os.getpid(), *[p.pid for p in children]]]
        return {
            "pool": pool,
            "mode": mode,
            "children": count,
            "total_rss_bytes": sum(s["rss_bytes"] for s in samples),
            "total_pss_bytes": sum(s["pss_bytes"] for s in samples),
        }
    finally:
        stop.set()
        for proc in children:
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pool", choices=["text", "image", "audio"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--sharing", choices=["own", "shared"])
    parser.add_argument("--processes", type=int, default=3)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.sharing:
        result = sharing(args.pool, args.processes, args.sharing)
        (args.out / f"{args.pool}-{args.sharing}.json").write_text(
            json.dumps(result, indent=2)
        )
        return
    started = time.perf_counter()
    before = memory(os.getpid())
    load(args.pool)
    after = memory(os.getpid())
    load_seconds = time.perf_counter() - started
    rows = []
    stop = threading.Event()
    phase = "loaded"

    def sampler():
        while not stop.is_set():
            rows.append(
                {
                    "seconds": time.perf_counter() - started,
                    "phase": phase,
                    **memory(os.getpid()),
                }
            )
            stop.wait(0.1)

    thread = threading.Thread(target=sampler)
    thread.start()
    task = build_task(AnalysisOperation(args.pool + "_analyze"), args.file)
    try:
        for index in range(args.repeat):
            phase = f"infer-{index + 1}"
            with tempfile.TemporaryDirectory() as tmp:
                ANALYSIS_OPERATIONS[task.operation](
                    task,
                    args.file,
                    Path(tmp),
                    timeout_s=600,
                    on_progress=lambda _: None,
                )
    finally:
        stop.set()
        thread.join()
        with (args.out / f"{args.pool}-samples.csv").open("w", newline="") as file:
            writer = csv.DictWriter(
                file, fieldnames=["seconds", "phase", "rss_bytes", "pss_bytes"]
            )
            writer.writeheader()
            writer.writerows(rows)
    profile = {
        "pool": args.pool,
        "input_bytes": args.file.stat().st_size,
        "os": platform.platform(),
        "pid": os.getpid(),
        "load_seconds": load_seconds,
        "before": before,
        "loaded": after,
        "peak_sampled_rss_bytes": max(row["rss_bytes"] for row in rows),
        "note": "PSS null on Windows; sampled RSS is not kernel peak; no cgroup limit on native host",
    }
    (args.out / f"{args.pool}-profile.json").write_text(json.dumps(profile, indent=2))
    print(json.dumps(profile))


if __name__ == "__main__":
    main()
