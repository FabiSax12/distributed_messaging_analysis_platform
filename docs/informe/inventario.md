# Inventario para el informe final (Fase 1)

Estado: **borrador de Fase 1, pendiente de tu revisión**. No se ha redactado nada del informe.

Origen de los datos: lectura del repositorio (código, CSV, JSON, fichas) por cuatro auditorías de solo lectura, el 2026-11-11. Regla aplicada: si la documentación y el código discrepan, manda el código. Las rutas con `:línea` salen del código o del archivo citado. No se modificó nada fuera de `docs/informe/`.

Convención de veredictos: **cumple** / **parcial** / **falta**, contra la columna "Evidencia mínima exigida" de B.5 y las tablas B.3, B.4 y B.6 del enunciado.

Nota sobre los datos "medidos": varias cifras de las fichas (p. ej. M1 en la nube, "370 registros" de custodia en M4) no tienen archivo crudo en el repo. En el informe solo se citarán como "afirmación de la ficha, sin archivo de respaldo", o se marcarán `[FALTA]`.

---

## 1. Mapa de componentes reales

Python es un workspace uv (`pyproject.toml` raíz, miembros: shared, coordinator, workers, storage). No existe código en `tests/`.

### 1.1 Coordinador (`services/coordinator/src/coordinator/`)

| Pieza | Ruta | Qué hace (según código) |
|---|---|---|
| App FastAPI | `main.py` | Lifespan que arranca hilos: `results-consumer-N`, `dlq-consumer`, `outbox-relay`, `sweeper-loop`, `snapshot-loop`, `samples-loop` (`main.py:129-213`). Ejecuta un `run_sweep` sincrónico antes de servir (`main.py:134`). |
| Entrada producción | `serve.py` | uvicorn `workers=1`; certificado autofirmado si `COORDINATOR_TLS` (`serve.py:20-50`, por defecto apagado). |
| Admisión (M1) | `admission/__init__.py:15` `reserve()` | Reserva de memoria y límite de casos activos dentro de la transacción de creación del caso (`api/cases.py:80`). |
| Planificador (M2) | `scheduler/__init__.py` | `priority()` y `ordered()`; FIFO o aging. Selección real en SQL: `repo/subtasks.py:115-138`. |
| Enrutado | `routing/planner.py`, `routing/outbox.py` | `plan_case` (un sub-task por archivo/operación); `OutboxRelay` es el único código que publica trabajo. |
| Barrera / reintentos | `barrier/close.py`, `barrier/finalize.py`, `barrier/sweeper.py` | Política de reintentos, cierre del caso, detección de workers muertos. |
| Consumidores | `consumers/results.py`, `consumers/dlq.py` | Resultados y dead-letter. |
| Persistencia | `repo/{cases,subtasks,workers}.py`, `db/schema.sql` | SQL plano con psycopg 3; el esquema se aplica en cada arranque. Incluye custodia (`schema.sql:77-121`). |
| Reporte | `reports/builder.py` | Construye `CaseReport` y lo escribe a S3 `results/{case}/report.json`. |
| Monitoreo | `monitor/{snapshot,broadcaster,samples,balancer,notifications}.py` | Estado del dashboard, push por WebSocket, muestras de carga, alertas, SNS/CloudWatch. |
| Seguridad | `auth.py`, `security.py` | JWT de Cognito (RS256), roles, middleware `X-Origin-Verify`. |
| API | `api/{cases,uploads,dataset,state,ws,health,admin,deps}.py` | `/api/cases`, `/api/uploads`, `/api/dataset`, `/api/state`, `/api/metrics`, `WS /api/ws`, `/api/me`, `/api/admin/*`, `/healthz`. |
| Scripts | `services/coordinator/scripts/{smoke,check_report,verify_custody}.py` | Humo HTTP, contrato del reporte, verificación de la cadena de custodia. |

### 1.2 Workers (`services/workers/src/workers/`)

- Entrada: `main.py` (`python -m workers.main`). `POOL` elige la cola; `WORKER_CONCURRENCY` (defecto 1, `config.py:61`), prefetch 1 (`main.py:159`), hilo de heartbeat.
- Despacho: `ops/analysis/__init__.py:29-33` (`ANALYSIS_OPERATIONS`); `runner/process.py:309`.
- Cada operación corre en un proceso hijo `spawn` matable (`runner/isolation.py:46-80`).

| Pool | Archivo | Tecnología real |
|---|---|---|
| Texto | `ops/analysis/text.py` | Reglas por regex y léxico bilingüe (`bilingual-rules`, `:224`). **No hay modelo de ML.** |
| Imagen | `ops/analysis/image.py` | YOLOv8n COCO (Ultralytics), `conf=0.25`. Solo `knife` y `scissors` generan hallazgo `SENSITIVE_IMAGE` (`:86`). |
| Audio | `ops/analysis/audio.py` | ffmpeg (mono 16 kHz) + faster-whisper (`tiny`, `int8`, 2 hilos, `beam_size=1`); luego las mismas reglas de texto por segmento. |

### 1.3 Contrato compartido, cola y almacenamiento

- Contrato: `services/shared/shared/{routing,messages,states,models,analysis,api,broker,broker_policy}.py`.
- Broker (RabbitMQ): topología declarada en `shared/broker.py:77-98`: exchange `work` (direct, durable), colas `work.text`, `work.image`, `work.audio` (`x-max-priority=4`, DLX), cola `results`, exchange `dead-letter` (fanout) + DLQ. Mensajes persistentes, confirmaciones del publicador. Política `bounded-work` (`max-length` 1000 por defecto, `reject-publish`) en `shared/broker_policy.py:35-38`, aplicada por `scripts/deploy/remote-node.sh:195`.
- Almacenamiento: S3 solo como almacén de bytes; estado en PostgreSQL. `services/storage/src/storage/{chunking,content_store,backpressure}`. **Solo `chunking` lo importa un servicio** (`workers/runner/process.py:54-55`). El CAS real en producción es un `put_object(IfNoneMatch="*")` directo sobre `content/<sha256>` (`process.py:396-402`). `FileContentStore` y `ThreadBoundedQueue` solo se usan en `check_storage.py` y en el benchmark de M3.

### 1.4 Frontend (`frontend/`)

