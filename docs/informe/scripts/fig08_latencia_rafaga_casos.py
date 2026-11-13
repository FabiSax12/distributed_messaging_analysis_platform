"""Figura 8 - Distribucion de latencia por caso en la rafaga de 20 casos.

Fuente de datos: experiments/fault_tests/burst/results/real-model-cases.csv
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/fault_tests/burst/results/real-model-cases.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig08_latencia_rafaga_casos.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)

non_completed = df[df["outcome"] != "completed"]
if not non_completed.empty:
    print(f"ADVERTENCIA: {len(non_completed)} filas con outcome != 'completed'")

df = df.sort_values("seconds").reset_index(drop=True)

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
ax.bar(range(len(df)), df["seconds"], color="#8172B2")

mean_val = df["seconds"].mean()
max_val = df["seconds"].max()
max_idx = df["seconds"].idxmax()

ax.axhline(mean_val, color="black", linestyle="--", linewidth=1)
ax.annotate(
    f"media = {mean_val:.1f} s",
    xy=(0, mean_val),
    xytext=(0, 6),
    textcoords="offset points",
    fontsize=9,
)
ax.annotate(
    f"máx = {max_val:.1f} s",
    xy=(max_idx, max_val),
    xytext=(0, 8),
    textcoords="offset points",
    ha="center",
    fontsize=9,
    color="darkred",
)

ax.set_xlabel("Caso (ordenado por latencia)")
ax.set_ylabel("Latencia (s)")
ax.set_title("Latencia por caso en la ráfaga de 20 casos concurrentes (modelos reales, Docker local)")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
