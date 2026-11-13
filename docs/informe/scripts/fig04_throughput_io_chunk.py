"""Figura 4 - Throughput de E/S segun tamano de chunk.

Fuente de datos: experiments/m3_io_storage/results/throughput.csv
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/m3_io_storage/results/throughput.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig04_throughput_io_chunk.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)

CHUNK_LABELS = {262144: "256 KiB", 4194304: "4 MiB", 16777216: "16 MiB"}
chunk_order = [262144, 4194304, 16777216]

missing = sorted(set(df["chunk_bytes"].unique()) - set(chunk_order))
if missing:
    print(f"ADVERTENCIA: chunk_bytes inesperados en el CSV: {missing}")

stats = df.groupby("chunk_bytes")["mib_s"].agg(["mean", "std"]).reindex(chunk_order)

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
x_positions = range(len(chunk_order))
ax.bar(
    x_positions,
    stats["mean"],
    yerr=stats["std"],
    capsize=6,
    color="#4C72B0",
)
ax.set_xticks(list(x_positions))
ax.set_xticklabels([CHUNK_LABELS[c] for c in chunk_order])
ax.set_xlabel("Tamaño de chunk")
ax.set_ylabel("Throughput (MiB/s)")
ax.set_title("Throughput de E/S (MiB/s) según tamaño de chunk (archivo sintético de 200 MiB)")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