React 18 + Vite 5, JS plano, rutas por hash. Vistas: resumen (workers, colas, alertas, gráficos de 30 min), lista de casos, detalle (barrera, sub-tasks, cancelar, reporte, vista previa de medios), nuevo caso (desde dataset, subida, o automático por carpeta), usuarios y roles. Auth: Cognito Hosted UI con PKCE (`auth/cognito.js:60-70`). Tiempo real: `WS /api/ws`, con sondeo de respaldo a `/api/state` cada 2 s.

### 1.5 Infraestructura

- `infra/docker/`: `compose.dev.yml` (RabbitMQ, PostgreSQL, MinIO en 127.0.0.1), `compose.coordinator.yml` (`read_only: true`, `cap_drop ALL`), `compose.worker.yml` (`mem_limit`/`memswap_limit` 1g por defecto, `cpus` 2, `pids_limit` 128, `cap_drop ALL`, `no-new-privileges`; **sin `read_only`**), Dockerfiles con usuario no root uid 10001.
- `infra/terraform/modules/`: **13** módulos (cdn, ci_oidc, compute, database, identity, messaging, network, observability, registry, secrets_kms, storage_evidence, storage_frontend, tls_origin). `environments/dev` los cablea; `environments/prod` es plantilla solo validada.
- Despliegue: `scripts/deploy/{build-and-push,deploy-app,remote-node,common,publish-dashboard}.sh`; `.github/workflows/{ci,deploy}.yml` (OIDC).
- Nodos AWS por defecto: coordinador t3.small, 3 workers t3.large, RDS `db.t4g.micro`, Amazon MQ `mq.t3.micro` (`infra/terraform/environments/dev`).
- Directorios que solo tienen `.gitkeep` (marcadores vacíos): `services/alerts/`, `services/coordinator/{admission,api,scheduler,state}/` (nivel superior), `services/security/*`, `services/workers/{common,text,image,audio}/`, `infra/monitoring/*`, `infra/tls/`, `tests/*`, `scripts/{bootstrap,measure}/`.

---

## 2. Módulos M1–M5

Nomenclatura: el enunciado pide `docs/modulo-MX-nombre.md`. Los archivos reales son `docs/module-m1..m5-*.md` (decisión explícita del usuario: todo en inglés salvo los tres docs nombrados por el PDF). Se anotará en el informe.

**Hallazgo transversal: ninguna de las 5 fichas cita bibliografía (autor, capítulo).** El enunciado exige "en qué capítulo de la bibliografía se sustenta". Los repos no contienen ninguna referencia de libro o paper, solo URLs de proyectos (ver sección 7). En el informe irá `[FALTA: referencia]` en cada concepto de SO.

### M1 · Gestión de memoria — responsable: David Molina

- Implementación: `admission/__init__.py:15` (`reserve()`, llamado en `api/cases.py:80`); límites cgroup en `compose.worker.yml:18-21` (texto 256m y resto 1g en `remote-node.sh:123`); métrica cgroup v2 en `workers/runner/memory.py:8-30`; aislamiento por proceso hijo `spawn` (`isolation.py:46-80`); un hijo que muere sin resultado da `WORKER_LOST` "possible OOM" (`isolation.py:74-77`), reintentable (`messages.py:42-44`).
- No hay detección explícita de `OOMKilled` en `services/`; solo `scripts/check_real_pipeline.py:222` lo registra.
- Estrategia de carga de modelos en producción: `@lru_cache` por proceso, pero cada operación lanza un hijo `spawn` nuevo, así que **el modelo se recarga cada vez**. La comparación fork/copy-on-write solo se mide (`measure.py:36-66`); no se usa en producción. mmap: no hay.
- Ficha `docs/module-m1-memory.md` (173 líneas): elementos 1, 3, 4 presentes; 2 sin referencia; **sin sección de interpretación como tal** (hay seis secciones, las interpretaciones van dentro de la 5 y 6). Los comandos documentados (`container_sweep.py`, Dockerfile `madp-m1`) **no son los que produjeron los resultados** committeados (los produjo `measure_images.py`).
- Datos medidos:
  - `experiments/m1_memory/results/native/` (Windows 11, CPython 3.13, 2026-11-10): RSS cargado texto 34 926 592 B, imagen 250 294 272 B, audio 166 043 648 B; PSS nulo.
  - `experiments/m1_memory/results/linux/` (WSL2, Docker local, commit `a7c7148`): `limits.csv` con 21 filas, una corrida por límite; perfiles RSS/PSS por pool y límite; `*-own.json`/`*-shared.json` (propio vs compartido, 2g); `profiles.png`, `burst-memory.png`.
  - Ráfaga con modelos reales: `experiments/fault_tests/burst/results/worker-memory.csv` (483 filas), `real-model-cases.csv` (20 filas), `containers.jsonl`.
- Cifras de las fichas verificadas contra crudos: tablas native, Linux y fork coinciden. **No verificables (sin archivo crudo):** rechazos en la nube (4 365 812 560 B, 33 442 088 184 B, caso P8Y82BM7), la verificación PostgreSQL de 560 870 912 B y "pasaron los chequeos reales de admisión/custodia". "37 de 40 sobre presupuesto / 0 de 40" sí se reprodujo desde `dataset/cases/*/manifest.json`.

| Evidencia mínima B.5 | Veredicto | Motivo |
|---|---|---|
| Tabla huella de memoria por modelo | **cumple** | Ficha M1 §2, líneas 89-93; entradas sintéticas diminutas. |
| Gráfico memoria por worker en ráfaga | **parcial** | `burst-memory.png` + `worker-memory.csv`; métrica en % de cgroup (incluye page cache), Docker local, manifiesto sintético de 3 fuentes; la ficha no lo referencia ni cita cifras. |
| Prueba de OOM provocado y recuperación | **parcial** | OOM reproducido (exit 137, `OOMKilled=True`) y éxito con límite mayor. La ficha reconoce que no es requeue por broker. `memory_saturation/run.py` no tiene resultados. |
| Comparación memoria total entre estrategias | **cumple** | PSS propio vs compartido (texto 103.04/41.70 MiB, imagen 676.81/296.26, audio 466.92/191.94). Solo pesos inactivos, no bajo inferencia. |
| Control de admisión a partir de los datos | **parcial** | El código existe; las bases (512/384/64 MiB) son "estimaciones iniciales" (`handoff-completion.md:130-131`); los chequeos usan SQL simulado. |

