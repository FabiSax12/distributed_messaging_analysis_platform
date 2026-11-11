"""Plot recorded cgroup profiles and loaded-process sharing, without rerunning models."""

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1] / "results/linux"
pools = ["text", "image", "audio"]
fig, axes = plt.subplots(1, 2, figsize=(11, 4))
for index, pool in enumerate(pools):
    profile = (
        json.loads((root / pool / "1g" / f"{pool}-profile.json").read_text())
        if pool != "text"
        else json.loads((root / pool / "256m" / f"{pool}-profile.json").read_text())
    )
    axes[0].bar(
        index - 0.15,
        profile["loaded"]["pss_bytes"] / 2**20,
        0.3,
        color="#4774a8",
        label="Loaded PSS" if index == 0 else None,
    )
    axes[0].bar(
        index + 0.15,
        profile["peak_sampled_rss_bytes"] / 2**20,
        0.3,
        color="#db8556",
        label="Sampled peak RSS" if index == 0 else None,
    )
    for offset, mode, color in [(-0.15, "own", "#4774a8"), (0.15, "shared", "#589b76")]:
        row = json.loads((root / pool / mode / f"{pool}-{mode}.json").read_text())
        axes[1].bar(
            index + offset,
            row["total_pss_bytes"] / 2**20,
            0.3,
            color=color,
            label=mode if index == 0 else None,
        )
for axis in axes:
    axis.set_xticks(range(3), pools)
    axis.set_ylabel("MiB")
    axis.legend()
axes[0].set_title("Synthetic-input model profiles")
axes[1].set_title("Loaded weights: parent + 3 idle children")
fig.tight_layout()
fig.savefig(root / "profiles.png", dpi=150)
plt.close(fig)
trace = root.parents[2] / "fault_tests/burst/results/worker-memory.csv"
if trace.exists():
    with trace.open() as file:
        rows = list(csv.DictReader(file))
    # Earlier campaign workers can remain alive in the snapshot until heartbeat expiry.
    latest = {}
    for row in sorted(rows, key=lambda row: float(row["seconds"])):
        latest[row["pool"]] = row["worker_id"]
    rows = [row for row in rows if row["worker_id"] == latest[row["pool"]]]
    fig, axis = plt.subplots(figsize=(9, 4))
    for worker in sorted({row["worker_id"] for row in rows}):
        data = [row for row in rows if row["worker_id"] == worker]
        axis.plot(
            [float(row["seconds"]) for row in data],
            [float(row["memory_percent"]) for row in data],
            label=worker,
        )
    axis.set(
        xlabel="Seconds",
        ylabel="Cgroup memory percent",
        title="Real-model 20-case burst",
    )
    axis.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(root / "burst-memory.png", dpi=150)
    plt.close(fig)
