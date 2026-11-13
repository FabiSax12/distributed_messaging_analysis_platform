"""Figura 2 - Uso de memoria por worker durante una rafaga de 20 casos.

Fuente de datos: experiments/fault_tests/burst/results/worker-memory.csv
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/fault_tests/burst/results/worker-memory.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig02_memoria_worker_rafaga.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)

# worker_id "text-Hybrid-Desktop-13075" pertenece a una corrida previa y
# separada (solo 24 filas sueltas, no forma parte de la rafaga de 20 casos
# medida por los otros tres workers). Se excluye explicitamente.
STALE_WORKER = "text-Hybrid-Desktop-13075"
df = df[df["worker_id"] != STALE_WORKER]

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

pool_labels = {"text": "Texto", "image": "Imagen", "audio": "Audio"}
for pool in ["text", "image", "audio"]:
    sub = df[df["pool"] == pool].sort_values("seconds")
    ax.plot(sub["seconds"], sub["memory_percent"], label=pool_labels[pool])

ax.set_xlabel("Tiempo (s)")
ax.set_ylabel("Memoria usada (% del cgroup)")
ax.set_title("Uso de memoria por worker durante una ráfaga de 20 casos (Docker local, cgroup %)")
ax.legend(title="Pool")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
