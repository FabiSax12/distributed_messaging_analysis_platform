"""Figura 6 - Latencia de Whisper segun configuracion.

Fuente de datos: experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv
Filtrado a filas con op == "audio".
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig06_latencia_whisper.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)
audio = df[df["op"] == "audio"].copy()
audio = audio.sort_values("p50_s")

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

y_positions = range(len(audio))
p50 = audio["p50_s"].to_numpy()
p95 = audio["p95_s"].to_numpy()
# Barra horizontal hasta p50, con un bigote asimetrico hacia p95 (solo hacia
# arriba, ya que p95 >= p50 siempre en estos datos de latencia).
upper_err = p95 - p50
ax.barh(list(y_positions), p50, xerr=[[0] * len(p50), upper_err], capsize=4, color="#55A868")

ax.set_yticks(list(y_positions))
ax.set_yticklabels(audio["config"])
ax.set_xlabel("Latencia (s) — barra = p50, bigote = p95")
ax.set_ylabel("Configuración")
ax.set_title(
    "Latencia de transcripción (Whisper) por configuración CPU\n(hilos, cuantización)",
    fontsize=12,
)
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
