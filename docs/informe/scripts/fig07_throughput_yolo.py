"""Figura 7 - Throughput de YOLO segun numero de hilos.

Fuente de datos: experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv
Filtrado a filas con op == "image". La columna images_per_s trae valores
reales (no vacios), por lo que se usa directamente sin recalcularla.
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig07_throughput_yolo.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(SOURCE_CSV)
image = df[df["op"] == "image"].copy()

if image["images_per_s"].isna().any():
    print("ADVERTENCIA: hay filas image con images_per_s vacío; se omiten del gráfico")
    image = image.dropna(subset=["images_per_s"])

image = image.sort_values("threads")

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
ax.bar(image["threads"].astype(str), image["images_per_s"], color="#C44E52")

ax.set_xlabel("Hilos de CPU")
ax.set_ylabel("Throughput (imágenes/s)")
ax.set_title("Throughput de detección de objetos (YOLOv8n) según hilos de CPU")
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_CSV.relative_to(REPO_ROOT)}")
