# Deployment: build, push, deploy

Terraform (`infra/terraform`) builds the infrastructure only. The application is
shipped by the scripts in `scripts/deploy/`, which are non-interactive and
configured through environment variables so that CI can call them as is. The
GitHub Actions workflows (see [CI/CD](#cicd-github-actions)) run exactly these
scripts.

```
build-and-push.sh  ->  ECR (4 repos)  ->  deploy-app.sh  ->  SSM  ->  docker compose pull && up -d
```

| File | Role |
|---|---|
| `infra/docker/{coordinator,worker-text,worker-image,worker-audio}/Dockerfile` | One image per node type, built from the repo root, non-root user (uid 10001). |
| `infra/docker/compose.coordinator.yml`, `infra/docker/compose.worker.yml` | Production compose files (`restart: unless-stopped`, `awslogs` to the Terraform log groups). The worker file is shared by all pools; `POOL` selects the image and the queue. |
| `scripts/deploy/build-and-push.sh` | Builds the images, tags them, pushes them to ECR. |
| `scripts/deploy/deploy-app.sh` | Rolls out a tag to the instances over SSM. |
| `scripts/deploy/remote-node.sh` | The part that runs on the instance (shipped by `deploy-app.sh`; do not run by hand). |
| `scripts/deploy/common.sh` | Shared helpers (terraform outputs, ECR names). |
| `scripts/deploy/publish-dashboard.sh` | Builds the dashboard, syncs it to the site bucket and invalidates CloudFront (`SITE_BUCKET`, `DISTRIBUTION_ID` or terraform outputs). |
| `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` | Checks on pull requests; deploy on push to `main`. |

`infra/docker/compose.dev.yml` is the separate local stack and is untouched.

## Usage

```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)   # optional, this is the default
scripts/deploy/build-and-push.sh                 # build + push the four images
scripts/deploy/deploy-app.sh                     # roll the tag out
```

Local checks without touching AWS: `PUSH=0 scripts/deploy/build-and-push.sh`
builds the images as `dmap-local/<name>:<tag>`; `DRY_RUN=1
scripts/deploy/deploy-app.sh` resolves everything and reports which nodes are
SSM-online, sending nothing (it does call `ssm:DescribeInstanceInformation`).

Rollback is redeploying an older tag: `IMAGE_TAG=<old> scripts/deploy/deploy-app.sh`
(ECR keeps the last `images_to_keep` images, see the `registry` module).

## Where configuration comes from

Every value has an environment variable that wins; otherwise it is read from
`terraform output -json` (local state of `environments/dev`).
Without state, provide the outputs once as `TF_OUTPUT_JSON="$(terraform output -json)"`
(or `TF_OUTPUT_FILE=<path>`; that JSON holds ARNs, endpoints and names only, no
credentials, and `jq` is required to read it) or set the individual variables
below. The GitHub workflow uses the individual variables.

| Variable | Terraform output | Used by |
|---|---|---|
| `IMAGE_TAG` | (default: git short SHA, `-dirty` suffix if the tree is dirty) | both |
| `ECR_REGISTRY`, `ECR_REPO_PREFIX` | `ecr_repository_urls` (`<account>.dkr.ecr.<region>.amazonaws.com`, `<name_prefix>`) | both |
| `AWS_REGION` | (default `AWS_DEFAULT_REGION`, else taken from `ECR_REGISTRY`) | both |
| `S3_BUCKET` | `evidence_bucket_name` | deploy |
| `RDS_ENDPOINT`, `RDS_DB_NAME` | `rds_endpoint` (`host:port`), `rds_db_name` | deploy |
| `MQ_ENDPOINT` | `mq_endpoint` (`amqps://host:5671`) | deploy |
| `RDS_SECRET_ARN`, `MQ_SECRET_ARN`, `ORIGIN_VERIFY_SECRET_ARN` | `secret_arns.{rds,mq,origin_verify}` | deploy |
| `LOG_GROUP_PREFIX` | dirname of `log_group_names.coordinator` (`/msg-analysis/dev`) | deploy |
| `COORDINATOR_INSTANCE_ID`, `WORKER_INSTANCE_IDS` (`text=i-1,image=i-2,audio=i-3`) | `coordinator_instance_id`, `worker_instance_ids` | deploy |
| `INSTANCE_DISCOVERY=tags`, `TAG_PROJECT`, `TAG_ENVIRONMENT` | (EC2 tags `Project`, `Environment`, `Role=coordinator\|worker-<pool>`) | deploy |

Other knobs: `PUSH`, `PUSH_LATEST`, `IMAGES`, `PLATFORM` (default `linux/amd64`)
for `build-and-push.sh`; `DEPLOY_NODES` (subset of `coordinator text image audio`),
`DEPLOY_STRICT`, `DRY_RUN`, `DEPLOY_TIMEOUT_S` for `deploy-app.sh`. Each script
documents them in its header.

Instance targeting: explicit IDs first; with `INSTANCE_DISCOVERY=tags` (or when
there are neither explicit IDs nor terraform outputs) instances are found by tag.
Tags are more robust in CI than stored IDs because an instance can be replaced
(for example when the audio node switches to GPU).

## What happens on a node

`deploy-app.sh` sends `remote-node.sh` with `AWS-RunShellScript`. The payload
contains configuration only, never credentials. On the instance (as root):

1. Reads the RDS, Amazon MQ and (coordinator) origin-verify secrets from Secrets
   Manager with the instance role and writes `/opt/dmap/app.env` (mode 600):
   `AMQP_URL`, `DATABASE_URL`, `S3_BUCKET`, `AWS_REGION`, `ORIGIN_VERIFY_SECRET`.
   Passwords are URL-encoded; values are single-quoted because the generated
   passwords contain `$` and `#`. AWS credentials are not set: boto3 uses the
   instance role (IMDS hop limit is 2, so containers can reach it).
2. Writes `/opt/dmap/.env` (compose variables, previous one kept as
   `.env.previous`) and `/opt/dmap/compose.yml`.
3. ECR login, `docker compose pull`, `docker compose up -d --remove-orphans`.
4. Waits for health: the coordinator for its image `HEALTHCHECK` (`/healthz`),
   workers (no port) for 20 s without restarts. On failure it prints
   `docker compose ps` and the last log lines and the run fails.
5. Prunes unused images older than 24 h.

The coordinator is deployed first and alone (it applies the database schema at
startup); if it fails, workers are not touched. Workers roll out in parallel.
Logs go to CloudWatch (`awslogs`, non-blocking) in `/msg-analysis/dev/coordinator`
and `/msg-analysis/dev/worker-<pool>`; stream name `<container>/<id>`.

## Stopped instances

Workers are meant to be stopped between sessions. Before sending anything,
`deploy-app.sh` checks each node with `ssm:DescribeInstanceInformation`. A node
that is not `Online` (stopped, or no instance found) is skipped and listed in the
summary; it does not fail the run. Set `DEPLOY_STRICT=1` to fail on any skip. If
no node at all was deployed the script exits non-zero. A skipped node keeps its
old tag: redeploy after starting it.

## CI/CD (GitHub Actions)

Two workflows, no AWS access keys anywhere: the deploy job logs in to AWS with
OIDC and assumes an IAM role that only `main` of this repository can assume.
Terraform is never run by CI (its state is local and holds generated passwords).

| Workflow | When | What |
|---|---|---|
| `ci.yml` | pull requests, pushes to any branch except `main` | `ruff check .`, dataset manifest validation, `shellcheck scripts/deploy/*.sh`, `pnpm install --frozen-lockfile && pnpm build`, `terraform fmt -check` + `terraform validate` (every environment, no backend), `docker build` of the four images (no push, buildx with GitHub cache). Read-only token, no AWS. Superseded runs of the same branch are cancelled. A branch with an open pull request runs once per event (push and pull request). |
| `deploy.yml` | push to `main` (not for markdown/docs-only changes), manual `workflow_dispatch` | OIDC login, `build-and-push.sh`, `deploy-app.sh` (`INSTANCE_DISCOVERY=tags`), `publish-dashboard.sh`, smoke check. One deploy at a time (`concurrency: deploy`, never cancelled: a newer push waits). |

### One-time setup

1. Apply the `ci_oidc` module by hand, with your own admin credentials
   (`modules/ci_oidc` creates the GitHub OIDC provider and the role
   `msg-analysis-dev-ci-deploy`, see its README for the exact permissions):

   ```bash
   cd infra/terraform/environments/dev
   terraform init    # picks up the new module
   terraform plan    # review: only module.ci_oidc and one output should be new
   terraform apply
   ```

   An account holds one OIDC provider per URL. If
   `aws iam list-open-id-connect-providers` already shows
   `token.actions.githubusercontent.com`, set `create_github_oidc_provider = false`
   and `existing_github_oidc_provider_arn = "<that arn>"` in `terraform.tfvars`.
   The trusted repository defaults to `FabiSax12/distributed_messaging_analysis_platform`
   (`github_repository`); the trusted branch is `main`.

2. Set the repository variables (Settings > Secrets and variables > Actions >
   Variables). They are all non-secret (ARNs, endpoints, names): use variables,
   not secrets. `gh` needs admin rights on the repository.

   ```bash
   cd infra/terraform/environments/dev
   REPO=FabiSax12/distributed_messaging_analysis_platform
   OUT="$(terraform output -json)"
   get() { printf '%s' "$OUT" | jq -er "$1"; }
   set_var() { gh variable set "$1" --repo "$REPO" --body "$2" && echo "set $1"; }

   ECR_URL="$(get '.ecr_repository_urls.value.coordinator')"   # <registry>/<prefix>/coordinator
   ECR_PATH="${ECR_URL#*/}"
   ECR_HOST="${ECR_URL%%/*}"                                    # <account>.dkr.ecr.<region>.amazonaws.com
   ECR_REST="${ECR_HOST#*.dkr.ecr.}"

   set_var AWS_REGION               "${ECR_REST%%.*}"
   set_var AWS_DEPLOY_ROLE_ARN      "$(get '.ci_deploy_role_arn.value')"
   set_var ECR_REGISTRY             "$ECR_HOST"
   set_var ECR_REPO_PREFIX          "${ECR_PATH%/coordinator}"
   set_var S3_BUCKET                "$(get '.evidence_bucket_name.value')"
   set_var RDS_ENDPOINT             "$(get '.rds_endpoint.value')"
   set_var RDS_DB_NAME              "$(get '.rds_db_name.value')"
   set_var MQ_ENDPOINT              "$(get '.mq_endpoint.value')"
   set_var RDS_SECRET_ARN           "$(get '.secret_arns.value.rds')"
   set_var MQ_SECRET_ARN            "$(get '.secret_arns.value.mq')"
   set_var ORIGIN_VERIFY_SECRET_ARN "$(get '.secret_arns.value.origin_verify')"
   set_var LOG_GROUP_PREFIX         "$(dirname "$(get '.log_group_names.value.coordinator')")"
   set_var SITE_BUCKET              "$(get '.frontend_bucket_name.value')"
   set_var DISTRIBUTION_ID          "$(get '.cloudfront_distribution_id.value')"
   set_var CLOUDFRONT_URL           "$(get '.cloudfront_url.value')"
   set_var COGNITO_DOMAIN           "$(get '.cognito_hosted_ui_domain.value')"
   set_var COGNITO_CLIENT_ID        "$(get '.cognito_client_id.value')"
   ```

   | Variable | Terraform output | Used for |
   |---|---|---|
   | `AWS_REGION` | region inside `ecr_repository_urls` | OIDC login, every AWS call |
   | `AWS_DEPLOY_ROLE_ARN` | `ci_deploy_role_arn` | role to assume |
   | `ECR_REGISTRY`, `ECR_REPO_PREFIX` | `ecr_repository_urls` | image names (`<registry>/<prefix>/<name>:<tag>`) |
   | `S3_BUCKET` | `evidence_bucket_name` | written into the nodes' `app.env` |
   | `RDS_ENDPOINT`, `RDS_DB_NAME` | `rds_endpoint`, `rds_db_name` | same |
   | `MQ_ENDPOINT` | `mq_endpoint` | same |
   | `RDS_SECRET_ARN`, `MQ_SECRET_ARN`, `ORIGIN_VERIFY_SECRET_ARN` | `secret_arns.{rds,mq,origin_verify}` | the nodes read the secrets themselves with their instance roles |
   | `LOG_GROUP_PREFIX` | directory of `log_group_names.coordinator` | `awslogs` group names |
   | `SITE_BUCKET`, `DISTRIBUTION_ID` | `frontend_bucket_name`, `cloudfront_distribution_id` | dashboard publish |
   | `CLOUDFRONT_URL` | `cloudfront_url` | smoke check |
   | `COGNITO_DOMAIN`, `COGNITO_CLIENT_ID` | `cognito_hosted_ui_domain`, `cognito_client_id` | baked into the dashboard build as `VITE_COGNITO_*` (public SPA client, no secret). Not checked by the first step: without them the published dashboard shows "Sign-in is not configured" and refuses to run unauthenticated |

   The workflow fails in its first step, naming the variables that are missing.
   The values change only if the infrastructure is destroyed and recreated:
   rerun the snippet then.

3. Optional but recommended: protect `main` and require the `CI` checks, so the
   branch that is allowed to deploy only receives code that passed them.

### How a deploy flows

1. A push to `main` starts `deploy.yml`. Its image tag is the short commit SHA.
2. The job assumes `msg-analysis-dev-ci-deploy` with a short-lived OIDC token
   (valid only for `repo:FabiSax12/distributed_messaging_analysis_platform:ref:refs/heads/main`).
3. `build-and-push.sh` builds and pushes the four images.
4. `deploy-app.sh` finds the instances by tag (`Project=msg-analysis`,
   `Environment=dev`, `Role=coordinator|worker-<pool>`), skips those that are not
   SSM-online, deploys the coordinator first and then the workers in parallel.
5. The dashboard is built, synced to the site bucket and `index.html` is
   invalidated.
6. The smoke check calls `GET <CloudFront URL>/api/state` (retries for two
   minutes) and requires the coordinator's JSON state. The request sends no
   `X-Origin-Verify`: CloudFront adds it when it proxies `/api/*`, and the
   check proves that path works end to end. `/healthz` is not used because
   CloudFront only routes `/api/*` to the coordinator, and an origin 403/404
   would come back as `200 index.html` (SPA fallback), which is why the body
   is checked, not only the status.

Manual run: Actions > Deploy > Run workflow (from `main`). `dry_run` builds the
images without pushing, lists which nodes are SSM-online and sends nothing, and
builds the dashboard without publishing; it still logs in to AWS, so it also
tests the OIDC setup.

### Rollback

Run the workflow manually with `image_tag` set to an older tag (the short SHA of
that commit, see `git log` or the ECR console). It skips the build and rolls
that existing tag out to the nodes. The dashboard is not touched in this mode:
roll it back by reverting the commit. ECR keeps only the last `images_to_keep`
images per repository (10 by default), so only recent tags exist. A tag that is
not in ECR fails on the node at `docker compose pull`.

### Cost note: stopped workers

Workers are expected to be stopped between sessions and a deploy never starts
them: a stopped node is reported as skipped and does not fail the run (the run
fails only if a deploy fails, nothing at all was deployed, or the smoke check
fails, which includes a stopped coordinator). A skipped worker keeps its old
tag. After starting it, wait until it is SSM-online and run the workflow
manually with `image_tag` set to the tag currently deployed on the others.

## Assumptions and limits

- The instances come from `user_data.sh.tftpl`: Docker, the Compose plugin
  (v2.29.7, hence no `env_file` `format: raw`), the AWS CLI and `python3`
  (Amazon Linux 2023) and the SSM agent. The compose file is not on the instance
  beforehand; the deploy script writes it to `/opt/dmap/compose.yml`.
- Images are `linux/amd64` (the dev instances are x86_64).
- Worker images install their pool extra; image uses CPU PyTorch and YOLO weights, audio includes cached Whisper tiny and ffmpeg.
- The audio GPU switch (M5) needs a CUDA base image and NVIDIA drivers on the
  node; neither is handled yet (see `infra/terraform/README.md`).
- Not verified against real instances yet (they were stopped when this was
  written): the `remote-node.sh` run, `awslogs` delivery and the SSM round trip.
- The handoff reports a successful OIDC login; the subsequent cloud ECR push failed. The ECR policy fix still needs apply and a new real deploy. The role's `ssm:SendCommand`
  tag condition (`ssm:resourceTag/Project|Environment`) is also untested against
  real instances.
- The deploy builds with plain `docker build` (no layer cache), so every deploy
  rebuilds the four images.

## Runtime settings and manual steps added after initial CI/CD

See [handoff completion](handoff-completion.md) for `RUNTIME_CONFIG_JSON`, Cognito, cgroups, queue policy and optional TLS origin activation. The ECR role now includes manifest-read permissions; apply Terraform before retrying the failing cloud push.
