"""Figura 5 - Profundidad de cola con y sin contrapresion.

Fuente de datos: experiments/m3_io_storage/results/queue_samples.csv
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/m3_io_storage/results/queue_samples.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig05_profundidad_cola.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)

POLICY_LABELS = {"unbounded": "Sin contrapresión", "block": "Bloqueo", "reject": "Rechazo"}
policy_order = ["unbounded", "block", "reject"]

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
for policy in policy_order:
    sub = df[df["policy"] == policy].sort_values("seconds")
    ax.plot(sub["seconds"], sub["depth"], label=POLICY_LABELS[policy])

ax.set_xlabel("Tiempo (s)")
ax.set_ylabel("Profundidad de la cola (mensajes)")
ax.set_title("Profundidad de la cola en ráfaga: sin contrapresión, bloqueo y rechazo")
ax.legend(title="Política")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
