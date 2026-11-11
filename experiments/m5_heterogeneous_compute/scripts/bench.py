"""M5 benchmark: Whisper (faster-whisper) and YOLOv8n under CPU thread,
quantization and process-parallelism configurations.

    uv run --all-packages --all-extras --group dataset python \
        experiments/m5_heterogeneous_compute/scripts/bench.py [--suite all|audio|image|parallel] \
        [--out experiments/m5_heterogeneous_compute/results/<host>] [--repeats 3] [--device cpu]

Inputs come from the project dataset (generate it first, see docs/dataset.md):
12 voice notes stratified by duration (4 under 10 s, 4 of 10-60 s, 4 over
60 s) and 40 images, picked with seed 42. Every configuration runs in its own
process (``bench_one.py``) because the worker caches the model per process.

Suites:
- ``audio``: model {tiny, base} x compute type {int8, float32} x threads.
- ``image``: YOLOv8n x torch threads.
- ``parallel``: tiny/int8 with P processes x T threads on a fixed budget of
  cores (the worker-pool sizing question: more slots or more threads per slot).

Outputs (one directory per host): ``runs.csv`` (one row per warm run),
``processes.csv`` (cold start, load time, memory per process), ``parallel.csv``,
``summary.csv``/``summary.md`` (p50/p95, throughput, memory per configuration),
``host.json``. ``--device cuda`` with ``float16`` configurations is accepted
for a GPU host (e.g. Colab) but CPU PyTorch is what the repo pins.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "dataset"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(DATASET / "generators"))

AUDIO_CONFIGS = [  # (model, compute type, threads)
    ("tiny", "int8", 1), ("tiny", "int8", 2), ("tiny", "int8", 4), ("tiny", "int8", 8),
    ("tiny", "float32", 2), ("tiny", "float32", 4),
    ("base", "int8", 2), ("base", "int8", 4),
    ("base", "float32", 4),
]
IMAGE_THREADS = [1, 2, 4, 8]
PARALLEL = [(1, 4), (2, 2), (4, 1), (1, 8), (2, 4), (4, 2), (8, 1)]  # (processes, threads each)


def probe_seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def dataset_sources(content_type: str) -> list[dict]:
    rows = []
    for manifest in sorted((DATASET / "cases").glob("*/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for s in data["sources"]:
            path = manifest.parent / s["path"]
            if s["content_type"] == content_type and path.is_file():
                rows.append({"case_id": data["case_id"], "rel": s["path"], "path": str(path), "bytes": s["size_bytes"]})
    return rows


def select_audio(rng: random.Random) -> list[dict]:
    notes = dataset_sources("audio")
    for n in notes:
        n["seconds"] = probe_seconds(Path(n["path"]))
    buckets = [[n for n in notes if lo <= n["seconds"] < hi] for lo, hi in ((0, 10), (10, 60), (60, 1e9))]
    picked = [n for b in buckets for n in sorted(rng.sample(b, 4), key=lambda x: x["seconds"])]
    references = reference_transcripts()
    for n in picked:
        n["reference"] = references.get((n["case_id"], n["rel"]), "")
    return picked


def select_images(rng: random.Random) -> list[dict]:
    return sorted(rng.sample(dataset_sources("image"), 40), key=lambda x: x["path"])


def reference_transcripts() -> dict:
    """What the generator made each voice note say (same seed, same plan)."""
    import generate_dataset as gen

    refs = {}
    for plan in gen.plan_dataset(42):
        names = gen._media_names(plan)
        for k, voice in enumerate(plan.voices):
            refs[(plan.case_id, names[("audio", k)])] = " ".join(t for t, _ in voice.sentences)
    return refs


_NUM = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
        9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 20: "twenty", 30: "thirty"}


def words(text: str) -> list[str]:
    text = re.sub(r"\d+", lambda m: _NUM.get(int(m.group()), m.group()), text.lower().replace("'", ""))
    return re.findall(r"[a-z]+", text)


def wer(reference: str, hypothesis: str) -> float | None:
    ref, hyp = words(reference), words(hypothesis or "")
    if not ref:
        return None
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def run_process(op: str, inputs: Path, env_extra: dict, repeats: int, warmup: bool = True) -> tuple[dict, float]:
    env = {**os.environ, **{k: str(v) for k, v in env_extra.items()}, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    cmd = [sys.executable, str(HERE / "bench_one.py"), op, str(inputs), "--repeats", str(repeats)]
    if not warmup:
        cmd.append("--no-warmup")
    spawned = time.time()
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), spawned


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def host_info(device: str) -> dict:
    versions = {}
    for pkg in ("faster-whisper", "ctranslate2", "torch", "ultralytics", "onnxruntime"):
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    cpu = platform.processor()
    if sys.platform == "win32":
        cpu = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                             capture_output=True, text=True, check=False).stdout.strip() or cpu
    elif Path("/proc/cpuinfo").is_file():
        m = re.search(r"model name\s*:\s*(.*)", Path("/proc/cpuinfo").read_text())
        cpu = m.group(1) if m else cpu
    ffmpeg = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    return {"date": time.strftime("%Y-%m-%d %H:%M:%S %z"), "os": platform.platform(), "cpu": cpu,
            "physical_cores": psutil.cpu_count(logical=False), "logical_cpus": psutil.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1), "python": platform.python_version(),
            "device": device, "versions": versions, "ffmpeg": ffmpeg,
            "memory_scope": "process peak working set (Windows) / ru_maxrss (Linux) of the benchmark process: "
                            "interpreter + imports + model + runtime buffers; ffmpeg child excluded"}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["all", "audio", "image", "parallel"], default="all")
    parser.add_argument("--out", type=Path, default=ROOT / "experiments/m5_heterogeneous_compute/results" / platform.node().lower())
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    audio_inputs = select_audio(rng)
    image_inputs = select_images(rng)
    (args.out / "inputs_audio.json").write_text(json.dumps(audio_inputs, indent=1), encoding="utf-8")
    (args.out / "inputs_image.json").write_text(json.dumps(image_inputs, indent=1), encoding="utf-8")
    (args.out / "host.json").write_text(json.dumps(host_info(args.device), indent=1), encoding="utf-8")
    if args.suite != "image":
        # Fetch weights now so no download lands inside a measured load time.
        from faster_whisper import download_model

        for model in sorted({m for m, _, _ in AUDIO_CONFIGS}):
            download_model(model)
    seconds = {i["path"]: i["seconds"] for i in audio_inputs}
    refs = {i["path"]: i["reference"] for i in audio_inputs}

    runs, procs, summary, parallel = [], [], [], []

    def record(config: str, op: str, meta: dict, spawned: float, extra: dict) -> None:
        procs.append({"config": config, "op": op, **extra, "cold_import_s": meta["t_import"] - spawned,
                      "load_s": meta["t_load"] - meta["t_import"], "rss_before_load_mb": meta["rss_before_load"] / 2**20,
                      "rss_after_load_mb": meta["rss_after_load"] / 2**20, "peak_mb": meta["peak_bytes"] / 2**20,
                      "torch_threads": meta.get("torch_threads"),
                      "ultralytics_default_threads": meta.get("ultralytics_default_threads")})
        lat = []
        for r in meta["rows"]:
            row = {"config": config, "op": op, **extra, "input": Path(r["input"]).name, "repeat": r["repeat"],
                   "latency_s": round(r["latency_s"], 4), "extract_s": r["extract_s"], "infer_s": r["infer_s"],
                   "findings": r["findings"]}
            if op == "audio":
                row["input_seconds"] = round(seconds[r["input"]], 2)
                row["rtf"] = round(r["latency_s"] / seconds[r["input"]], 4)
                row["wer"] = None if r["repeat"] else wer(refs[r["input"]], r["transcript"])
            runs.append(row)
            lat.append(r["latency_s"])
        total_wall = sum(lat)
        s = {"config": config, "op": op, **extra, "runs": len(lat),
             "p50_s": percentile(lat, 0.5), "p95_s": percentile(lat, 0.95), "mean_s": statistics.mean(lat),
             "load_s": meta["t_load"] - meta["t_import"], "peak_mb": meta["peak_bytes"] / 2**20,
             "model_mb": (meta["rss_after_load"] - meta["rss_before_load"]) / 2**20}
        if op == "audio":
            audio_s = sum(seconds[r["input"]] for r in meta["rows"])
            s["audio_s_per_s"] = audio_s / total_wall
            for name, lo, hi in (("short", 0, 10), ("medium", 10, 60), ("long", 60, 1e9)):
                sub = [r["latency_s"] for r in meta["rows"] if lo <= seconds[r["input"]] < hi]
                s[f"p50_{name}_s"] = percentile(sub, 0.5)
                s[f"p95_{name}_s"] = percentile(sub, 0.95)
            w = [x["wer"] for x in runs if x["config"] == config and x.get("wer") is not None]
            s["wer_mean"] = statistics.mean(w) if w else None
        else:
            s["images_per_s"] = len(lat) / total_wall
        summary.append(s)
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()}), flush=True)

    if args.suite in ("all", "audio"):
        path = args.out / "inputs_audio.json"
        for model, compute, threads in AUDIO_CONFIGS:
            config = f"whisper-{model}-{compute}-t{threads}"
            env = {"AUDIO_MODEL": model, "AUDIO_COMPUTE_TYPE": compute, "MODEL_THREADS": threads,
                   "MODEL_DEVICE": args.device, "OMP_NUM_THREADS": threads}
            meta, spawned = run_process("audio", path, env, args.repeats)
            record(config, "audio", meta, spawned, {"model": model, "compute_type": compute, "threads": threads, "processes": 1})

    if args.suite in ("all", "image"):
        path = args.out / "inputs_image.json"
        for threads in IMAGE_THREADS:
            env = {"MODEL_THREADS": threads, "OMP_NUM_THREADS": threads, "MKL_NUM_THREADS": threads, "MODEL_DEVICE": args.device}
            meta, spawned = run_process("image", path, env, args.repeats)
            record(f"yolov8n-t{threads}", "image", meta, spawned,
                   {"model": "yolov8n", "compute_type": "float32", "threads": threads, "processes": 1})

    if args.suite in ("all", "parallel"):
        work = [i for i in audio_inputs if i["seconds"] >= 10]  # medium + long notes
        for procs_n, threads in PARALLEL:
            shards = [work[k::procs_n] for k in range(procs_n)]
            files = []
            for k, shard in enumerate(shards):
                f = args.out / f"shard_{procs_n}x{threads}_{k}.json"
                f.write_text(json.dumps(shard), encoding="utf-8")
                files.append(f)
            env = {**os.environ, "AUDIO_MODEL": "tiny", "AUDIO_COMPUTE_TYPE": "int8", "MODEL_THREADS": str(threads),
                   "OMP_NUM_THREADS": str(threads), "MODEL_DEVICE": args.device, "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
            started = time.time()
            handles = [subprocess.Popen([sys.executable, str(HERE / "bench_one.py"), "audio", str(f), "--repeats", "1",
                                         "--no-warmup"], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                       for f in files]
            metas = [json.loads(h.communicate()[0].strip().splitlines()[-1]) for h in handles]
            ended = time.time()
            steady = max(m["last_end"] for m in metas) - min(m["first_start"] for m in metas)
            audio_s = sum(i["seconds"] for i in work)
            parallel.append({"processes": procs_n, "threads_each": threads, "total_threads": procs_n * threads,
                             "inputs": len(work), "audio_s": round(audio_s, 1), "makespan_s": round(ended - started, 2),
                             "steady_makespan_s": round(steady, 2), "audio_s_per_s": round(audio_s / steady, 2),
                             "sum_peak_mb": round(sum(m["peak_bytes"] for m in metas) / 2**20, 1),
                             "max_load_s": round(max(m["t_load"] - m["t_import"] for m in metas), 2)})
            for f in files:
                f.unlink()
            print(json.dumps(parallel[-1]), flush=True)

    suffix = "" if args.suite == "all" else f"_{args.suite}"
    write_csv(args.out / f"runs{suffix}.csv", runs)
    write_csv(args.out / f"processes{suffix}.csv", procs)
    write_csv(args.out / f"summary{suffix}.csv", [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()} for s in summary])
    write_csv(args.out / f"parallel{suffix}.csv", parallel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