Discrepancias M1: nota constante "PSS null on Windows" en los JSON de Linux (`measure.py:123`); anomalía PSS de `text/32m` (25.76 MiB vs RSS 37.09); la README del experimento pide muestreo a 1 s y los datos están a ~0.85 s.

### M2 · Planificación de cargas — responsable en matriz: Fabián Vargas

- Implementación: niveles LOW/MEDIUM/HIGH/CRITICAL (`shared/analysis.py:67-76`, AMQP 1-4 en `shared/routing.py:98-104`); aging: `min(4, base + int(edad/aging_s))`, desempate por `created_at` (`scheduler/__init__.py`); selección en SQL (`repo/subtasks.py:115-138`); el relay solo publica mientras `queue_limit - depth > 0` (`outbox.py:99-127`). Defecto: `SCHEDULER_POLICY=aging`, `SCHEDULER_AGING_S=30`, `OUTBOX_QUEUE_LIMIT=1` (`config.py:81-82,104`).
- Ficha (46 líneas): sin referencia bibliográfica, **sin comandos exactos, sin tabla ni gráfico** (solo prosa), sin rutas de código; contiene "Fabi?n" mal codificado (línea 39).
- Datos: `experiments/m2_scheduling/results/local/{fifo,aging}.csv` (40 filas c/u, columnas `index,case_id,outcome,seconds,detail`) y `method.json`. **No hay columna de prioridad** (se deriva de `index % 4`, `load_client/run.py:128,142`), ni espera/servicio, ni throughput. Máquina y fecha no registradas (commit `a7c7148`, 2026-11-10). Worker simulado de 0.5 s, un solo worker, local.
- Verificación: p95 37.16 (FIFO) y 35.18 (aging) coinciden. Los "p50 nearest-rank" 32.61 y 32.44 en realidad son la mediana (promedio de la 20.ª y 21.ª); nearest-rank daría 32.56 y 32.17.
- Con `SCHEDULER_AGING_S=2` el aging satura casi desde el inicio (LOW llega a prioridad 4 a los 6 s) mientras las latencias rondan 32 s.

| Evidencia mínima B.5 | Veredicto | Motivo |
|---|---|---|
| 4 niveles + algoritmo explícito anti-inanición | **cumple** | Código y ficha §1. |
| Comparar ≥2 políticas | **parcial** | Dos corridas únicas; la propia ficha dice que no establecen superioridad. |
| Bajo flujo sostenido de alta prioridad | **falta** | Las prioridades rotan 25 % cada una; no hay flujo sostenido de alta prioridad. |
| Espera, retorno y throughput por prioridad | **falta** | Solo latencia extremo a extremo por caso; sin columna de prioridad en los CSV. |
| Ausencia de inanición con datos | **parcial** | Los 40 casos completan en ambas políticas y se puede derivar máximo por prioridad (low: 37.16 FIFO, 35.18 aging), pero la ficha no lo presenta. |

### M3 · E/S y almacenamiento — responsable: David Molina

- Implementación: `chunking/file_chunker.py` (`FileChunker.split`, `DiskReassembler.add/finish` con verificación por chunk y global, `:13-108`); tamaño por defecto 4 MiB (`chunking/__init__.py:17-19`); `content_store/file_store.py:29-71` (CAS con `os.link`, dedup); `backpressure/thread_queue.py` (BLOCK/REJECT); en producción: descarga por chunks de 4 MiB con verificación de hash (`process.py:369-410`), subida directa a S3 con URL prefirmada (`api/uploads.py`), endpoints síncronos en threadpool, y contrapresión real por `OUTBOX_QUEUE_LIMIT`, `max-length`+`reject-publish` de RabbitMQ y prefetch 1.
- Ficha (69 líneas): sin referencia bibliográfica; sin rutas de archivos del repo (solo nombres de clases). `measurements.png` solo mencionada, no incrustada.
- Datos en `experiments/m3_io_storage/results/` (Windows 11, CPython 3.13, disco temporal local, caché caliente, fecha 2026-11-10, commit `aae45c9`):
  - `throughput.csv` (9 filas): 256 KiB 257.83 ± 3.68 MiB/s; 4 MiB 215.37 ± 5.87; 16 MiB 186.34 ± 15.16 (3 corridas c/u, archivo sintético de 200 MiB).
  - `dedup.csv`: 10 puts, 2 objetos, 8 aciertos, 175 000 B lógicos / 35 000 B físicos, razón 5.0 (**fuente declarada: "synthetic repeated attachments; not dataset evidence"**).
  - `backpressure.csv`, `queue_samples.csv` (139 filas), `measurements.png`, `machine.json`.
- Coinciden con las tablas de la ficha. Línea 56 de la ficha está obsoleta ("no case manifests/media"): los 40 manifiestos ya existen; solo falta la media.

| Evidencia mínima B.5 | Veredicto | Motivo |
|---|---|---|
| Throughput vs tamaño de chunk (≥3) | **parcial** | 3 tamaños × 3 corridas, pero archivo sintético, disco local Windows, sin red ni S3. |
| Tasa de dedup sobre el dataset | **falta** | Solo el 5.0 sintético. La media del dataset no está en disco (465 de 505 archivos ausentes). |
| Profundidad de cola y tiempos, con y sin backpressure | **parcial** | Gráfico y p50/p95 de unbounded/block/reject, pero con una cola de hilos en proceso (200 × 256 KiB, consumidor de 5 ms), no el coordinador ni RabbitMQ. |
| Justificación del tamaño de chunk | **parcial** | El más rápido medido es 256 KiB; el código usa 4 MiB sin justificación escrita. |
| Reensamblado verificado por hash | **cumple** | Código en `file_chunker.py`. |
| CAS SHA-256 | **cumple (código)** | El CAS de S3 en producción no tiene medición. |
| E/S asíncrona o no bloqueante | **parcial** | URLs prefirmadas directo a S3 + endpoints síncronos en threadpool; sin comparación síncrono/asíncrono. |

### M4 · Protección y seguridad — responsable en matriz: Fabián Vargas

