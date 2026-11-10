#!/usr/bin/env sh
# Rolls out an image tag to the coordinator and worker instances over SSM.
#
# Usage:   IMAGE_TAG=<tag> scripts/deploy/deploy-app.sh
#
# For every selected node that is SSM-online it runs remote-node.sh (ECR login,
# credentials from Secrets Manager, compose file, `docker compose pull && up -d`,
# health wait). The coordinator is deployed first and alone (it applies the
# database schema at startup); if it fails the workers are not touched. Nodes
# that are not SSM-online (e.g. workers stopped between sessions) are skipped
# and reported, not treated as failures, unless DEPLOY_STRICT=1.
# Redeploying an older IMAGE_TAG is the rollback.
#
# Environment:
#   IMAGE_TAG          tag to deploy (default: git short SHA, "-dirty" if the tree is dirty)
#   DEPLOY_NODES       space-separated subset of "coordinator text image audio" (default: all)
#   DEPLOY_STRICT      1 = exit non-zero if any selected node was skipped (default 0)
#   DRY_RUN            1 = resolve everything and check SSM status, send nothing (default 0)
#   DEPLOY_TIMEOUT_S   per-node wait for the command result (default 900)
#
# Instance targeting (first match wins):
#   COORDINATOR_INSTANCE_ID, WORKER_INSTANCE_IDS ("text=i-abc,image=i-def,audio=i-123")
#   INSTANCE_DISCOVERY=tags  look up instances by EC2 tags Project=$TAG_PROJECT
#                            (default msg-analysis), Environment=$TAG_ENVIRONMENT (default dev),
#                            Role=coordinator|worker-<pool>   [needs ec2:DescribeInstances]
#   terraform outputs        coordinator_instance_id, worker_instance_ids
#   (without terraform outputs and without explicit IDs it falls back to tags)
#
# Configuration (each overridable by the environment variable of the same name,
# otherwise read from terraform outputs, see common.sh; in CI set TF_OUTPUT_JSON):
#   ECR_REGISTRY, ECR_REPO_PREFIX     ecr_repository_urls
#   AWS_REGION                        AWS_DEFAULT_REGION, else derived from ECR_REGISTRY
#   S3_BUCKET                         evidence_bucket_name
#   RDS_ENDPOINT, RDS_DB_NAME         rds_endpoint (host:port), rds_db_name
#   MQ_ENDPOINT                       mq_endpoint (amqps://host:5671)
#   RDS_SECRET_ARN, MQ_SECRET_ARN, ORIGIN_VERIFY_SECRET_ARN    secret_arns.{rds,mq,origin_verify}
#   LOG_GROUP_PREFIX                  dirname of log_group_names.coordinator (e.g. /msg-analysis/dev)
set -eu

# shellcheck source=common.sh source-path=SCRIPTDIR
. "$(dirname "$0")/common.sh"

IMAGE_TAG="${IMAGE_TAG:-$(default_image_tag)}"
DEPLOY_NODES="${DEPLOY_NODES:-coordinator text image audio}"
DEPLOY_STRICT="${DEPLOY_STRICT:-0}"
DRY_RUN="${DRY_RUN:-0}"
DEPLOY_TIMEOUT_S="${DEPLOY_TIMEOUT_S:-900}"
TAG_PROJECT="${TAG_PROJECT:-msg-analysis}"
TAG_ENVIRONMENT="${TAG_ENVIRONMENT:-dev}"

for tool in aws base64; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done

load_tf_outputs
resolve_ecr

S3_BUCKET="${S3_BUCKET:-$(tf_value '.evidence_bucket_name.value')}"
RDS_ENDPOINT="${RDS_ENDPOINT:-$(tf_value '.rds_endpoint.value')}"
RDS_DB_NAME="${RDS_DB_NAME:-$(tf_value '.rds_db_name.value')}"
MQ_ENDPOINT="${MQ_ENDPOINT:-$(tf_value '.mq_endpoint.value')}"
RDS_SECRET_ARN="${RDS_SECRET_ARN:-$(tf_value '.secret_arns.value.rds')}"
MQ_SECRET_ARN="${MQ_SECRET_ARN:-$(tf_value '.secret_arns.value.mq')}"
ORIGIN_VERIFY_SECRET_ARN="${ORIGIN_VERIFY_SECRET_ARN:-$(tf_value '.secret_arns.value.origin_verify')}"
if [ -z "${LOG_GROUP_PREFIX:-}" ]; then
  coordinator_group="$(tf_value '.log_group_names.value.coordinator')"
  LOG_GROUP_PREFIX="${coordinator_group%/*}"
