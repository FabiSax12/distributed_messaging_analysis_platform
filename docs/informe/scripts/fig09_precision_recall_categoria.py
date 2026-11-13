"""Figura 9 - Precision y recall por categoria de hallazgo.

Fuente de datos: dataset/evaluation/metrics.json, objeto "per_category".
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_JSON = REPO_ROOT / "dataset/evaluation/metrics.json"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig09_precision_recall_categoria.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

metrics = json.loads(SOURCE_JSON.read_text())
per_category = metrics["per_category"]

CATEGORY_ORDER = ["illicit_activity", "offensive_language", "sensitive_image", "threat", "violence"]
CATEGORY_LABELS = {
    "illicit_activity": "Actividad ilícita",
    "offensive_language": "Lenguaje ofensivo",
    "sensitive_image": "Imagen sensible",
    "threat": "Amenaza",
    "violence": "Violencia",
}

missing = sorted(set(CATEGORY_ORDER) - set(per_category.keys()))
if missing:
    print(f"ADVERTENCIA: faltan categorías esperadas en per_category: {missing}")

precision = [per_category[c]["precision"] for c in CATEGORY_ORDER]
recall = [per_category[c]["recall"] for c in CATEGORY_ORDER]

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
x_positions = range(len(CATEGORY_ORDER))
bar_width = 0.35

ax.bar([x - bar_width / 2 for x in x_positions], precision, width=bar_width, label="Precisión")
ax.bar([x + bar_width / 2 for x in x_positions], recall, width=bar_width, label="Recall")

ax.set_xticks(list(x_positions))
ax.set_xticklabels([CATEGORY_LABELS[c] for c in CATEGORY_ORDER], rotation=15, ha="right")
ax.set_xlabel("Categoría de hallazgo")
ax.set_ylabel("Valor (0-1)")
ax.set_ylim(0, 1.05)
ax.set_title("Precisión y recall por categoría de hallazgo (evaluación sobre 505 fuentes etiquetadas)")
ax.legend()
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {SOURCE_JSON.relative_to(REPO_ROOT)}")
