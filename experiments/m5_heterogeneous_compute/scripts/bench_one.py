"""One benchmark process: one model configuration over a list of inputs.

Started by ``bench.py`` with the configuration already in the environment
(``AUDIO_MODEL``, ``AUDIO_COMPUTE_TYPE``, ``MODEL_THREADS``, ``MODEL_DEVICE``,
``OMP_NUM_THREADS``), because the worker caches the model per process. It runs
the production operation (``workers.ops.run_local.run``: ffmpeg extraction,
hashing, model, findings) so the latency is what a worker slot spends per
sub-task, minus broker and S3 transfer.

Phases, timed separately:
1. ``import``: interpreter + worker package + model library imports.
2. ``load``: the worker's ``load_model()`` (weights from the local cache).
3. one warm-up run on the first input (not counted),
4. ``--repeats`` warm runs per input.

Writes one JSON document to stdout: per-run rows plus process metadata.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

T0 = time.time()

parser = argparse.ArgumentParser()
parser.add_argument("op", choices=["audio", "image"])
parser.add_argument("inputs", help="JSON file: list of {path, seconds?}")
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--no-warmup", action="store_true")
args = parser.parse_args()

import psutil

PROC = psutil.Process()


def rss() -> int:
    return PROC.memory_info().rss


def peak() -> int:
    if sys.platform == "win32":
        return PROC.memory_info().peak_wset
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


with contextlib.redirect_stdout(sys.stderr):
    if args.op == "audio":
        from workers.ops.analysis import audio as module
    else:
        import torch
        from workers.ops.analysis import image as module

        # image.py does not read MODEL_THREADS; torch picks its intra-op
        # thread count from OMP_NUM_THREADS at import. Set it explicitly too.
        torch.set_num_threads(int(os.environ.get("MODEL_THREADS", "1")))
    from workers.ops.run_local import run

    t_import = time.time()
    rss_before_load = rss()
    module.load_model()
    t_load = time.time()
    rss_after_load = rss()

    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    operation = "audio_analyze" if args.op == "audio" else "image_analyze"
    if not args.no_warmup:
        run(operation, inputs[0]["path"], timeout_s=3600)
    rows = []
    first_start = time.time()
    for repeat in range(args.repeats):
        for item in inputs:
            started = time.perf_counter()
            report = run(operation, item["path"], timeout_s=3600)
            latency = time.perf_counter() - started
            result = report.to_json()["result"]
            rows.append({
                "input": item["path"], "repeat": repeat, "latency_s": latency,
                "extract_s": result["timings_s"].get("extract"), "infer_s": result["timings_s"].get("infer"),
                "transcript": (result.get("transcript") or {}).get("text"),
                "findings": len(result.get("findings", [])),
                "labels": [lbl["label"] for lbl in result.get("image_labels", [])],
                "rss_bytes": rss(),
            })
    last_end = time.time()

print(json.dumps({
    "pid": os.getpid(), "t0": T0, "t_import": t_import, "t_load": t_load,
    "first_start": first_start, "last_end": last_end,
    "rss_before_load": rss_before_load, "rss_after_load": rss_after_load, "peak_bytes": peak(),
    "torch_threads": __import__("torch").get_num_threads() if args.op == "image" else None,
    "rows": rows,
}))
