# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

University project (II Proyecto Programado IC-6600, Alternative B): a distributed platform that analyzes messaging data (text, image, audio) on AWS. It builds on the team's Project I (`FabiSax12/distributed-multimedia-processing`); `docs/reutilizacion.md` records what is reused, modified or new.

- `services/coordinator` and `services/workers` run on RabbitMQ (pika) + PostgreSQL (psycopg 3, plain SQL, schema in `coordinator/db/schema.sql` applied at startup), with S3 only as byte store. Coordinator: outbox relay publishes work, the coordinator alone decides retries (`retry_at` backoff), the barrier is one guarded `UPDATE ... RETURNING`, the sweeper finalizes stuck cases and requeues work of workers with a stale heartbeat. Wire contract: `shared/{routing,messages,states,models,broker}.py`; analysis results: `shared/analysis.py`. Workers dispatch through `workers.ops.analysis.ANALYSIS_OPERATIONS`, with text rules, YOLOv8n and faster-whisper tiny analysis ops; `python -m workers.ops.run_local` runs one op on a local file.
- Python is a uv workspace (root `pyproject.toml`, one `uv.lock`): `uv sync --all-packages --group dataset`. Validate the dataset with `uv run --group dataset python dataset/generators/validate_manifests.py`. Lint with `uv run ruff check <path>`.
- Local end to end: `docker compose -f infra/docker/compose.dev.yml up -d --wait` (RabbitMQ 5673/15673, PostgreSQL 5434, MinIO 9100, bound to 127.0.0.1), `set -a; source infra/docker/dev.env; set +a`, then `uv run --all-packages python services/workers/scripts/local_e2e.py` (coordinator + `fake_worker.py` per pool; scenarios basic, unsupported, retries, crash, restart). By hand: `uv run --package coordinator uvicorn coordinator.main:app`, `services/workers/scripts/fake_worker.py --pool <text|image|audio> [--fail-rate P --crash-rate P]`, `services/coordinator/scripts/smoke.py`. `docker compose ... down -v` when done.
- FIFO/aging scheduling, JWT/RBAC, custody triggers, process-isolated cancellation, report aggregation and M3 download/CAS wiring are implemented. Read `docs/handoff-completion.md` for evidence and manual cloud steps.
- `frontend/` has a Vite dashboard; `infra/monitoring`, `infra/tls`, experiment runners now exist; Camila's frontend Cognito/roles integration, generated dataset (`dataset/generators/`) and M5 benchmark (`experiments/m5_heterogeneous_compute/`) are delivered; see `docs/responsabilidades.md`.

## Conventions

- Build only what the assignment PDF asks for. No unit tests (the PDF grades measured evidence: M1-M5 reports and the five B.6 load/fault scenarios). Tools are fine when they save end-to-end testing time (`run_local`, `fake_worker.py`, `smoke.py`, the manifest validator). Measurement experiments are required: a module without measured evidence is capped at 2/5.
- Required doc names come from the PDF and stay in Spanish: `docs/responsabilidades.md`, `docs/reutilizacion.md`, `docs/bitacora_ia.md` (contents in English).
- Everything else is in English (explicit user decision): file and directory names, code, comments, commit messages, the README and all docs. Existing Spanish content (the `docs/` templates, `docs/architecture/diagram.drawio` labels, `docs/reutilizacion.md`'s `docs/arquitectura/` path) is legacy and should be translated when touched.
- Planned layout: `services/coordinator/{api,admission,scheduler,state}`, `services/workers/{common,text,image,audio}`, `services/storage/*`, `services/security/*`, one `experiments/mN_*` folder per OS module (M1 memory, M2 scheduling, M3 I/O and storage, M4 security, M5 heterogeneous compute), each with a matching `docs/module-m*.md` report template (sections: what was implemented, OS concept, measurement method, results, interpretation).
- `docs/responsabilidades.md` maps modules and areas to team members; `docs/bitacora_ia.md` is a required log of AI usage (at least two documented AI errors).

## Terraform (`infra/terraform/`)

Read `infra/terraform/README.md` first; it covers deploy, destroy, cost and the GPU switch.

```bash
cd infra/terraform/environments/dev
cp terraform.tfvars.example terraform.tfvars   # set alert_email and cognito_domain_prefix
terraform init && terraform plan && terraform apply
terraform output
terraform destroy
terraform fmt -recursive ../..      # from environments/dev
terraform validate
```

Architecture: CloudFront serves the SPA from S3 (OAC) and proxies `/api/*` to the coordinator EC2 (public subnet, EIP, port 8000, only reachable from CloudFront). The coordinator and the text/image/audio workers (private subnets, NAT egress) use Amazon MQ (RabbitMQ), RDS PostgreSQL and a KMS-encrypted evidence S3 bucket. Instances are managed only through SSM (no SSH, no key pairs).

Structure and gotchas that need more than one file to see:

- `modules/` has 12 single-purpose modules (network, secrets_kms, storage_evidence, storage_frontend, messaging, database, registry, compute, cdn, identity, observability, ci_oidc). `environments/dev/main.tf` wires them together; `environments/prod` is a template that was only `terraform validate`d, never planned or applied.
- Dependency cycles are avoided on purpose. Log group names and the SNS topic name are computed as locals in the environment `main.tf` and passed to both `compute` and `observability`; compute builds ARNs from names. `cdn` does not depend on `identity`, so `identity` can take the CloudFront URL.
- Workers are driven by the `worker_nodes` map (`text`, `image`, `audio`); log groups and instances are derived from its keys. Switching audio to GPU (`g4dn.xlarge`) is a tfvars change, but the default AL2023 AMI has no NVIDIA drivers.
- The coordinator application must validate the `X-Origin-Verify` header (value in Secrets Manager), because the CloudFront prefix list is shared across all customers.
- Terraform only builds infrastructure. The app is deployed by `scripts/deploy/` (`build-and-push.sh`, `deploy-app.sh`; see `docs/deployment.md`), which read `terraform output` and push through SSM. GitHub Actions (`.github/workflows/ci.yml`, `deploy.yml`) runs the same scripts on push to `main`, authenticating through OIDC with the `ci_oidc` module's role; CI never runs Terraform.
- State holds generated passwords. Never commit `*.tfstate*` or `*.tfvars`; do commit `.terraform.lock.hcl` and `*.tfvars.example`.
- Dev runs about 280-300 USD/month if left on (3x t3.large workers, NAT, Amazon MQ dominate), above the default 60 USD budget alert. Destroy or stop workers between sessions.

M1 admission and M3 storage implementations now exist; see their module reports for measured native results and remaining Linux/Docker/dataset evidence.
