```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

## Anexos

### Anexo A — Matriz de responsabilidades

Tomada de `docs/responsabilidades.md`. El trabajo se repartió por carga: Fabián asume el trabajo más pesado y dependiente de la nube (lo que permite empezar de inmediato), David la segunda carga, Camila la carga más liviana. Cada integrante es responsable principal de al menos un módulo de sistemas operativos (B.5).

| Tema / módulo | Responsable principal | Apoyo | Ruta en el repositorio | Ficha de evidencia |
|---|---|---|---|---|
| Migración a RabbitMQ + PostgreSQL (coordinador, workers, contrato compartido) | Fabián Vargas | David Molina | `services/coordinator/`, `services/workers/`, `services/shared/` | — |
| Infraestructura Terraform | Fabián Vargas | — | `infra/terraform/` | — |
| Despliegue (imágenes Docker, Compose por nodo, scripts SSM) | Fabián Vargas | David Molina | `infra/docker/`, `scripts/deploy/` | — |
| M2 Planificación (prioridades, aging, experimento de política) | Fabián Vargas | David Molina | `services/coordinator/src/coordinator/scheduler/`, `experiments/m2_scheduling/` | `docs/module-m2-scheduling.md` |
| M4 Seguridad (RBAC de Cognito, TLS, KMS, bitácora de custodia, workers sin root) | Fabián Vargas | David Molina, Camila Hidalgo | `services/coordinator/src/coordinator/` (`auth.py`, `security.py`), `infra/tls/`, `experiments/m4_security/` | `docs/module-m4-security.md` |
| Pruebas de carga y tolerancia a fallos (B.6) | Fabián Vargas | David Molina | `experiments/fault_tests/`, `services/load_client/` | Este informe |
| Workers de análisis (texto, imagen, audio) | David Molina | Camila Hidalgo | `services/workers/` | — |
| M1 Memoria (límites de cgroup, RSS/PSS, control de admisión, requeue por OOM) | David Molina | Fabián Vargas | `services/coordinator/src/coordinator/admission/`, `experiments/m1_memory/` | `docs/module-m1-memory.md` |
| M3 E/S y almacenamiento (chunks, CAS SHA-256, dedup, contrapresión) | David Molina | Fabián Vargas | `services/storage/`, `experiments/m3_io_storage/` | `docs/module-m3-io-storage.md` |
| M5 Cómputo heterogéneo (CPU vs. GPU / hilos vs. cuantización) | Camila Hidalgo | David Molina | `experiments/m5_heterogeneous_compute/` | `docs/module-m5-heterogeneous-compute.md` |
| Dataset (generadores, fuentes públicas, ground truth) | Camila Hidalgo | David Molina | `dataset/`, `scripts/dataset/`, `docs/dataset.md` | `docs/dataset.md` (resultados de evaluación) |
| Frontend (dashboard, login Cognito, vistas por rol) | Camila Hidalgo | Fabián Vargas | `frontend/` | `frontend/README.md` (registro de verificación) |
| Bitácora de uso de IA | Todos | — | `docs/bitacora_ia.md` | — |

Nota de autoría de código vs. responsabilidad de módulo (de `docs/responsabilidades.md`): David implementó en una sola PR de cierre (`a7c7148`) gran parte del backend, workers, almacenamiento, infraestructura, experimentos y cambios de despliegue/documentación restantes, incluida la primera implementación del planificador de M2 y su comparación FIFO/aging local, y los chequeos de JWT/RBAC de M4; por eso aparece como autor de código en módulos cuyo responsable principal es Fabián. Fabián realizó el `terraform apply`, la configuración de variables del repositorio, el ciclo de vida de instancias, los usuarios de Cognito, los experimentos/capturas en la nube y la recolección de retroalimentación del instructor, y revisó y fusionó cada PR. La responsabilidad de módulo de la tabla se mantiene para efectos de presentación e interpretación de evidencia; la autoría de código sigue los commits reales.

### Anexo B — Fichas completas de los módulos

El capítulo 5 de este informe sintetiza los cinco elementos exigidos por B.5 (qué se implementó, concepto de SO, método de medición, resultados, interpretación) para cada módulo a partir de las fichas originales y de una verificación directa contra el código. Las fichas completas, con su texto íntegro, quedan en el repositorio y no se reproducen aquí para no duplicar contenido ya cubierto en el cuerpo:

- `docs/module-m1-memory.md` (173 líneas)
- `docs/module-m2-scheduling.md` (46 líneas)
- `docs/module-m3-io-storage.md` (69 líneas)
- `docs/module-m4-security.md` (53 líneas)
- `docs/module-m5-heterogeneous-compute.md` (262 líneas, la más completa de las cinco)

Ninguna de las cinco cita una referencia bibliográfica con autor y capítulo (ver capítulo 5 y "Referencias" del cuerpo). Las fichas de M2 y M4 no incluyen tabla ni gráfico propios; la ficha de M1 no tiene una sección de interpretación nominal (sus interpretaciones están distribuidas dentro de las secciones 5 y 6 del documento).

### Anexo C — Comandos de reproducción

Tomados de `CLAUDE.md` y de los README de cada experimento. Requieren el workspace de `uv` sincronizado (`uv sync --all-packages --group dataset`, desde la raíz del repositorio).

**Pipeline end-to-end local**

```bash
docker compose -f infra/docker/compose.dev.yml up -d --wait
set -a; source infra/docker/dev.env; set +a
uv run --all-packages python services/workers/scripts/local_e2e.py
docker compose -f infra/docker/compose.dev.yml down -v
```

**Validación del dataset**

```bash
uv run --group dataset python dataset/generators/validate_manifests.py --verify-files
```

**Evaluación del dataset (Figura 4, capítulo 3)**

```bash
uv run --group dataset python dataset/generators/evaluate.py
```

**M1 — memoria** (`experiments/m1_memory/`)

```bash
uv run python experiments/m1_memory/scripts/measure.py       # perfiles nativos
uv run python experiments/m1_memory/scripts/measure_images.py  # Linux/Docker, barrido de límites
```

**M2 — planificación** (`experiments/m2_scheduling/`)

```bash
uv run python services/load_client/run.py --policy fifo
uv run python services/load_client/run.py --policy aging
```

**M3 — E/S y almacenamiento** (`experiments/m3_io_storage/`)

```bash
uv run python experiments/m3_io_storage/scripts/bench_throughput.py
uv run python experiments/m3_io_storage/scripts/bench_backpressure.py
```

**M4 — seguridad** (`experiments/m4_security/`, `docs/load-tests/`)

```bash
uv run python services/coordinator/scripts/verify_custody.py --self-check
uv run python services/coordinator/scripts/check_auth.py
```

**M5 — cómputo heterogéneo** (`experiments/m5_heterogeneous_compute/`)

```bash
uv run python experiments/m5_heterogeneous_compute/scripts/bench.py
uv run python experiments/m5_heterogeneous_compute/scripts/plot.py
```

**Escenarios de carga y tolerancia a fallos (B.6, capítulo 6)**

```bash
uv run python experiments/fault_tests/run.py --scenario burst
uv run python experiments/fault_tests/run.py --scenario worker_crash
uv run python experiments/fault_tests/run.py --scenario coordinator_restart
uv run python experiments/fault_tests/run.py --scenario cancel_inflight
uv run python experiments/fault_tests/run.py --scenario memory_saturation
```

**Regeneración de las figuras de este informe**

```bash
uv run --with matplotlib --with pandas --with numpy python docs/informe/scripts/fig01_memoria_rss_por_limite.py
# ... análogo para fig02 a fig11 en docs/informe/scripts/
```

### Anexo D — Tabla de trazabilidad

Cada cifra cuantitativa citada en el cuerpo de este informe tiene su fila correspondiente (valor, sección, archivo y línea de origen) en `docs/informe/trazabilidad.md`, entregado junto con este documento.