- Implementación:
  - Autenticación y RBAC: `auth.py:12-60` (RS256, `token_use=access`, `client_id`, grupos `analyst/supervisor/administrator`; `DELETE` exige supervisor o administrador; `/api/admin/*` solo administrador); `security.py:44-86`; WebSocket `api/ws.py:50-96`. **Sin `COGNITO_ISSUER_URL` y sin `ORIGIN_VERIFY_SECRET`, el modo local concede rol administrador a cualquiera** (`auth.py:18-23`).
  - Cadena de custodia: tabla `custody` con `hash = sha256(previous || body)`, triggers que impiden UPDATE/DELETE/TRUNCATE (`db/schema.sql:77-121`) sobre `cases` y `subtasks`; verificador `scripts/verify_custody.py` con ancla y `--self-check`. **Registra mutaciones de filas de BD, no hashes de archivos ni de transformaciones**; el SHA-256 de la fuente se verifica en el worker (`process.py:376-384`).
  - TLS: AMQPS verificado (`shared/broker.py:57-64`); RDS con `sslmode=require` (**no verifica certificado**, `remote-node.sh:85-88`); S3 con política que niega no-TLS; TLS del coordinador apagado por defecto.
  - En reposo: KMS CMK con rotación para S3, RDS y secretos (Terraform). Claves/contraseñas generadas en Secrets Manager (y en el estado de Terraform).
  - Sin privilegios: uid 10001, `cap_drop ALL`, `no-new-privileges`; **`read_only` solo en el coordinador, no en los workers**.
- Ficha (53 líneas): sin referencia; método sin comandos exactos; resultados solo en prosa; la interpretación son límites y pendientes, no comparación con la teoría. "Fabi?n" mal codificado (línea 51). Línea 52 obsoleta ("frontend PKCE sigue siendo trabajo de Camila").
- Datos: `experiments/m4_security/results/` solo tiene `.gitkeep`. Solo hay cinco JSON sueltos sin script productor en `docs/load-tests/local-builds/`: `privileges.json` (uid 10001, capacidades efectivas 0, `no_new_privileges` 1), `tls-target.json` (TLSv1.3, 200, rechaza el autofirmado con la confianza por defecto), `custody-head.json` (2976 registros, cabeza `afcbb8a4…57d28`, BD sintética local), `machine.json`, y los tamaños de imágenes. `containers.jsonl` registra `user: "app"`.
- Cifras sin respaldo: "370 registros" (ficha línea 43) no aparece en ningún archivo; los chequeos de roles/manipulación son salida de consola no guardada.

| Evidencia mínima B.5 | Veredicto | Motivo |
|---|---|---|
| Prueba de manipulación detectada | **parcial** | `verify_custody.py --self-check` sintético y `check_postgres.py` (la BD rechaza UPDATE). Sin salida guardada ni prueba de alterar-y-verificar sobre cadena real. |
| Accesos denegados por rol | **parcial** | `check_auth.py` offline asserta 403/401 (salida no guardada). `cloud_probe.py` nunca generó `probes.csv`. |
| Captura de tráfico cifrado entre nodos | **falta** | No hay pcap/tcpdump. Solo `tls-target.json` (handshake TLSv1.3 local). |
| Workers sin root | **cumple** | `privileges.json`, `containers.jsonl`, Dockerfiles. |
| Autenticación + RBAC (3 roles) | **cumple (código + prueba offline)** | Sin login real contra Cognito ni campaña de roles en la nube (ficha líneas 49-50). |
| TLS en tránsito | **parcial** | Ver arriba: RDS no verifica, TLS del coordinador apagado y solo probado local. |
| Cifrado en reposo y claves | **cumple (Terraform)** | Sin artefacto de evidencia en reposo. |
| Cadena de custodia (hash en ingreso y transformaciones) | **parcial** | Log encadenado y solo-anexión implementado; no hashea artefactos de salida ni transformaciones. |
| FS de solo lectura en workers | **falta** | Solo el compose del coordinador lo declara. |

### M5 · Cómputo heterogéneo — responsable: Camila Hidalgo

- Implementación: `experiments/m5_heterogeneous_compute/scripts/{bench,bench_one,plot}.py`; cambio de producción derivado: `ops/analysis/image.py:32-38` (`MODEL_THREADS` controla hilos de YOLO, commit `706331d`); `audio.py:33-35`; `MODEL_THREADS: 2` en `remote-node.sh:105`.
- **La política de routing en el coordinador es solo el mapa estático `OPERATION_POOL`** (`shared/routing.py:80-85`, `planner.py:76`); no hay umbral de duración, ni selector de dispositivo ni pool GPU. La ficha decide "mantener routing estático" (`:178-180`). Todavía no desplegado en AWS (ficha `:253-255`).
- Ficha (262 líneas): **la más completa**. Elementos 1, 3 (sin líneas de comando; están en `experiments/m5_heterogeneous_compute/README.md:36-44`), 4 (tablas y 4 figuras) y 5 presentes; elemento 2 con el concepto (CPU/GPU/NPU, ancho de banda, cuantización) **sin referencia bibliográfica**.
- Datos: `experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/` (fecha 2026-11-11, Windows 11, i5-11320H, 4 núcleos/8 lógicos, 15.7 GB, solo CPU): `runs.csv` (804 filas), `summary.csv` (13), `processes.csv` (13), `parallel.csv` (7), `raw/*.json` (13), `host.json`, 4 figuras PNG.
  - Whisper p50 / audio-s por s / pico MB: tiny-int8-t2 1.3447 s / 24.8617 / 322.76; tiny-float32-t2 1.8288 / 17.0087 / 513.36; base-float32-t4 3.2739 / 10.4565 / 679.50.
  - YOLOv8n p50 (s) / imágenes por s: t1 0.1043 / 9.449; t2 0.0814 / 11.5793; t4 0.0700 / 14.198; t8 0.0744 / 12.7918.
  - Paralelo audio s/s: 1×4 33.35, 2×2 38.1, 4×1 37.02, 1×8 28.11, 2×4 41.63, 4×2 46.38, 8×1 52.66.
- Verificación: todas las cifras de tabla de la ficha coinciden con los CSV. Diferencias menores: RTF de la ficha 0.11/0.028 vs 0.1204/0.0281 agregado (0.1128/0.0285 mediana por corrida); CV "2-19 %, mediana 10 %" vs 2.1-18.7 %, mediana 9.6 % (desv. poblacional); "33.4" vs 33.35. Los precios USD/h de la ficha citan rastreadores de terceros sin archivo en el repo.
- Una sola máquina (laptop), no el t3.large objetivo; el host tuvo dos bugchecks durante la campaña (ficha `:84-90`).