fi

# Plain checks instead of an indirect lookup: the list is short and fixed.
[ -n "$S3_BUCKET" ] || die "S3_BUCKET is not set"
[ -n "$RDS_ENDPOINT" ] || die "RDS_ENDPOINT is not set"
[ -n "$RDS_DB_NAME" ] || die "RDS_DB_NAME is not set"
[ -n "$MQ_ENDPOINT" ] || die "MQ_ENDPOINT is not set"
[ -n "$RDS_SECRET_ARN" ] || die "RDS_SECRET_ARN is not set"
[ -n "$MQ_SECRET_ARN" ] || die "MQ_SECRET_ARN is not set"
[ -n "$LOG_GROUP_PREFIX" ] || die "LOG_GROUP_PREFIX is not set"
case " $DEPLOY_NODES " in
  *" coordinator "*) [ -n "$ORIGIN_VERIFY_SECRET_ARN" ] || die "ORIGIN_VERIFY_SECRET_ARN is not set" ;;
esac

# Prints the instance id of a node, or nothing if it cannot be found.
node_instance_id() {
  node=$1
  if [ "$node" = coordinator ]; then
    role=coordinator
    explicit="${COORDINATOR_INSTANCE_ID:-}"
    filter='.coordinator_instance_id.value'
  else
    role="worker-$node"
    explicit=""
    for pair in $(printf '%s' "${WORKER_INSTANCE_IDS:-}" | tr ',' ' '); do
      if [ "${pair%%=*}" = "$node" ]; then explicit="${pair#*=}"; fi
    done
    filter=".worker_instance_ids.value.\"$node\""
  fi
  if [ -n "$explicit" ]; then
    echo "$explicit"
  elif [ "${INSTANCE_DISCOVERY:-}" = tags ] || [ -z "$TF_JSON" ]; then
    aws ec2 describe-instances \
      --filters "Name=tag:Project,Values=$TAG_PROJECT" "Name=tag:Environment,Values=$TAG_ENVIRONMENT" \
      "Name=tag:Role,Values=$role" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].InstanceId' --output text
  else
    tf_value "$filter"
  fi
}

# Exit status 0 when the SSM agent of the instance reports Online.
ssm_online() {
  status="$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$1" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>&1)" ||
    die "cannot query SSM (credentials or permissions?): $status"
  [ "$status" = Online ]
}

shquote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# Builds the self-contained script for one node (export lines + remote-node.sh),
# base64-encoded on one line. It holds no credentials: the node reads those from
# Secrets Manager itself.
build_script() {
  node=$1
  if [ "$node" = coordinator ]; then
    compose="$ROOT/infra/docker/compose.coordinator.yml"
    role=coordinator
    pool=""
    component=coordinator
  else
    compose="$ROOT/infra/docker/compose.worker.yml"
    role=worker
    pool="$node"
    component="worker-$node"
  fi
  {
    for pair in \
      "NODE_ROLE=$role" "POOL=$pool" "IMAGE_TAG=$IMAGE_TAG" \
      "ECR_REGISTRY=$ECR_REGISTRY" "ECR_REPO_PREFIX=$ECR_REPO_PREFIX" "AWS_REGION=$AWS_REGION" \
      "LOG_GROUP=$LOG_GROUP_PREFIX/$component" "S3_BUCKET=$S3_BUCKET" \
      "RDS_ENDPOINT=$RDS_ENDPOINT" "RDS_DB_NAME=$RDS_DB_NAME" "RDS_SECRET_ARN=$RDS_SECRET_ARN" \
      "MQ_ENDPOINT=$MQ_ENDPOINT" "MQ_SECRET_ARN=$MQ_SECRET_ARN" \
      "ORIGIN_VERIFY_SECRET_ARN=$ORIGIN_VERIFY_SECRET_ARN" \
      "RUNTIME_CONFIG_JSON=${RUNTIME_CONFIG_JSON:-}"; do
      printf 'export %s=%s\n' "${pair%%=*}" "$(shquote "${pair#*=}")"
    done
    printf 'export COMPOSE_B64=%s\n' "$(base64 <"$compose" | tr -d '\n')"
    cat "$DEPLOY_DIR/remote-node.sh"
  } | base64 | tr -d '\n'
}

# Sends the deploy command to a node; prints the command id.
send_deploy() {
  payload="$(build_script "$1")"
  aws ssm send-command \
    --instance-ids "$2" \
    --document-name AWS-RunShellScript \
    --comment "deploy $1 $IMAGE_TAG" \
    --parameters "{\"commands\":[\"echo $payload | base64 -d > /tmp/dmap-deploy.sh\",\"sh /tmp/dmap-deploy.sh\"],\"executionTimeout\":[\"$DEPLOY_TIMEOUT_S\"]}" \
    --query Command.CommandId --output text
}

# Prints the last lines of one output field of a command invocation.
show_output() {
  aws ssm get-command-invocation --command-id "$3" --instance-id "$2" \
    --query "$4" --output text | tail -n 25 | sed "s/^/[$1] /"
}

# Waits for a command to finish. Prints its output; non-zero unless it succeeded.
wait_deploy() {
  waited=0
  last_error=""
  while :; do
    if out="$(aws ssm get-command-invocation --command-id "$3" --instance-id "$2" \
      --query Status --output text 2>&1)"; then
      case "$out" in
        Pending | InProgress | Delayed | Cancelling) ;;
        Success)
          show_output "$1" "$2" "$3" StandardOutputContent
          return 0
          ;;
        *)
          echo "[$1] command ended with status $out" >&2
          show_output "$1" "$2" "$3" StandardOutputContent
          show_output "$1" "$2" "$3" StandardErrorContent >&2
          return 1
          ;;
      esac
    else
      last_error="$out" # right after send-command the invocation may not exist yet
    fi
    if [ "$waited" -ge "$((DEPLOY_TIMEOUT_S + 60))" ]; then
      echo "[$1] timed out waiting for the command result ${last_error:+($last_error)}" >&2
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

