"""Figura 3 - Comparacion FIFO vs aging por nivel de prioridad.

Fuente de datos:
- experiments/m2_scheduling/results/local/fifo.csv
- experiments/m2_scheduling/results/local/aging.csv

La prioridad no es una columna del CSV: se deriva del indice de la fila con la
regla exacta usada por el cliente de carga del experimento:
priority = ["low", "critical", "medium", "high"][index % 4]
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
FIFO_CSV = REPO_ROOT / "experiments/m2_scheduling/results/local/fifo.csv"
AGING_CSV = REPO_ROOT / "experiments/m2_scheduling/results/local/aging.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig03_fifo_vs_aging.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

PRIORITY_BY_INDEX_MOD_4 = ["low", "critical", "medium", "high"]
PRIORITY_ORDER = ["low", "medium", "high", "critical"]
PRIORITY_LABELS = {"low": "Baja", "medium": "Media", "high": "Alta", "critical": "Crítica"}


def load_mean_latency_by_priority(csv_path: Path) -> pd.Series:
    df = pd.read_csv(csv_path)
    df["priority"] = df["index"].apply(lambda i: PRIORITY_BY_INDEX_MOD_4[i % 4])
    return df.groupby("priority")["seconds"].mean()

fifo_means = load_mean_latency_by_priority(FIFO_CSV)
aging_means = load_mean_latency_by_priority(AGING_CSV)

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

x_positions = range(len(PRIORITY_ORDER))
bar_width = 0.35

fifo_values = [fifo_means[p] for p in PRIORITY_ORDER]
aging_values = [aging_means[p] for p in PRIORITY_ORDER]

ax.bar([x - bar_width / 2 for x in x_positions], fifo_values, width=bar_width, label="FIFO")
ax.bar([x + bar_width / 2 for x in x_positions], aging_values, width=bar_width, label="Aging")

ax.set_xticks(list(x_positions))
ax.set_xticklabels([PRIORITY_LABELS[p] for p in PRIORITY_ORDER])
ax.set_xlabel("Nivel de prioridad")
ax.set_ylabel("Latencia media (s)")
ax.set_title("Latencia media por nivel de prioridad: FIFO vs aging (n=10 por nivel)")
ax.legend(title="Política")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {FIFO_CSV.relative_to(REPO_ROOT)}, {AGING_CSV.relative_to(REPO_ROOT)}")