| Evidencia mínima B.5 | Veredicto | Motivo |
|---|---|---|
| ≥2 configuraciones de cómputo | **cumple** | CPU con hilos y cuantización (no hay GPU; la alternativa que permite el enunciado). |
| Tabla latencia, throughput, memoria | **cumple** | Memoria = pico de working set del proceso. |
| Justificación de política de routing con los datos | **parcial** | Justifica "CPU, int8, 2 hilos, escalar por procesos"; el cruce CPU/GPU no está medido y la política implementada es el mapa estático. |
| Conexión con Unidad 1 (CPU/GPU/NPU) | **cumple** | Ficha `:31-52`, `:213-224`; sin bibliografía. |

---

## 3. Escenarios B.6

Orquestación: `experiments/fault_tests/run.py` (envoltorios en `burst/`, `worker_crash/`, `coordinator_restart/`, `cancel_inflight/`, `memory_saturation/`), `services/load_client/run.py`, `services/workers/scripts/local_e2e.py`. **No hay ningún resultado de la nube para ninguno de los cinco escenarios.** `docs/load-tests/README.md` y `experiments/fault_tests/README.md` lo reconocen ("campaña en la nube manual").

| Escenario | Evidencia medida | Entorno | Gráfico | Qué falta |
|---|---|---|---|---|
| 1 Ráfaga (≥20 casos) | `experiments/fault_tests/burst/results/real-model-cases.csv` (20 filas, todas `completed`; latencia mín 16.389, máx 131.628, mediana 82.897, media 79.034 s; p95 nearest-rank 128.122 s, recalculado), `worker-memory.csv` (483 filas, solo memoria %), `containers.jsonl`, `real-model-summary.json` | Docker local en WSL, modelos CPU reales, manifiestos sintéticos de 3 archivos | `m1_memory/results/linux/burst-memory.png` (solo memoria) | **Sin `meta.json` del run de 20 casos: no hay tiempo total ni throughput en archivo** (solo derivable: 20/131.8 s). Sin CPU, sin uso por nodo del coordinador/RabbitMQ/PostgreSQL, sin gráfica de latencia/throughput, sin nube. El CSV incluye un worker extra `text-Hybrid-Desktop-13075` (24 filas) de una campaña anterior, y `containers.jsonl` mezcla 3 corridas. |
| 2 Caída de worker | `experiments/fault_tests/local-results/existing-scenarios.csv`, fila `crash,OK` (transcrita de consola) | Local, operaciones simuladas (`fake_worker.py`), `kill -9` | ninguno | Tiempos de detección y reasignación, intentos, corrida con workers reales o en AWS, métricas y gráficos. |
| 3 Reinicio del coordinador | Misma tabla, fila `restart,OK` | Local, simulado | ninguno | Sin tiempos ni conteos guardados (el script comprueba 9 sub-tasks sin duplicar, pero solo el veredicto quedó), sin nube, sin gráficos. |
| 4 Cancelación en curso | `local-results/cancellation.csv`, fila `cancel_inflight,OK` | Local, operación simulada `sleep` de 30 s | ninguno | **Ninguna medición de recursos liberados** (memoria, procesos, temporales) ni latencia de cancelación; no es un caso pesado real. |
| 5 Saturación de memoria | Solo el barrido OOM de M1 (`limits.csv`) y chequeos de admisión con SQL simulado | Local | ninguno | **No hay corrida donde la carga supere la memoria del nodo con rechazos de admisión registrados**, ni comparación con admisión desactivada. `memory_saturation/run.py` solo hace `docker inspect`. |

Evidencia suelta de nube (no es B.6): `docs/handoff-completion.md:47-62` anota dos casos reales (GFVN309Z: 3/3 en 4.0 s; P8Y82BM7: 6/6 en 12.0 s), sin archivo crudo.

Los seis veredictos de `local-results/existing-scenarios.csv` (basic, unsupported, retries, crash, restart, dbdown) son todos `OK` y solo contienen la frase "Transcribed from successful local_e2e console output before CSV logging was added".

---

## 4. Estado de los documentos exigidos

| Documento | Estado | Notas |
|---|---|---|
| `README.md` (75 líneas, inglés) | Casi completo | Descripción + imagen (`ArchitectureAWS.jpg`), requisitos, despliegue, mapa del repo con rutas existentes. Obsoleto: líneas 19-22 (frontend/dataset/M5 "de Camila"), línea 74 (`tests/` "Unit and integration tests", está vacío). |
| `docs/reutilizacion.md` (110 líneas) | Completo en estructura | Reutiliza (11 filas), modifica (15), nuevo (11), limitaciones del Proyecto I corregidas (6). Pendientes: commit del Proyecto I "sin resolver" (línea 5: `de2c96c`), "instructor feedback" no suministrado (línea 98), ruta heredada `docs/arquitectura/` (línea 7), línea 83 repite el mínimo (20 000) en vez del real (21 000). |
| `docs/dataset.md` (335 líneas) | Completo | Mínimos, formato, procedencia y licencias, evaluación. Obsoleto: líneas 49-52 sobre `upload-dataset.sh`. |
| `docs/responsabilidades.md` (48 líneas) | Completo | Matriz copiada en el anexo. Faltan nombres completos y carnés (ver sección 9, hueco 17). |
| `docs/bitacora_ia.md` (93 líneas) | Completo en contenido | Línea 3 es una instrucción de plantilla que quedó. 3 integrantes: David (Codex; 5 filas + tabla de 4 errores + narrativa), Fabián (Claude Code; 14 filas), Camila (Claude Code Opus 5.5; 13 filas). Más de dos errores de IA documentados. **No hay fila etiquetada para M2 ni para M4**; M2 solo se menciona en la línea 26. |
| Documento de arquitectura | Parcial | `docs/architecture/current.md` (Mermaid de componentes, 30 líneas), `ArchitectureAWS.jpg/.drawio` (despliegue AWS, es el que incrusta el README), `diagram.png/.drawio` (obsoleto: dice "spaCy"). **No existe un diagrama del flujo de un caso** (solo prosa en `services/coordinator/README.md:114-155`). No hay diagrama de componentes independiente del Mermaid. |
| Cinco fichas `docs/module-m*.md` | Existen | Ver sección 2: ninguna con bibliografía; M2 y M4 sin tabla/gráfico; M1 sin sección de interpretación nominal. |

