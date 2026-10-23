# Terraform infrastructure

Distributed messaging-analysis platform on AWS (IC-6600, alternative B). Single region, optimized for clarity and low cost.

```
Users -> CloudFront --default--> S3 (SPA, via OAC)
                    --/api/*---> Coordinator EC2 (public subnet, EIP, port 8000, only from CloudFront)
                                      |  Amazon MQ (RabbitMQ) / RDS PostgreSQL / S3 evidence (KMS)
                                      v
                          Workers text / image / audio (private subnets, NAT for egress)
```

## Layout

- `modules/`: network, secrets_kms, storage_evidence, storage_frontend, messaging, database, registry, compute, cdn, identity, observability (each has its own README).
- `environments/dev`: deployable environment. `environments/prod`: template only (see its README).

## Prerequisites

- Terraform >= 1.6 and AWS credentials with admin-like rights on the target account (`aws configure` or `AWS_PROFILE`).
- AWS CLI + Session Manager plugin to reach instances (no SSH, no key pairs, no port 22).
- A confirmed email address for alerts and a globally unique Cognito domain prefix.

## Deploy

```bash
cd environments/dev
cp terraform.tfvars.example terraform.tfvars   # edit alert_email and cognito_domain_prefix
terraform init
terraform plan
terraform apply
terraform output
```

After applying, confirm the SNS subscription email. The application is NOT deployed by Terraform: deployment scripts read `terraform output` (ECR URLs, endpoints, secret ARNs, Cognito IDs, instance IDs) and push containers/configuration through SSM.

Connect to an instance: `aws ssm start-session --target <instance-id>`.

## Destroy

```bash
cd environments/dev
terraform destroy
```

Dev uses `force_destroy` on buckets/ECR and a 0-day secret recovery window so that destroy and re-apply work. The KMS key is scheduled for deletion (7 days).

## WARNING: state contains secrets

Generated passwords (RDS, Amazon MQ, X-Origin-Verify) are stored in the Terraform state, and the origin secret is also in the CloudFront config. **Never commit `*.tfstate*` or `*.tfvars` to git** (they are in `.gitignore`). For teams use the S3 backend example in `backend.tf` with encryption and restricted access. `.terraform.lock.hcl` should be committed.

## Estimated monthly cost (us-east-1, on-demand, 24x7, approximate)

| Resource | Approx. USD / month | Note |
|---|---|---|
| NAT Gateway (1) | ~33 + data | **Most expensive**; `enable_nat_gateway = false` removes it but breaks private egress |
| Amazon MQ mq.t3.micro | ~25 | **Expensive** for what it is |
| RDS db.t4g.micro + 20 GB gp3 | ~13 + 3 | **Expensive**; doubles with `multi_az` |
| Coordinator t3.small + 30 GB | ~15 + 3 | |
| 3x worker t3.large + 40 GB | ~180 | Largest compute cost; stop instances when idle |
| Elastic IPs (public IPv4) | ~3.6 per address | NAT EIP + coordinator EIP |
| CloudFront, S3, ECR, Secrets Manager (3), KMS (1), CloudWatch | ~5-10 | Low volume |
| Cognito | 0 | Free tier up to 10k MAU |

Total is roughly 280-300 USD/month if left running 24x7, above the default 60 USD budget alert. For a two-week project, stop the workers or run `terraform destroy` between work sessions. Figures are estimates, not a quote.

## Switch the audio node to GPU (module M5)

Change only the variable in `terraform.tfvars`:

```hcl
worker_nodes = {
  text  = { instance_type = "t3.large", volume_gb = 40 }
  image = { instance_type = "t3.large", volume_gb = 40 }
  audio = { instance_type = "g4dn.xlarge", volume_gb = 80 }
}
```

Then `terraform apply` (the instance is replaced when the type family changes architecture/hardware, otherwise stopped/started). Notes:

- g4dn.xlarge costs ~380 USD/month on-demand; GPU vCPU quota ("Running On-Demand G and VT instances") may need to be raised in Service Quotas.
- The default Amazon Linux 2023 AMI does not include NVIDIA drivers or the NVIDIA Container Toolkit. Install them in your deployment script, or set `ami_ssm_parameter` in the compute module to a GPU-ready AMI. This was not verified.

## Design notes

- No dependency cycles: compute and observability share deterministic names (log groups, SNS topic) defined as locals in the environment; compute builds ARNs from names. Identity receives the CloudFront URL from cdn (cdn does not depend on identity).
- The coordinator must validate the `X-Origin-Verify` header (value in Secrets Manager) because the CloudFront prefix list is shared by all CloudFront customers.
- CloudFront to coordinator uses `http-only` by default (the origin has no certificate). Traffic between CloudFront and the origin is therefore unencrypted; use `origin_protocol = "https-only"` once the coordinator serves TLS.
- CloudFront 403/404 -> `/index.html` (200) applies to the whole distribution, so API 404/403 responses are also rewritten.
