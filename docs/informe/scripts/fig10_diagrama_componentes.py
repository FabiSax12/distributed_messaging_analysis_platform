"""Diagrama de componentes generado para este informe a partir del codigo real
(no existe un diagrama de componentes independiente en docs/architecture/; solo
hay un diagrama de despliegue AWS y un flowchart Mermaid de alto nivel).

Fuentes de las rutas y conexiones dibujadas (todas verificadas en el codigo):
- services/coordinator/src/coordinator/{main.py,admission,scheduler,routing,barrier,monitor,api,auth.py}
- services/workers/src/workers/{main.py,ops/analysis/*.py,runner/*.py}
- services/shared/shared/broker.py (exchanges/colas work, results, dead-letter)
- services/coordinator/src/coordinator/db/schema.sql (PostgreSQL, incl. tabla custody)
- services/coordinator/src/coordinator/monitor/notifications.py (SNS)
- frontend/src/auth/cognito.js, frontend/src/api/client.js, frontend/src/useLiveState.jsx
"""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.style.use("seaborn-v0_8-whitegrid")
fig, ax = plt.subplots(figsize=(11, 6.3))
ax.set_xlim(0, 11)
ax.set_ylim(1.5, 7.9)
ax.axis("off")


def box(x, y, w, h, text, color="#dbe9f6", fontsize=9.3):
    p = FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.08",
        linewidth=1.1, edgecolor="#2c3e50", facecolor=color, zorder=2,
    )
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
             fontsize=fontsize, zorder=3)
    return (x, y, w, h)


def arrow(b1, b2, text="", side1="right", side2="left", color="#555555",
          style="-|>", rad=0.08, label_dx=0.0, label_dy=0.12):
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    pts = {
        "right": (x1 + w1, y1 + h1 / 2), "left": (x1, y1 + h1 / 2),
        "top": (x1 + w1 / 2, y1 + h1), "bottom": (x1 + w1 / 2, y1),
    }
    pts2 = {
        "right": (x2 + w2, y2 + h2 / 2), "left": (x2, y2 + h2 / 2),
        "top": (x2 + w2 / 2, y2 + h2), "bottom": (x2 + w2 / 2, y2),
    }
    p1, p2 = pts[side1], pts2[side2]
    a = FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=12,
                         color=color, linewidth=1.1, zorder=1,
                         connectionstyle=f"arc3,rad={rad}")
    ax.add_patch(a)
    if text:
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        ax.text(mx + label_dx, my + label_dy, text, ha="center", va="bottom",
                fontsize=7.2, color="#333333",
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))


ax.text(0.15, 7.75, "Componentes reales del sistema (generado para este informe a partir del codigo)",
        fontsize=10.5, fontweight="bold")

frontend = box(0.3, 6.1, 2.1, 1.3, "Frontend (React + Vite)\nCognito PKCE\nfrontend/src/", "#fdebd0")
api = box(3.1, 6.1, 2.5, 1.3, "Coordinador · API\napi/{cases,uploads,ws,state}.py\nauth.py (JWT + RBAC)", "#d6eaf8")
admission = box(6.1, 6.75, 2.2, 0.65, "admission/\n(M1 reserve())", "#eafaf1", fontsize=8.6)
scheduler = box(8.5, 6.75, 2.2, 0.65, "scheduler/\n(M2 FIFO/aging)", "#eafaf1", fontsize=8.6)
outbox = box(6.1, 6.0, 2.2, 0.65, "routing/outbox.py\n(unico publicador)", "#eafaf1", fontsize=8.0)
barrier = box(8.5, 6.0, 2.2, 0.65, "barrier/\nclose · finalize · sweeper", "#eafaf1", fontsize=8.0)

broker = box(3.1, 4.3, 2.5, 1.1, "RabbitMQ\nexch. work -> work.text/image/audio\ncola results · dead-letter", "#f9e79f", fontsize=8.3)
db = box(0.3, 4.3, 2.1, 1.1, "PostgreSQL\ncases, subtasks, workers,\ncustody (hash-chain)", "#f5cba7", fontsize=8.3)
s3 = box(6.1, 4.3, 2.2, 1.1, "S3 / MinIO\nuploads/, content/<sha256>,\nresults/, metrics/", "#d2b4de", fontsize=8.3)
sns_box = box(8.5, 4.3, 2.2, 1.1, "AWS SNS (correo)\nmonitor/notifications.py", "#f2d7d5", fontsize=8.3)

workers = box(2.0, 2.0, 2.6, 1.3, "Workers (procesos spawn)\ntexto: reglas · imagen: YOLOv8n\naudio: ffmpeg + faster-whisper", "#aed6f1", fontsize=8.6)
monitor = box(5.3, 2.0, 2.6, 1.3, "monitor/\nsnapshot · broadcaster\nbalancer (alertas)", "#eafaf1", fontsize=8.6)

arrow(frontend, api, "HTTPS + JWT /\nWS /api/ws", label_dy=0.18)
arrow(api, admission, "reserve()", side1="top", side2="bottom", label_dy=0.06)
arrow(api, scheduler, "", side1="right", side2="left", rad=0.25)
arrow(api, outbox, "", side1="right", side2="left", rad=-0.05)
arrow(api, barrier, "", side1="right", side2="left", rad=-0.2)
arrow(outbox, broker, "publica a\nwork.<pool>", side1="bottom", side2="top", label_dx=-0.6)
arrow(api, db, "SQL (psycopg3): INSERT\ncaso + subtasks", side1="left", side2="right", label_dy=0.2)
arrow(api, s3, "uploads/ (PUT prefirmado)\nresults/ (reporte)", side1="right", side2="top", rad=0.55, label_dx=2.1, label_dy=-0.15)
arrow(broker, workers, "consume work.<pool>", side1="bottom", side2="top", rad=-0.15, label_dx=-0.9)
arrow(workers, broker, "publica results", side1="top", side2="bottom", rad=0.25, label_dx=0.9, label_dy=-0.55)
arrow(workers, s3, "descarga por chunks\n+ verificacion sha256", side1="right", side2="bottom", rad=-0.2, label_dy=-0.5)
arrow(broker, api, "consumers/results.py\nconsumers/dlq.py", side1="right", side2="bottom", rad=0.3, label_dx=0.5)
arrow(db, monitor, "snapshot\ncada 2s", side1="bottom", side2="left", rad=-0.15, label_dy=-0.45)
arrow(monitor, frontend, "push por WS /\nfallback /api/state", side1="top", side2="left", rad=0.25, label_dx=-1.1, label_dy=0.3)
arrow(monitor, sns_box, "hallazgo severo /\ncaso finalizado", side1="right", side2="left", rad=0.2, label_dy=-0.5)

plt.tight_layout()
out = "docs/informe/figures/fig10_diagrama_componentes.png"
plt.savefig(out, dpi=150)
print("FUENTE: generado para este informe a partir de services/coordinator/src/coordinator/*, "
      "services/workers/src/workers/*, services/shared/shared/broker.py, frontend/src/*")