Las etiquetas del `ArchitectureAWS.jpg` que **no tienen respaldo en el código**: "chunks AES-GCM" (no hay AES en el código), "FS solo lectura" para workers (solo el coordinador), "Correo / Slack" (solo existe suscripción de correo SNS), "S3 evidencias chunks por SHA-256" (los objetos son archivos completos `content/<sha256>`).

---

## 5. Dataset frente a B.3

La media (imágenes, audios, videos) **no está en disco ni en git ni publicada en un bucket** (`dataset/.gitignore`; `handoff-completion.md:60-62`). Solo están versionados 40 manifiestos, 40 archivos de ground truth, 40 exportaciones de chat (29 WhatsApp `.txt`, 11 Telegram `.json`) y los generadores. Todo lo de media abajo es **declarado por los manifiestos**, no verificado en disco.

| Mínimo B.3 | Real | Fuente | Veredicto |
|---|---|---|---|
| 40 casos | 40 | `dataset/cases/*/manifest.json` | **cumple** |
| ≥10 heterogéneos | 15 (10 con video pequeño + 5 pesados) | manifiestos; `docs/dataset.md:228` | **cumple** |
| 20 000 mensajes de texto | 21 000 de usuario + 337 de sistema = 21 337 (contados por parseo de los chats versionados) | `dataset/cases/*/chat/*` | **cumple** (verificado) |
| 300 imágenes con proporción positiva conocida | 300 declaradas; 73 positivas / 227 negativas (≈24.3 %); positivas = solo cuchillo/tijeras de COCO | manifiestos + ground truth | **cumple (declarado)** |
| 150 audios de distinta duración | 150 declarados; 3.8 s a 172.3 s (notas de manifiesto), suma 73.9 min | manifiestos; `docs/dataset.md:231` (3.9-172.3 s, 74 min por ffprobe) | **cumple (declarado)** |
| ≥5 casos ≥200 MB | 5 videos: 362 217 118, 408 513 738, 343 374 671, 264 284 958, 269 614 277 B (casos 011-015) | `size_bytes` de manifiestos | **cumple (declarado)** |
| Ground truth | 40 archivos, 505 fuentes etiquetadas | `dataset/ground_truth/` | **cumple** |
| Procedencia y licencias | Declaradas por fuente | `docs/dataset.md:280-287` | **cumple** |

Total declarado de fuentes: 505 (texto 40, imagen 300, audio 150, video 15), ~1.7 GB (1 715 067 965 B). Diferencia de contabilidad: bins de duración de audio por notas del manifiesto (73/56/21) vs `dataset.md:231` (74/55/21); no se reconcilió porque exige la media.

Procedencia: texto = síntesis por plantillas (`generate_dataset.py` + `corpus.py`, semilla 42, bancos de frases escritos con ayuda de IA); imágenes = COCO 2017 val (licencias CC BY, CC BY-SA, CC BY-ND, "sin restricciones conocidas"; 167/84/48/1 entre las 300 usadas, calculado desde los manifiestos; el doc dice 174/87/48/1 sobre las 310 seleccionadas); audio = TTS de Windows SAPI (David/Zira, inglés) o espeak-ng en Linux; videos = `testsrc2`/`noise` de ffmpeg con la pista de audio TTS.

Evaluación (`dataset/evaluation/metrics.json`, `dataset/generators/evaluate.py`, ejecutada el 2026-11-11 sobre 505 fuentes; **no se registra la máquina**): por categoría, precisión/recall/F1 — threat 0.884/0.688/0.774, offensive_language 0.804/0.741/0.771, violence 0.657/0.657/0.657, illicit_activity 0.614/0.614/0.614, sensitive_image 0.697/0.172/0.275 (a nivel de imagen: TP 27, FP 1, FN 46, TN 226; precisión 0.964, recall 0.370). IoU medio de cajas emparejadas 0.845. Sin errores de operación.

---

## 6. Discrepancias entre documentación y código

Prevalece el código. (D = discrepancia.)

**Estado de entrega obsoleto**
- D1. `README.md:19-22`, `docs/handoff-completion.md:96,138-149`, `docs/handoff-camila.md:22,85,112`, `docs/frontend-contract.md:1`, `docs/report/README.md:3-4,23,35-38`, `docs/module-m3-io-storage.md:69` y `docs/module-m4-security.md:52` dicen que el login del frontend, el dataset y M5 no están entregados. El repo tiene `frontend/src/auth/*`, `dataset/` con 40 casos y `experiments/m5_heterogeneous_compute/`. `docs/responsabilidades.md:36-48` sí los marca entregados.
- D2. `docs/module-m3-io-storage.md:56`: dice que no hay manifiestos; los 40 existen (falta la media).

**Descripciones erradas del código**
- D3. `services/coordinator/README.md:174`: `OUTBOX_POLL_S` = 1; el código dice 0.5 (`config.py:103`).
- D4. `services/coordinator/README.md:229-230` y `models.py:253-255`: dos prefijos reservados; el código tiene tres (`results/`, `metrics/`, `content/`, `models.py:258`).
- D5. `services/workers/README.md:105-128`: usa `--extra text`; `workers/pyproject.toml` solo define `image` y `audio`.
- D6. `docs/deployment.md:115`: CI corre en PR y pushes a ramas no-main; `ci.yml:5-7` solo tiene `pull_request` y `workflow_dispatch`.
- D7. `docs/deployment.md:205-211`: la comprobación usa `/api/state`; `deploy.yml:177` usa `/api/healthz`.
- D8. `infra/terraform/README.md:89`: dice que hay reescritura 403/404 → `/index.html`; `modules/cdn/main.tf:81` y `handoff-completion.md:85-86` dicen que no.
- D9. `frontend/README.md:98` y `api/client.js:121`: PUT prefirmados SigV2; `aws.py:26` usa `s3v4`.
- D10. `frontend/src/views/CaseDetail.jsx:32` menciona "DynamoDB throttling"; el backend es PostgreSQL.
- D11. `monitor/balancer.py:5` dice que las alertas severas por SNS son "un cambio posterior"; `notifications.py:115-146` ya las publica.
- D12. `shared/api.py:6-14` encabezado "Planned endpoints", omite `/api/ws`, `/api/metrics`, `/api/me`, `/api/admin/*`.
- D13. `services/storage/README.md:3-5,26` llama a los componentes independientes ("a conectar tras la migración"); el worker ya importa `storage.chunking`. `FileContentStore` y `ThreadBoundedQueue` no se usan en servicios, y el CAS de producción es un `put_object` directo. `README.md:68` ("integrated in worker download") solo es cierto para chunking.
- D14. `CLAUDE.md` y `infra/terraform/README.md:15`: 12 módulos; hay 13 (`tls_origin` sin README/variables/outputs).
- D15. `CLAUDE.md`: "`infra/monitoring` e `infra/tls` ahora existen"; solo contienen `.gitkeep` (el monitoreo está en `modules/observability` y TLS en `modules/tls_origin` y `serve.py`). `docs/responsabilidades.md:11` lista `infra/tls/` como ruta de M4.
- D16. `CLAUDE.md` "Planned layout" apunta a directorios vacíos; el código real está en `src/coordinator/*` y `src/workers/{runner,ops}`.
- D17. `CLAUDE.md` lista cinco escenarios de `local_e2e.py`; hay siete (`dbdown`, `cancel_inflight`, `local_e2e.py:468`).
- D18. `README.md:74`: `tests/` con pruebas; solo hay `.gitkeep`.
- D19. `README.md:63` y `:73`: `infra/docker/` descrita solo como stack local (también tiene Dockerfiles y composes de producción); `scripts/` lista bootstrap/measure, vacíos.

