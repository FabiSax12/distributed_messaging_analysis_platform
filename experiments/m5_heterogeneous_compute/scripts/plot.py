"""Figures for docs/module-m5-heterogeneous-compute.md from bench.py output.

    uv run --all-packages --all-extras python experiments/m5_heterogeneous_compute/scripts/plot.py \
        experiments/m5_heterogeneous_compute/results/<host>

Writes PNGs next to the CSVs and prints the markdown tables used in the report.
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Fixed categorical order (one hue per model/precision, never cycled).
SERIES = {
    ("tiny", "int8"): ("#2a78d6", "tiny int8"),
    ("tiny", "float32"): ("#eb6834", "tiny float32"),
    ("base", "int8"): ("#1baf7a", "base int8"),
    ("base", "float32"): ("#eda100", "base float32"),
}
INK, INK_2, GRID = "#1b1930", "#5e5a78", "#e6e4ee"


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def style(ax, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8)


def main() -> int:
    out = Path(sys.argv[1])
    summary = rows(out / "summary.csv")
    runs = rows(out / "runs.csv")
    parallel = rows(out / "parallel.csv")
    audio = [s for s in summary if s["op"] == "audio"]
    image = [s for s in summary if s["op"] == "image"]

    # 1. Throughput vs threads per model/precision.
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    for key, (color, label) in SERIES.items():
        pts = sorted((int(s["threads"]), float(s["audio_s_per_s"])) for s in audio if (s["model"], s["compute_type"]) == key)
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=6, label=label)
        ax.annotate(label, (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", color=INK_2, fontsize=8, va="center")
    ax.set_xticks([1, 2, 4, 8])
    ax.set_ylim(bottom=0)
    style(ax, "Whisper throughput by threads (one process)", "CPU threads (MODEL_THREADS)", "audio seconds processed per wall second")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "fig_audio_throughput_threads.png")
    plt.close(fig)

    # 2. Latency vs input duration for selected configurations (median of repeats).
    chosen = ["whisper-tiny-int8-t2", "whisper-tiny-float32-t2", "whisper-base-int8-t2", "whisper-base-float32-t4"]
    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    for config, (color, _label) in zip(chosen, SERIES.values(), strict=True):
        per_input = defaultdict(list)
        for r in runs:
            if r["config"] == config:
                per_input[float(r["input_seconds"])].append(float(r["latency_s"]))
        if not per_input:
            continue
        xs = sorted(per_input)
        ys = [statistics.median(per_input[x]) for x in xs]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5, label=config.removeprefix("whisper-"))
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    style(ax, "Sub-task latency vs voice-note length", "voice note duration (s)", "median latency per sub-task (s)")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "fig_audio_latency_duration.png")
    plt.close(fig)

    # 3. Process x thread split of a fixed core budget.
    if parallel:
        fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
        labels = [f"{p['processes']}x{p['threads_each']}" for p in parallel]
        values = [float(p["audio_s_per_s"]) for p in parallel]
        colors = ["#2a78d6" if int(p["total_threads"]) == 4 else "#eb6834" for p in parallel]
        bars = ax.bar(labels, values, color=colors, width=0.6)
        for bar, v in zip(bars, values, strict=True):
            ax.annotate(f"{v:.1f}", (bar.get_x() + bar.get_width() / 2, v), xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=8, color=INK_2)
        from matplotlib.patches import Patch

        ax.legend(handles=[Patch(color="#2a78d6", label="4 threads total"), Patch(color="#eb6834", label="8 threads total")],
                  frameon=False, fontsize=8, loc="upper left")
        style(ax, "tiny int8: processes x threads per process", "processes x threads each", "audio seconds per wall second")
        fig.tight_layout()
        fig.savefig(out / "fig_parallel_split.png")
        plt.close(fig)

    # 4. YOLO latency by threads (p50 and p95, same unit).
    if image:
        fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
        xs = [int(s["threads"]) for s in image]
        ax.plot(xs, [float(s["p50_s"]) * 1000 for s in image], color="#2a78d6", linewidth=2, marker="o", label="p50")
        ax.plot(xs, [float(s["p95_s"]) * 1000 for s in image], color="#eb6834", linewidth=2, marker="o", label="p95")
        ax.set_xticks([1, 2, 4, 8])
        ax.set_ylim(bottom=0)
        style(ax, "YOLOv8n per-image sub-task latency by threads", "torch threads", "latency (ms)")
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        fig.savefig(out / "fig_image_threads.png")
        plt.close(fig)

    # Markdown tables.
    print("| Configuration | p50 (s) | p95 (s) | p50 short / medium / long (s) | Audio s per s | WER | Load (s) | Peak RSS (MB) |")
    print("|---|---|---|---|---|---|---|---|")
    for s in audio:
        print(f"| {s['config'].removeprefix('whisper-')} | {float(s['p50_s']):.2f} | {float(s['p95_s']):.2f} | "
              f"{float(s['p50_short_s']):.2f} / {float(s['p50_medium_s']):.2f} / {float(s['p50_long_s']):.2f} | "
              f"{float(s['audio_s_per_s']):.1f} | {float(s['wer_mean']):.3f} | {float(s['load_s']):.2f} | {float(s['peak_mb']):.0f} |")
    print()
    print("| Configuration | p50 (ms) | p95 (ms) | Images per s | Load (s) | Peak RSS (MB) |")
    print("|---|---|---|---|---|---|")
    for s in image:
        print(f"| {s['config']} | {float(s['p50_s']) * 1000:.0f} | {float(s['p95_s']) * 1000:.0f} | {float(s['images_per_s']):.1f} | "
              f"{float(s['load_s']):.2f} | {float(s['peak_mb']):.0f} |")
    print()
    print("| Processes x threads | Total threads | Steady makespan (s) | Audio s per s | Sum of peak RSS (MB) |")
    print("|---|---|---|---|---|")
    for p in parallel:
        print(f"| {p['processes']} x {p['threads_each']} | {p['total_threads']} | {p['steady_makespan_s']} | {p['audio_s_per_s']} | {p['sum_peak_mb']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