echo "Deploying IMAGE_TAG=$IMAGE_TAG to: $DEPLOY_NODES (region $AWS_REGION)"

FAILED=""
SKIPPED=""
DEPLOYED=""
PENDING=""
TARGETS=""

# Resolve targets; nodes that are not SSM-online are skipped and reported.
for node in $DEPLOY_NODES; do
  case "$node" in
    coordinator | text | image | audio) ;;
    *) die "unknown node '$node' (expected coordinator, text, image or audio)" ;;
  esac
  instance_id="$(node_instance_id "$node")"
  if [ -z "$instance_id" ] || [ "$instance_id" = None ]; then
    echo "[$node] SKIPPED: no instance found"
    SKIPPED="$SKIPPED $node"
  elif ! ssm_online "$instance_id"; then
    echo "[$node] SKIPPED: $instance_id is not SSM-online (stopped?)"
    SKIPPED="$SKIPPED $node"
  else
    echo "[$node] $instance_id is SSM-online"
    TARGETS="$TARGETS $node=$instance_id"
  fi
done

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY_RUN=1: would deploy to:${TARGETS:- nothing}"
  echo "Skipped (not online):${SKIPPED:- none}"
  exit 0
fi

# Coordinator first, alone.
for target in $TARGETS; do
  node="${target%%=*}"
  instance_id="${target#*=}"
  [ "$node" = coordinator ] || continue
  command_id="$(send_deploy "$node" "$instance_id")"
  echo "[$node] sent command $command_id"
  if wait_deploy "$node" "$instance_id" "$command_id"; then
    DEPLOYED="$DEPLOYED $node"
  else
    FAILED="$FAILED $node"
  fi
done

# Workers in parallel, only if the coordinator did not fail.
if [ -n "$FAILED" ]; then
  echo "Coordinator deploy failed: workers were not touched."
else
  for target in $TARGETS; do
    node="${target%%=*}"
    instance_id="${target#*=}"
    [ "$node" != coordinator ] || continue
    command_id="$(send_deploy "$node" "$instance_id")"
    echo "[$node] sent command $command_id"
    PENDING="$PENDING$node $instance_id $command_id
"
  done
  while read -r node instance_id command_id; do
    [ -n "$node" ] || continue
    if wait_deploy "$node" "$instance_id" "$command_id"; then
      DEPLOYED="$DEPLOYED $node"
    else
      FAILED="$FAILED $node"
    fi
  done <<EOF
$PENDING
EOF
fi

echo "---"
echo "IMAGE_TAG=$IMAGE_TAG"
echo "Deployed:${DEPLOYED:- none}"
echo "Skipped (not online):${SKIPPED:- none}"
echo "Failed:${FAILED:- none}"

[ -z "$FAILED" ] || exit 1
[ -n "$DEPLOYED" ] || die "nothing was deployed"
if [ "$DEPLOY_STRICT" = 1 ] && [ -n "$SKIPPED" ]; then
  die "DEPLOY_STRICT=1 and some nodes were skipped"
fi