**Capacidades que el material afirma y el código no respalda**
- D20. `ArchitectureAWS.jpg`/`.drawio`: "chunks AES-GCM", "FS solo lectura" en workers, "Correo / Slack", "chunks por SHA-256" (ver sección 4).
- D21. `diagram.png`: "spaCy" en el nodo de texto (el texto usa reglas); coloca el coordinador en "VPC privada" (el código lo pone en subred pública, `compute/main.tf:195`).
- D22. M5: la ficha habla de "política de routing"; el coordinador solo tiene el mapa estático de operación a pool.
- D23. M1: no hay detección explícita de `OOMKilled` en `services/`; un hijo muerto se trata como `WORKER_LOST`.
- D24. Descripción de la analítica: los "temas" del texto son solo los nombres de las categorías de regla coincidentes; no hay clasificador temático. En imagen solo `knife`/`scissors` producen hallazgo. No existe una estructura de "incidencias" separada en el reporte (los fallos van como `error` por sub-task y en el resumen).

**Inconsistencias de las fichas con sus datos**
- D25. M1: los comandos documentados (`container_sweep.py`, Dockerfile `madp-m1`) no son los que produjeron los resultados (`measure_images.py` sobre imágenes `madp-worker-<pool>:handoff`).
- D26. M1: nota constante "PSS null on Windows" en los JSON de Linux (`measure.py:123`).
- D27. M2: "p50 nearest-rank" calculado como mediana.
- D28. M4: "370 registros" sin respaldo; el único dato es 2976.
- D29. Codificación "Fabi?n" en `module-m2-scheduling.md:39` y `module-m4-security.md:51`.
- D30. `docs/bitacora_ia.md:3` es texto de plantilla sin eliminar; en `:65` la etiqueta "AI error: instance bootstrap" no coincide con su contenido (el permiso de ECR está en `:64`).
- D31. `experiments/fault_tests/README.md` menciona `burst/results/cases.csv`, que no existe; los archivos son `real-model-*.csv`.

**Despliegue / configuración**
- D32. `RETRY_*`, `HEARTBEAT_TIMEOUT_S`, `SWEEP_INTERVAL_S` y `OUTBOX_POLL_S` no están en la lista permitida de `remote-node.sh:98-103`: no se pueden fijar en despliegues a la nube.
- D33. Límite de memoria del worker de texto: 256m en `remote-node.sh:123`, 1g en `compose.worker.yml:18`; `deployment.md` no lo dice.
- D34. El despliegue aborta si faltan variables de Cognito/`ALERTS_TOPIC_ARN` (`remote-node.sh:94-96`); `deployment.md` no lo menciona.

---

## 7. Referencias bibliográficas que ya existen en el repo

Solo existen estas; **no hay ningún libro, capítulo ni paper**. Para los conceptos de SO el informe llevará `[FALTA: referencia]`.

- `docs/reutilizacion.md:5`: Proyecto I, https://github.com/FabiSax12/distributed-multimedia-processing (commit `de2c96c`, sin resolver).
- `docs/dataset.md:285`: COCO 2017, https://cocodataset.org (licencias CC BY 2.0, CC BY-SA 2.0, CC BY-ND 2.0; anotaciones CC BY 4.0).
- `services/workers/README.md:217`: Ultralytics prediction, https://docs.ultralytics.com/modes/predict/.
- `services/workers/README.md:218`: faster-whisper, https://github.com/SYSTRAN/faster-whisper.
- `services/workers/README.md:219-220`: nota de licencias (YOLO AGPL/enterprise; Whisper y faster-whisper MIT).
- `dataset/generators/data/coco_selection.json`: campo `source` y URLs de licencia por imagen (archivo de datos, no un doc).
- `docs/handoff-camila.md:7`: PR #6 del repo (no es una referencia bibliográfica).

---

## 8. Mapa de requisitos de B.4 frente al código (insumo de la sección 4 del informe)

