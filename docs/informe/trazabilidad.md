# Matriz de trazabilidad

Una fila por cifra cuantitativa citada en `informe.md`. Fuente = archivo y línea (del repositorio o de `docs/informe/inventario.md` cuando el dato se derivó durante la auditoría de Fase 1). Regla: si una cifra no tiene fila aquí, no debería aparecer en el cuerpo del informe.

| Valor citado | Sección del informe | Archivo/línea de origen |
|---|---|---|
| RSS cargado: texto 34 926 592 B, imagen 250 294 272 B, audio 166 043 648 B | 5.1 M1 | `experiments/m1_memory/results/native/` (ver tabla en `inventario.md:80`) |
| PSS propio/compartido: texto 103.04/41.70 MiB, imagen 676.81/296.26 MiB, audio 466.92/191.94 MiB | 5.1 M1 | `experiments/m1_memory/results/linux/*-own.json` / `*-shared.json`; `inventario.md:90` |
| `limits.csv`, 21 filas, una corrida por límite | 5.1 M1 (Figura 5) | `experiments/m1_memory/results/linux/limits.csv` |
| 37 de 40 casos rechazados bajo presupuesto de 3 GiB; 0 de 40 tras la corrección | 5.1 M1, 9 (lecciones), 10 (uso de IA) | `dataset/cases/*/manifest.json`; reproducido desde los manifiestos, ver `inventario.md:83` |
| `worker-memory.csv`, 483 filas | 5.1 M1 (Figura 6) | `experiments/fault_tests/burst/results/worker-memory.csv` |
| Bases de admisión 512/384/64 MiB, "estimaciones iniciales" | 5.1 M1 | `docs/handoff-completion.md:130-131` |
| Aging: `min(4, base + edad/30s)`; `SCHEDULER_AGING_S=30` por defecto | 5.2 M2 | `services/coordinator/src/coordinator/scheduler/__init__.py`; `config.py:81-82` |
| Dos corridas de 40 casos, FIFO y aging | 5.2 M2 (Figura 7) | `experiments/m2_scheduling/results/local/{fifo,aging}.csv` |
| p95: 37.16 s (FIFO), 35.18 s (aging) | 5.2 M2 | `experiments/m2_scheduling/results/local/{fifo,aging}.csv`; recalculado, ver `inventario.md:100` |
| p50 (mediana real): 32.61 s (FIFO), 32.44 s (aging) | 5.2 M2 | Ídem; discrepancia con la ficha anotada como D27 en `inventario.md:270,100` |
| Con `SCHEDULER_AGING_S=2`, LOW llega a prioridad 4 a los 6 s | 5.2 M2 | `inventario.md:101` |
| Chunk por defecto 4 MiB | 5.3 M3 | `services/storage/src/storage/chunking/__init__.py:17-19` |
| Throughput: 256 KiB 257.83±3.68 MiB/s; 4 MiB 215.37±5.87; 16 MiB 186.34±15.16 | 5.3 M3 (Figura 8) | `experiments/m3_io_storage/results/throughput.csv` (9 filas, 3 corridas c/u) |
| Deduplicación sintética: razón 5.0 (175 000 B / 35 000 B) | 5.3 M3 | `experiments/m3_io_storage/results/dedup.csv` |
| `queue_samples.csv`, 139 filas | 5.3 M3 (Figura 9) | `experiments/m3_io_storage/results/queue_samples.csv` |
| Capacidades efectivas 0, `no_new_privileges` 1 | 5.4 M4 | `docs/load-tests/local-builds/privileges.json` |
| Cadena de custodia: 2976 registros, cabeza `afcbb8a4…57d28` | 5.4 M4 | `docs/load-tests/local-builds/custody-head.json`; cifra "370 registros" de la ficha sin archivo de respaldo, ver `inventario.md:141` |
| TLSv1.3 en el objetivo local | 5.4 M4 | `docs/load-tests/local-builds/tls-target.json` |
| Whisper: `tiny-int8-t2` 1.3447 s / 24.8617 / 322.76 MB; `tiny-float32-t2` 1.8288 / 17.0087 / 513.36; `base-float32-t4` 3.2739 / 10.4565 / 679.50 | 5.5 M5 (Figura 10) | `experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv` |
| YOLOv8n p50(s)/img por s: 1h 0.1043/9.449; 2h 0.0814/11.5793; 4h 0.0700/14.198; 8h 0.0744/12.7918 | 5.5 M5 (Figura 11) | `experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/summary.csv` |
| `runs.csv` 804 filas, `summary.csv` 13 configuraciones | 5.5 M5 | `experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/` |
| `MODEL_THREADS=2` en despliegue | 5.5 M5, 7 (decisiones) | `scripts/deploy/remote-node.sh:105` |
| Ráfaga 20 casos: latencia mín. 16.389 s, máx. 131.628 s, mediana 82.897 s, p95≈128.1 s | 6 (B.6, Figura 12) | `experiments/fault_tests/burst/results/real-model-cases.csv`; p95 recalculado, ver `inventario.md:182` |
| `worker-memory.csv` 483 filas (escenario 1) | 6 (B.6) | `experiments/fault_tests/burst/results/worker-memory.csv` |
| Casos nube sin archivo crudo: GFVN309Z 3/3 en 4.0 s; P8Y82BM7 6/6 en 12.0 s | 6 (B.6), 5.1 M1, 10 (uso de IA) | `docs/handoff-completion.md:47-62` |
| 40 casos del dataset | 3 (Dataset), B.3 | `dataset/cases/*/manifest.json` |
| 15 casos heterogéneos (10 con video pequeño + 5 pesados) | 3 (Dataset) | `docs/dataset.md:228` |
| 21 337 mensajes de texto (21 000 usuario + 337 sistema) | 3 (Dataset) | Conteo directo por parseo de `dataset/cases/*/chat/*`, ver `inventario.md:218` |
| 300 imágenes; 73 positivas / 227 negativas (≈24.3 %) | 3 (Dataset) | Manifiestos + `dataset/ground_truth/` |
| 150 audios, 3.8–172.3 s, suma 73.9 min | 3 (Dataset) | Manifiestos; `docs/dataset.md:231` |
| 5 videos ≥200 MB: 362 217 118 / 408 513 738 / 343 374 671 / 264 284 958 / 269 614 277 B | 3 (Dataset) | `size_bytes` de los manifiestos de los casos 011–015 |
| Total declarado 505 fuentes, ≈1.7 GB (1 715 067 965 B) | 3 (Dataset) | Suma de manifiestos, ver `inventario.md:225` |
| Licencias COCO 167/84/48/1 (de 300 usadas) | 3 (Dataset) | Calculado desde los manifiestos; discrepancia con `docs/dataset.md` (174/87/48/1 sobre 310), ver `inventario.md:227` |
| Precisión/recall por categoría: threat 0.884/0.688; offensive_language 0.804/0.741; violence 0.657/0.657; illicit_activity 0.614/0.614; sensitive_image 0.697/0.172 | 3 (Dataset, Figura 4) | `dataset/evaluation/metrics.json` |
| `sensitive_image` a nivel de imagen: precisión 0.964, recall 0.370 (27 TP, 1 FP, 46 FN, 226 TN) | 3 (Dataset) | `dataset/evaluation/metrics.json` |
| IoU medio de cajas emparejadas 0.845 | 3 (Dataset) | `dataset/evaluation/metrics.json` |
| `delay = min(60, 2 × 2^(intento-1))`, máx. 3 intentos | 4 (Pipeline) | `services/coordinator/src/coordinator/config.py:91-93,115-117` |
| Heartbeat cada 5 s, sweeper cada 5 s, muerto si `last_seen` > 60 s | 4 (Pipeline) | `services/coordinator/src/coordinator/barrier/sweeper.py:49-72` |
| Snapshot cada 2 s | 4 (Pipeline), 2 (Arquitectura) | `services/coordinator/src/coordinator/monitor/snapshot.py` |
| Alertas operativas máx. cada 300 s por pool/nivel | 4 (Pipeline) | `services/coordinator/src/coordinator/monitor/notifications.py:30-146` |

Cifras citadas en el Resumen que remiten a las filas anteriores (B.3, M1–M5, B.6) no se repiten aquí; ver la fila correspondiente por valor.
