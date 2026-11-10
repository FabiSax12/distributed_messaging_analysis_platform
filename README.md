# Distributed messaging-analysis platform on AWS

II Programming Project (IC-6600 Operative Sistems Course): a distributed platform that analyzes messaging data (text, images and audio) on AWS. A coordinator splits each case into sub-tasks, and specialized workers process them in parallel. Each project module focuses on an operating-systems concern.

Full documentation lives in [docs/](docs/).

## Architecture

See [the current architecture](docs/architecture/current.md). The older PNG is
historical; it should not be used as proof of deployment.

CloudFront serves the dashboard and proxies the API. The coordinator performs
admission, FIFO/aging scheduling, case barriers, retries, reports and custody
logging. RabbitMQ connects three non-root CPU worker pools. Downloads use
verified chunks; S3 CAS objects use SHA-256 keys. Cognito access tokens authorize
REST and WebSocket access when configured. Production compose supports cgroup
limits. Terraform includes SNS/application alarms and optional managed origin
TLS; cloud activation and experiments remain manual.

Frontend login/views, full dataset generation and the M5 benchmark remain
Camila's independent deliverables. See [completion status and manual steps](docs/handoff-completion.md).

## Modules

| Module | Topic | Report |
|---|---|---|
| M1 | Memory | [docs/module-m1-memory.md](docs/module-m1-memory.md) |
| M2 | Scheduling | [docs/module-m2-scheduling.md](docs/module-m2-scheduling.md) |
| M3 | I/O and storage | [docs/module-m3-io-storage.md](docs/module-m3-io-storage.md) |
| M4 | Security | [docs/module-m4-security.md](docs/module-m4-security.md) |
| M5 | Heterogeneous compute | [docs/module-m5-heterogeneous-compute.md](docs/module-m5-heterogeneous-compute.md) |

Who owns what: [docs/responsabilidades.md](docs/responsabilidades.md). What is reused from Project I: [docs/reutilizacion.md](docs/reutilizacion.md).

## Requirements

- Terraform >= 1.9 and AWS credentials with admin-like rights on the target account.
- AWS CLI and the Session Manager plugin, to reach instances.
- A confirmed email address for alerts and a globally unique Cognito domain prefix.

## Deploy

Terraform builds the infrastructure only. The application is pushed afterwards by the scripts in `scripts/deploy/` that read `terraform output` (see `docs/deployment.md`), by hand or through GitHub Actions on push to `main`.

```bash
cd infra/terraform/environments/dev
cp terraform.tfvars.example terraform.tfvars   # set alert_email and cognito_domain_prefix
terraform init
terraform plan
terraform apply
```

Then confirm the SNS subscription email. Details, cost estimate, GPU switch and destroy steps are in [infra/terraform/README.md](infra/terraform/README.md).

Dev costs roughly 280-300 USD/month if left running 24x7. Run `terraform destroy` or stop the workers between sessions.

## Repository map

| Path | Contents |
|---|---|
| `infra/terraform/` | Terraform modules and the `dev` environment (`prod` is a template only) |
| `infra/docker/` | Local stack for end-to-end runs (RabbitMQ, PostgreSQL, MinIO) |
| `infra/terraform/modules/observability`, `tls_origin` | Dashboard/alarms and optional managed origin TLS |
| `services/coordinator/` | REST API, outbox relay, barrier, retries and recovery on RabbitMQ + PostgreSQL; admission (M1), FIFO/aging scheduling (M2), JWT/RBAC and custody (M4) |
| `services/workers/` | Worker runner for the text, image and audio pools; registered text, YOLO and Whisper operations; isolated cancellation |
| `services/shared/` | Wire contracts: routing, messages, states, models, broker topology, analysis results |
| `services/storage/` | Chunking, content store and backpressure; integrated in worker download |
| `services/load_client/` | Concurrent manifest-driven load client; alerting lives in coordinator/monitor |
| `frontend/` | React dashboard (Vite) |
| `dataset/` | Test data, generators and ground truth |
| `experiments/` | Per-module measurements and fault tests |
| `scripts/` | Bootstrap, dataset, deploy and measurement scripts |
| `tests/` | Unit and integration tests |
| `docs/` | Module reports, architecture diagram, dataset, AI log, report |