| Requisito | Veredicto | Mecanismo real (resumen) |
|---|---|---|
| Recepción | **cumple, "múltiples fuentes" parcial** | `POST /api/uploads` (PUT prefirmado) + `GET /api/dataset` + `POST /api/cases` (1 a 1000 archivos, `shared/api.py:60`). Validación de tipo por extensión (`routing.py:39,124`) y `head_object` en la creación; extensión desconocida = sub-task nacida `failed/unsupported_format`. Contenido validado en el worker. `CaseSource` solo `manual` y `auto` (`models.py:46`). Límite de tamaño apagado por defecto (`M1_MAX_CASE_INPUT_BYTES=0`). |
| Cola y routing | **cumple** | Un sub-task por archivo; operación → pool (`routing.py:81`); publicación por exchange `work` con clave = pool (`outbox.py:113`), limitada por `OUTBOX_QUEUE_LIMIT`, orden FIFO/aging en SQL. |
| Workers texto/imagen/audio | **cumple con límites** | Ver 1.2. Confianza de texto fija 0.65 (`text.py:37-58,90`). |
| Estados y barrera | **cumple** | Sub-task: pending, assigned, running, retrying, completed, failed, cancelled; caso: queued, processing, retrying, completed, partially_completed, failed, cancelled (`states.py:17,56`). Barrera: una sola sentencia CTE que cierra el sub-task y decrementa `pending_count` (`subtasks.py:233-295`); a cero, `finalize` idempotente (`finalize.py:31-73`). |
| Reintentos | **cumple** | `delay = min(RETRY_MAX_DELAY_S, RETRY_BASE_DELAY_S × 2^(attempt-1))`; defectos `RETRY_MAX_ATTEMPTS=3` (intentos totales), base 2.0 s, máx 60.0 s, sin jitter (`config.py:91-93,115-117`). Reintenta `internal`, `external_api_error`, `worker_lost` (`messages.py:42`). |
| Cancelación real | **cumple en el worker; sin purga del lado del coordinador** | `DELETE` solo marca `cancel_requested` (`cases.py:63-85`). El worker comprueba antes, durante (consulta a PostgreSQL cada 0.25 s, `isolation.py:57,61`) y después de la operación; `_stop` mata hijos y descendientes con psutil y borra el `TemporaryDirectory` (`isolation.py:31-43`, `process.py:315`). Huecos: la descarga de S3 no se interrumpe (`workers/README.md:177`); sub-tasks en cola o `retrying` solo se cancelan cuando un worker de ese pool los consume. |
| Reporte consolidado | **cumple con límites** | `reports/builder.py`: hallazgos, categorías (conteos), línea temporal, grupos, sub-tasks, workers usados. Evidencia con `source_key`, `message_index`, `sender`, `timestamp`, `excerpt`, `audio_start_s/end_s`, `bbox`, sha256 (`analysis.py:152-174`). Hallazgos de imagen/audio con `time: null`. Sin estructura de "incidencias". |
| Alertas | **cumple (solo correo), mejor esfuerzo** | SNS: hallazgo HIGH/CRITICAL (una vez por caso por proceso), caso finalizado (una vez), alertas operativas (máx. cada 300 s por pool/nivel) (`notifications.py:30-146`). No-op si falta `ALERTS_TOPIC_ARN`. La suscripción de SNS estaba `PendingConfirmation` (`handoff-completion.md:54-55`). |
| Dashboard | **parcial: sin GPU** | Snapshot cada 2 s, push por WS solo si cambia el contenido. Workers con `cpu_percent` (de todo el sistema), `mem_percent` (cgroup), slots, vivos; colas; alertas (`balancer.py:14-110`). Historial ~2 h a 5 s, volcado a S3 cada 60 s. GPU: no encontrada en `services/`, `frontend/` ni `infra/`. |
| Recuperación tras reinicio | **cumple** | Estado en PostgreSQL; el relay republica lo `enqueued=false`; sweeper al arrancar finaliza casos con `pending_count=0`. Se pierden en memoria: deduplicado de alertas, buffer de muestras. |
| Detección de caída de worker | **cumple** | (1) redelivery del broker → `worker_lost` sin ejecutar (`process.py:133,166-180`); (2) heartbeat cada 5 s, sweeper cada 5 s, muerto si `last_seen` > 60 s, sintetiza `worker_lost` para sus sub-tasks (`sweeper.py:49-72`). |

---

## 9. Lista final de huecos

Datos o evidencias que faltan para que el informe esté completo. Cada uno se marcará `[FALTA: …]` en el informe, sin estimar.

**Evidencia medida**
1. B.6 en la nube: ningún escenario tiene datos de AWS (cinco huecos).
2. B.6-1 ráfaga: no hay tiempo total ni throughput guardados para los 20 casos; no hay CPU por nodo, ni uso del coordinador/RabbitMQ/PostgreSQL, ni gráfica de latencia/throughput.
3. B.6-2 y B.6-3: solo veredictos `OK` transcritos; sin tiempos, sin conteos guardados, sin gráficos.
4. B.6-4: ninguna medición de recursos liberados tras cancelar (memoria, procesos, temporales).
5. B.6-5: ninguna corrida de saturación con admisión que rechace; sin comparación con admisión apagada.
6. M1: ráfaga de memoria en bytes/en nube; OOM con requeue por broker; cifras de nube sin archivo crudo; calibración de las bases de admisión.
7. M2: flujo sostenido de alta prioridad; espera/retorno/throughput por prioridad; columna de prioridad en los CSV; repeticiones; máquina y fecha; gráfico; corrida con el workload del dataset o en la nube.
8. M3: dedup sobre el dataset real; throughput con media del dataset (≥200 MB) o por red/S3; con/sin backpressure sobre el sistema real; justificación del chunk de 4 MiB.
9. M4: capturas de tráfico cifrado; salidas guardadas de las pruebas de manipulación y de denegación por rol; `probes.csv` de `cloud_probe.py`; `read_only` en workers; hash de transformaciones; origen de "370 registros".
10. M5: ninguna medición con GPU ni en el t3.large objetivo; política de routing implementada (solo mapa estático); despliegue de `MODEL_THREADS` a AWS sin verificar.
11. Dataset: media (imágenes, audios, videos) no disponible localmente ni publicada: no se puede verificar en disco ni ejecutar M3 sobre el dataset; máquina de `evaluate.py` no registrada.

**Documentación**
12. Referencia bibliográfica (autor y capítulo) para cada uno de los cinco conceptos de SO.
13. Diagrama de flujo de un caso y diagrama de componentes independiente; el JPG de despliegue contiene etiquetas sin respaldo (D20).
14. Commit/tag del Proyecto I y retroalimentación del instructor (`reutilizacion.md:5,98`).
15. Fichas M2 y M4 sin tabla/gráfico; M1 sin sección de interpretación nominal; M2/M4 sin entrada propia en la bitácora de IA.
16. Distribución real de 3 nodos físicos/separados con red real: el código y Terraform lo respaldan (3 EC2 de workers), pero no hay evidencia de una ejecución de la nube con la ráfaga de 20 casos.

**Datos de portada**
17. Nombres completos y carnés de David y Camila (Fabián aparece en git como "Fabián Ricardo Vargas Araya"), grupo, profesor, convención de nombre del PDF del curso y fecha de entrega (ver preguntas).

---

## 10. Matriz de trazabilidad inicial (insumos para `trazabilidad.md`)

Se llenará en la Fase 2 una fila por cifra citada en el informe. Las fuentes ya identificadas están referenciadas en las secciones 2, 3 y 5 de este inventario.
