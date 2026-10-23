# Reutilización / Reuse of Project I

This document declares what Project II (Alternative B) reuses from our Project I, what it modifies, and what is new (assignment section B.2).

- **Project I repository:** [FabiSax12/distributed-multimedia-processing](https://github.com/FabiSax12/distributed-multimedia-processing), commit `de2c96c` **[confirm: this hash could not be resolved when checked; pin the delivered commit or a tag]**.
- **Paths below** refer to the Project I repository unless stated otherwise.
- **Architecture diagram:** see `docs/arquitectura/`.

## Project I at a glance

Project I processed video, audio and images with ffmpeg on AWS. It is a Python system with:

- **Coordinator:** a single FastAPI process, thread-based (all work is I/O-bound), running on one EC2 instance.
- **Messaging and state:** SQS queues per worker pool, DynamoDB for cases, sub-tasks and workers, and S3 for media.
- **Workers:** EC2 nodes with one pool per media type, deployed with systemd units and shell scripts.
- **Dashboard:** a React (Vite) app with a WebSocket live view, published to S3 behind CloudFront. CloudFront also proxies `/api/*` to the coordinator, protected by an `X-Origin-Verify` shared secret.
- **Infrastructure:** Terraform with modules `compute`, `data`, `frontend` and `queues`. Instances are managed through SSM, without SSH.

Project II keeps the coordination pattern (cases, sub-tasks, barrier, specialized workers) and changes the content being processed (messaging data instead of media conversion), the queue and persistence layer, and the operating-systems concerns (memory, scheduling, I/O, security, heterogeneous compute).

## Summary

| Category | What it contains |
|---|---|
| Reused almost unchanged | Shared contracts, WebSocket broadcaster and snapshot, health and logging, dashboard hooks and components, origin verification middleware, CloudFront + S3 frontend delivery, test tooling, SSM-based access |
| Reused and modified | API, planner, barrier, sweeper, consumers, repositories, report builder, alert rules, worker runner, dashboard views, dataset manifest and upload script, Terraform modules, deployment |
| New | M1 to M5, analysis workers, priority scheduler, content-addressed store, user authentication, custody log, dataset content, load client, fault-tolerance tests |

## What is reused

Copied with minimal changes (renames, imports, content types, extra fields).

| Source (Project I) | Purpose here | Change |
|---|---|---|
| `shared/shared/states.py` | `SubTaskStatus` and `CaseStatus` with `queued`, `processing`, `completed`, `partially_completed`, `failed`, `retrying`, `cancelled` (the states required by B.4). `state_order()` prevents a duplicated or late message from moving a state backwards. | None in substance |
| `shared/shared/messages.py` | `SubTaskMessage` (coordinator to workers) and `ResultMessage` (worker to coordinator). | Content-type and priority fields |
| `shared/shared/models.py`, `shared/shared/api.py` | Shapes of `Case`, `SubTask`, `Worker`, `CaseReport` and the REST contracts. | Evidence and role fields |
| `coordinator/.../monitor/broadcaster.py`, `monitor/snapshot.py` | Thread-to-asyncio bridge that pushes state over `/api/ws`, and the periodic snapshot. | Per-node CPU and memory added to the snapshot |
| `coordinator/.../api/health.py`, `api/ws.py`, `logging.py`, `config.py` | Health endpoint, WebSocket endpoint, logging, per-environment configuration. | New environment variables |
| `coordinator/.../security.py` | `X-Origin-Verify` middleware so the API cannot be reached while bypassing CloudFront (also checked in the WebSocket handshake). | Kept as an extra layer. User authentication and roles are added on top (M4) |
| `dashboard/src/hooks/`, `lib/`, `components/` | WebSocket with polling fallback, hash routing, charts. | None in substance |
| `infra/modules/frontend`, `deploy/publish-dashboard.sh` | Dashboard delivery: Vite build, S3 bucket, CloudFront invalidation, and `/api/*` proxied to the coordinator (same origin, so no CORS). | Cognito login integrated in the app |
| Concurrency model of the coordinator | One process with threads, no `multiprocessing`. | None |
| `coordinator/scripts/fake_worker.py`, `smoke.py`, `workers/scripts/local_e2e.py`, `pytest` layout | Local end-to-end and smoke testing without real workers. | AWS mocks (`moto`) replaced by PostgreSQL and RabbitMQ fixtures |
| Infrastructure conventions | Flat Terraform layout, SSM-only access (no SSH), AMI taken from an SSM parameter, generated `.env.local`. | Extended, see below |

## What is modified

The logic is reused, but the technology or the scope changes in a meaningful way.

| Source (Project I) | What is kept | What changes |
|---|---|---|
| `shared/shared/routing.py` | The table extension, type, operation, pool and queue, and the idea of one sub-task per file. | Content types `text`, `image`, `audio` (previously video, audio, image). Analysis operations instead of conversions. Four priority levels (previously two) for M2. |
| `coordinator/.../api/` (`cases`, `uploads`, `dataset`) | Create, list, view and cancel cases, and fetch the report. | Validation of messaging export formats (WhatsApp/Telegram style). Chunked upload with hash verification (M3). Every endpoint requires a JWT and a role (M4). |
| `coordinator/.../routing/planner.py`, `enqueue.py` | Decomposing a case into sub-tasks, and the outbox pattern (write sub-tasks, then the case, then enqueue). | The outbox moves from DynamoDB + SQS to a PostgreSQL transaction + RabbitMQ. |
| `coordinator/.../barrier/close.py`, `finalize.py` | Atomic `pending_count` decrement per sub-task, idempotent close, final state computed from failures and cancellation. | `TransactWriteItems` becomes `UPDATE ... SET pending_count = pending_count - 1` inside a SQL transaction. |
| `coordinator/.../barrier/sweeper.py` | Repairing orphaned cases and stuck barriers at startup and periodically. This is the base of the "coordinator restart" test (B.6). | SQL queries. Reassignment of sub-tasks owned by a crashed worker is added. |
| `coordinator/.../consumers/` | Threads that consume results and the dead-letter queue. | From SQS consumers to AMQP consumers with manual ack. Retries with configurable backoff are new. |
| `coordinator/.../repo/` | The repository interface (`Cases`, `SubTasks`, `Workers`) and writes guarded by `state_order`. | From DynamoDB (with `Scan`) to PostgreSQL with indexes, which removes a known Project I limitation. |
| `coordinator/.../reports/builder.py` | The consolidated `CaseReport`. | Findings, categories, activity timeline and incidents, with evidence fragments linked to the source message, image or audio segment. |
| `coordinator/.../monitor/balancer.py` | Pure alert rules (pool saturation, pool without workers, dead-letter queue). | In Project I they only produced alerts inside the snapshot. Here they are published through SNS when a case closes or a severe incident appears. |
| `workers/.../runner/` (`poller.py`, `process.py`, `heartbeat.py`) | Poll, process, publish result loop. Checks `cancel_requested` before processing. Heartbeat to detect crashes. | From SQS to AMQP. Cancellation goes from "do not start" to **stopping an in-flight sub-task** and releasing processes, memory and temporary files. The heartbeat target moves from DynamoDB to PostgreSQL. |
| `workers/.../ops/ffmpeg.py` (pattern) | Running heavy work as a subprocess with a timeout. | Generalized to any subprocess, so it can be killed on cancellation. |
| `dashboard/src/views/` (`CasesList`, `CaseDetail`, `NewCase`, `Overview`) | Case list, detail, new case, overview. | Per-node resource usage, queues and alerts. Messaging dataset upload. Role-based views. |
| `dataset/` (manifest, upload script) and `api/dataset.py` | The idea of a dataset manifest and a script that uploads it. | Adapted to the messaging dataset and its ground truth. |
| `infra/` (Terraform modules `compute`, `data`, `queues`) | Module structure and conventions. | `queues`: SQS replaced by Amazon MQ (RabbitMQ). `data`: DynamoDB replaced by RDS PostgreSQL (S3 stays as the byte store). `compute`: nodes run Docker containers with cgroups limits. New modules for identity (Cognito), secrets and keys (KMS, Secrets Manager) and container registry (ECR). |
| `deploy/` (systemd units and scripts) | Idempotent per-instance deployment. | Replaced by Docker Compose per node. |

## What is new

Nothing in this table exists in Project I.

| Component | Description | Assignment |
|---|---|---|
| Analysis workers | Text (keywords, topic, sentiment, offensive or threatening language), image (classification or detection) and audio (speech-to-text and transcript analysis). They replace the ffmpeg operations (`ops/video.py`, `ops/audio.py`, `ops/metadatos.py`). | B.4 |
| M1 Memory | cgroups v2 limits, RSS/PSS footprint per model, admission control, requeue after OOM, and model loading strategy. | B.5 |
| M2 Scheduling | Explicit priority algorithm with aging, and an experiment comparing at least two policies. | B.5 |
| M3 I/O and storage | Chunked transfer with hash verification, content-addressed storage (SHA-256), deduplication, backpressure with bounded queues, asynchronous I/O. | B.5 |
| M4 Protection and security | User authentication and roles (analyst, supervisor, administrator), TLS between nodes, encryption at rest, hash-chained custody log, unprivileged workers. | B.5 |
| M5 Heterogeneous compute | CPU vs. GPU (or threads and quantization) comparison, and a coordinator routing policy based on the measurements. | B.5 |
| Real cancellation | Stop in-flight sub-tasks and release resources. | B.4 |
| Retries with backoff | Configurable maximum and delay. Project I only used the SQS redrive (`maxReceiveCount = 3`) and the visibility timeout. | B.4 |
| Synthetic dataset | 40 cases, 20,000 messages, 300 images, 150 voice notes, 5 heavy cases and ground truth. | B.3 |
| Load client | Burst of 20 concurrent cases. The `client/` folder in Project I only contains a `.gitkeep`. | B.6 |
| Load and fault-tolerance tests | Burst, worker crash, coordinator restart, cancellation in progress and memory saturation. | B.6 |

## Why the queue and persistence layer changes

Replacing SQS and DynamoDB is the largest modification, so the reasons are stated here.

- **Bounded queues and backpressure (M3):** RabbitMQ supports a maximum queue length that rejects producers when full. SQS has no maximum queue length.
- **Priority levels (M2):** RabbitMQ supports priority queues natively, and the scheduler can decide what to publish and when.
- **Atomic multi-row operations:** the barrier and the outbox become plain SQL transactions.
- **Known limitation:** queries based on `Scan` are replaced by indexed queries.

## Limitations of Project I that are corrected

B.2 requires fixing the deficiencies of Project I. These are the known ones, taken from the Project I documentation and code. **[confirm: add the observations from the instructor's evaluation of Project I.]**

| Limitation in Project I | Where it is corrected |
|---|---|
| Repositories based on `Scan` instead of indexes | PostgreSQL repositories with indexes |
| No per-user authentication (only the origin-verification secret) | M4 |
| Possible orphaned outbox entries if the process dies mid-write | Transactional outbox in PostgreSQL, plus the sweeper |
| Cancellation only before processing starts | Real cancellation (B.4) |
| Alerts without any action | SNS notification |
| No load client (`client/` is empty) | Load client (B.6) |

## Out of scope

The video conversion operations, the `video` and `metadatos` pools, and the MusicBrainz and lyrics.ovh integrations of Project I are not reused.