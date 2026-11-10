# Responsibilities

Work is split by load: Fabián owns the heaviest and cloud-bound work, which can start right away; David owns the second-largest share; Camila owns the lightest share. Every member is the primary owner of at least one OS module (assignment B.5).

| Topic / module | Primary owner | Support | Path in repository | Evidence report |
|---|---|---|---|---|
| Migration to RabbitMQ + PostgreSQL (coordinator, workers, shared) | Fabián Vargas | David Molina | `services/coordinator/`, `services/workers/`, `services/shared/` | — |
| Terraform infrastructure | Fabián Vargas | — | `infra/terraform/` | — |
| Deployment (Docker images, Compose per node, SSM scripts) | Fabián Vargas | David Molina | `infra/docker/`, `scripts/deploy/` | — |
| M2 Scheduling (priorities, aging, policy experiment) | Fabián Vargas | David Molina | `services/coordinator/scheduler/`, `experiments/m2_scheduling/` | `docs/module-m2-scheduling.md` |
| M4 Security (Cognito RBAC, TLS, KMS, custody log, non-root workers) | Fabián Vargas | Camila Hidalgo | `services/security/`, `infra/tls/`, `experiments/m4_security/` | `docs/module-m4-security.md` |
| Load and fault-tolerance tests (B.6) | Fabián Vargas | David Molina | `experiments/fault_tests/`, `services/load_client/` | Final report |
| Analysis workers (text, image, audio) | David Molina | Camila Hidalgo | `services/workers/` | — |
| M1 Memory (cgroups limits, RSS/PSS, admission control, OOM requeue) | David Molina | Fabián Vargas | `services/coordinator/admission/`, `experiments/m1_memory/` | `docs/module-m1-memory.md` |
| M3 I/O and storage (chunks, SHA-256 CAS, dedup, backpressure) | David Molina | Fabián Vargas | `services/storage/`, `experiments/m3_io_storage/` | `docs/module-m3-io-storage.md` |
| M5 Heterogeneous compute (CPU vs GPU / threads vs quantized) | Camila Hidalgo | David Molina | `experiments/m5_heterogeneous_compute/` | `docs/module-m5-heterogeneous-compute.md` |
| Dataset (generators, public sources, ground truth) | Camila Hidalgo | David Molina | `dataset/`, `scripts/dataset/`, `docs/dataset.md` | — |
| Frontend (dashboard, Cognito login, role-based views) | Camila Hidalgo | Fabián Vargas | `frontend/` | — |
| AI usage log | Everyone | — | `docs/bitacora_ia.md` | — |

## Current implementation/manual split

David implements remaining backend, worker, storage, infrastructure code,
experiments and deployment/docs changes in one completion PR. Fabi?n performs
Terraform apply, repository-variable setup, instance lifecycle, Cognito users,
cloud experiments/screenshots and instructor-feedback collection. Camila keeps
frontend login/views, the full dataset and M5 benchmarking; backend contracts
and model device/thread/quantization settings are available without waiting
for those deliverables. Module ownership above remains for presentation and
evidence interpretation; code authorship follows actual commits.
