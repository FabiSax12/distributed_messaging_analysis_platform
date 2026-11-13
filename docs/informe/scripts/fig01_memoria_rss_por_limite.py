"""Figura 1 - Huella de memoria (RSS) por modelo y limite de cgroup sobrevivido.

Fuente de datos:
- experiments/m1_memory/results/linux/limits.csv
- experiments/m1_memory/results/linux/<pool>/<limit>/<pool>-profile.json
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
LIMITS_CSV = REPO_ROOT / "experiments/m1_memory/results/linux/limits.csv"
RESULTS_DIR = REPO_ROOT / "experiments/m1_memory/results/linux"
OUTPUT_PNG = REPO_ROOT / "docs/informe/figures/fig01_memoria_rss_por_limite.png"

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except (OSError, ValueError):
    pass

df = pd.read_csv(LIMITS_CSV)

# Solo nos interesan las corridas de modo "inference" contra un limite real de
# cgroup (se excluyen las filas "own"/"shared" en 2g, que son una referencia sin
# limite). De esas, solo las que sobrevivieron (oom_killed == False) tienen un
# directorio de perfil.
survivors = df[(df["mode"] == "inference") & (df["oom_killed"] == False)].copy()  # noqa: E712

pools = ["text", "image", "audio"]
records = []
for _, row in survivors.iterrows():
    pool, limit = row["pool"], row["limit"]
    profile_path = RESULTS_DIR / pool / limit / f"{pool}-profile.json"
    if not profile_path.exists():
        print(f"ADVERTENCIA: no existe perfil para {pool}/{limit}: {profile_path}")
        continue
    profile = json.loads(profile_path.read_text())
    rss_mib = profile["loaded"]["rss_bytes"] / 1024**2
    records.append({"pool": pool, "limit": limit, "rss_mib": rss_mib})

data = pd.DataFrame(records)

# Orden de limites por tamano real (bytes) para que las barras dentro de cada
# grupo queden ordenadas de menor a mayor limite de cgroup.
def limit_to_bytes(limit: str) -> int:
    unit = limit[-1].lower()
    value = int(limit[:-1])
    return value * (1024**3 if unit == "g" else 1024**2)

all_limits = sorted(data["limit"].unique(), key=limit_to_bytes)
smallest_surviving = {pool: min(data[data["pool"] == pool]["limit"], key=limit_to_bytes) for pool in pools}

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

n_limits = len(all_limits)
bar_width = 0.8 / n_limits
x_positions = range(len(pools))

for i, limit in enumerate(all_limits):
    heights = []
    for pool in pools:
        row = data[(data["pool"] == pool) & (data["limit"] == limit)]
        heights.append(row["rss_mib"].iloc[0] if not row.empty else 0)
    offsets = [x + (i - n_limits / 2 + 0.5) * bar_width for x in x_positions]
    bars = ax.bar(offsets, heights, width=bar_width, label=f"limite {limit}")
    for x, pool, h in zip(offsets, pools, heights):
        if h > 0 and smallest_surviving[pool] == limit:
            ax.annotate(
                "menor limite\nque sobrevive",
                xy=(x, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=7,
                color="darkred",
            )

ax.set_xticks(list(x_positions))
ax.set_xticklabels(["Texto", "Imagen", "Audio"])
ax.set_xlabel("Modelo / pool")
ax.set_ylabel("RSS cargado (MiB)")
ax.set_title("Huella de memoria (RSS) por modelo y límite de cgroup sobrevivido")
ax.legend(title="Límite de cgroup", fontsize=8)
fig.tight_layout()
fig.savefig(OUTPUT_PNG)

print(f"FUENTE: {LIMITS_CSV.relative_to(REPO_ROOT)}")
