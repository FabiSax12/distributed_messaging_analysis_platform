"""Diagrama de flujo de un caso generado para este informe a partir del codigo
real (no existe ningun diagrama de flujo de caso en docs/architecture/; el
flujo solo esta descrito en prosa en services/coordinator/README.md:114-155 y
en los docstrings del codigo).

Pasos y sus fuentes exactas en el codigo:
1. api/uploads.py (PUT prefirmado a S3 uploads/<ulid>/<name>)
2. api/cases.py:63-97 (head_object, admission.reserve(), INSERT caso+subtasks
   en una transaccion; routing/planner.py:22-53 crea un subtask por archivo)
3. routing/outbox.py:99-127 (publica a exchange work con routing key = pool,
   limitado por OUTBOX_QUEUE_LIMIT; scheduler/__init__.py ordena FIFO/aging)
4. workers/runner/process.py:369-410 (descarga por chunks de 4 MiB, verifica
   sha256, reensambla); ops/analysis/{text,image,audio}.py ejecuta el analisis
   en un proceso hijo spawn aislado (runner/isolation.py)
5. El worker publica ResultMessage a la cola results (shared/broker.py)
6. consumers/results.py + repo/subtasks.py:233-295 (barrera: UPDATE...RETURNING
   que cierra el subtask y decrementa pending_count de forma atomica)
7. barrier/finalize.py:31-73 (si pending_count=0: construye CaseReport,
   lo sube a S3 results/<case>/report.json, calcula estado final)
8. monitor/notifications.py (alerta SNS si hay hallazgo HIGH/CRITICAL o al
   finalizar el caso); monitor/broadcaster.py empuja el nuevo estado por
   WebSocket al frontend
"""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.style.use("seaborn-v0_8-whitegrid")
fig, ax = plt.subplots(figsize=(11, 5.4))
ax.set_xlim(0, 11)
ax.set_ylim(0, 5.3)
ax.axis("off")
ax.text(0.15, 5.1, "Flujo de un caso (generado para este informe a partir del codigo)",
        fontsize=10.5, fontweight="bold")

steps = [
    ("1. Subida", "Cliente sube archivos\nPUT prefirmado -> S3 uploads/\napi/uploads.py", "#fdebd0"),
    ("2. Creacion del caso", "POST /api/cases: valida formato,\nreserva memoria (M1), INSERT\ncaso + subtasks en 1 transaccion\napi/cases.py:63-97", "#d6eaf8"),
    ("3. Enrutado", "Outbox publica a work.<pool>\nsegun tipo de contenido,\norden FIFO o aging (M2)\nrouting/outbox.py", "#eafaf1"),
    ("4. Analisis", "Worker descarga por chunks\n(hash verificado), ejecuta en\nproceso hijo aislado: texto/\nimagen (YOLOv8n)/audio (Whisper)", "#aed6f1"),
    ("5. Resultado", "Worker publica ResultMessage\na la cola results\nshared/broker.py", "#f9e79f"),
    ("6. Barrera", "UPDATE...RETURNING cierra el\nsubtask y decrementa\npending_count (atomico)\nrepo/subtasks.py:233-295", "#f5cba7"),
    ("7. Cierre del caso", "pending_count=0 -> finalize():\nreporte consolidado a S3\nresults/<case>/report.json\nbarrier/finalize.py", "#d2b4de"),
    ("8. Notificacion", "Alerta SNS (hallazgo severo o\ncaso finalizado) + push por\nWebSocket al dashboard", "#f2d7d5"),
]

cols = 4
w, h = 2.35, 1.65
xgap, ygap = 0.3, 0.55
x0, y_top = 0.15, 4.55

boxes = []
for i, (title, body, color) in enumerate(steps):
    col = i % cols
    row = i // cols
    x = x0 + col * (w + xgap)
    y = y_top - row * (h + ygap) - h
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.08",
                        linewidth=1.1, edgecolor="#2c3e50", facecolor=color, zorder=2)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h - 0.18, title, ha="center", va="top", fontsize=9.3,
            fontweight="bold", zorder=3)
    ax.text(x + w / 2, y + h / 2 - 0.18, body, ha="center", va="center", fontsize=7.1, zorder=3)
    boxes.append((x, y, w, h))

for i in range(len(boxes) - 1):
    x1, y1, w1, h1 = boxes[i]
    x2, y2, w2, h2 = boxes[i + 1]
    if i % cols == cols - 1:
        p1 = (x1 + w1 / 2, y1)
        p2 = (x2 + w2 / 2, y2 + h2)
        style = "arc3,rad=-0.5"
    else:
        p1 = (x1 + w1, y1 + h1 / 2)
        p2 = (x2, y2 + h2 / 2)
        style = "arc3,rad=0.0"
    a = FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=13,
                         color="#555555", linewidth=1.2, zorder=1, connectionstyle=style)
    ax.add_patch(a)

plt.tight_layout()
out = "docs/informe/figures/fig11_flujo_de_caso.png"
plt.savefig(out, dpi=150)
print("FUENTE: generado para este informe a partir de services/coordinator/src/coordinator/"
      "{api/cases.py,routing/outbox.py,barrier/*.py,repo/subtasks.py}, "
      "services/workers/src/workers/runner/process.py, services/shared/shared/broker.py")
