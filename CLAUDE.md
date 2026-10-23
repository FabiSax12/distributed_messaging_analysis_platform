# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

University project (II Proyecto Programado IC-6600, Alternative B): a distributed platform that analyzes messaging data (text, image, audio) on AWS. It builds on the team's Project I (`FabiSax12/distributed-multimedia-processing`); `docs/reuse.md` records what is reused, modified or new.

Right now only `infra/terraform/` contains real code. Everything else (`services/`, `frontend/`, `dataset/`, `experiments/`, `scripts/`, `tests/`, `infra/docker`, `infra/monitoring`, `infra/tls`) is an empty scaffold of `.gitkeep` files, so there are no build, lint or test commands yet. Do not invent them.

## Conventions

- Everything is in English (explicit user decision): file and directory names, code, comments, commit messages, the README and all docs. Existing Spanish content (the `docs/` templates, `docs/architecture/diagram.drawio` labels, `docs/reuse.md`'s `docs/arquitectura/` path) is legacy and should be translated when touched.
- Planned layout: `services/coordinator/{api,admission,scheduler,state}`, `services/workers/{common,text,image,audio}`, `services/storage/*`, `services/security/*`, one `experiments/mN_*` folder per OS module (M1 memory, M2 scheduling, M3 I/O and storage, M4 security, M5 heterogeneous compute), each with a matching `docs/module-m*.md` report template (sections: what was implemented, OS concept, measurement method, results, interpretation).
- `docs/responsibilities.md` maps modules and areas to team members; `docs/ai_log.md` is a required log of AI usage (at least two documented AI errors).

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

- `modules/` has 11 single-purpose modules (network, secrets_kms, storage_evidence, storage_frontend, messaging, database, registry, compute, cdn, identity, observability). `environments/dev/main.tf` wires them together; `environments/prod` is a template that was only `terraform validate`d, never planned or applied.
- Dependency cycles are avoided on purpose. Log group names and the SNS topic name are computed as locals in the environment `main.tf` and passed to both `compute` and `observability`; compute builds ARNs from names. `cdn` does not depend on `identity`, so `identity` can take the CloudFront URL.
- Workers are driven by the `worker_nodes` map (`text`, `image`, `audio`); log groups and instances are derived from its keys. Switching audio to GPU (`g4dn.xlarge`) is a tfvars change, but the default AL2023 AMI has no NVIDIA drivers.
- The coordinator application must validate the `X-Origin-Verify` header (value in Secrets Manager), because the CloudFront prefix list is shared across all customers.
- Terraform only builds infrastructure. The app is deployed by scripts (planned in `scripts/deploy/`) that read `terraform output` and push through SSM.
- State holds generated passwords. Never commit `*.tfstate*` or `*.tfvars`; do commit `.terraform.lock.hcl` and `*.tfvars.example`.
- Dev runs about 280-300 USD/month if left on (3x t3.large workers, NAT, Amazon MQ dominate), above the default 60 USD budget alert. Destroy or stop workers between sessions.
