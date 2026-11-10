#!/usr/bin/env sh
# Runs ON an instance, as root, through SSM. Not meant to be run by hand:
# deploy-app.sh prepends the `export` lines it needs (and the compose file as
# COMPOSE_B64) and ships this script with AWS-RunShellScript.
#
# Required environment: NODE_ROLE (coordinator|worker), POOL (workers only),
# IMAGE_TAG, ECR_REGISTRY, ECR_REPO_PREFIX, AWS_REGION, LOG_GROUP, S3_BUCKET,
# RDS_ENDPOINT, RDS_DB_NAME, RDS_SECRET_ARN, MQ_ENDPOINT, MQ_SECRET_ARN,
# ORIGIN_VERIFY_SECRET_ARN (coordinator only), COMPOSE_B64.
#
# Credentials are read here from Secrets Manager with the instance role and
# written to /opt/dmap/app.env (mode 600). They never travel in the SSM payload
# and are never printed.
set -eu
umask 077
trap 'rm -f "$0"' EXIT

: "${NODE_ROLE:?}" "${IMAGE_TAG:?}" "${ECR_REGISTRY:?}" "${ECR_REPO_PREFIX:?}"
: "${AWS_REGION:?}" "${LOG_GROUP:?}" "${S3_BUCKET:?}" "${RDS_ENDPOINT:?}"
: "${RDS_DB_NAME:?}" "${RDS_SECRET_ARN:?}" "${MQ_ENDPOINT:?}" "${MQ_SECRET_ARN:?}"
: "${COMPOSE_B64:?}"

APP_DIR=/opt/dmap
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-120}"
HOME="${HOME:-/root}"
export HOME

if [ "$NODE_ROLE" = coordinator ]; then
  : "${ORIGIN_VERIFY_SECRET_ARN:?}"
  POOL=""
  STOP_GRACE_PERIOD=30s
else
  : "${POOL:?}"
  # Longer than the pool's op timeout (workers finish the current sub-task on SIGTERM).
  case "$POOL" in
    audio) STOP_GRACE_PERIOD=630s ;;
    *) STOP_GRACE_PERIOD=150s ;;
  esac
fi

echo "[deploy] role=$NODE_ROLE pool=${POOL:-n/a} tag=$IMAGE_TAG"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

fetch_secret() {
  aws secretsmanager get-secret-value --region "$AWS_REGION" \
    --secret-id "$1" --query SecretString --output text
}

# Application environment (with credentials), written atomically.
RDS_SECRET_JSON="$(fetch_secret "$RDS_SECRET_ARN")"
MQ_SECRET_JSON="$(fetch_secret "$MQ_SECRET_ARN")"
ORIGIN_VERIFY_SECRET_VALUE=""
if [ "$NODE_ROLE" = coordinator ]; then
  ORIGIN_VERIFY_SECRET_VALUE="$(fetch_secret "$ORIGIN_VERIFY_SECRET_ARN")"
fi
export RDS_SECRET_JSON MQ_SECRET_JSON ORIGIN_VERIFY_SECRET_VALUE

# Values are single-quoted: Docker Compose's env_file would otherwise expand $
# and treat # as a comment, and the generated passwords contain both.
python3 - >app.env.new <<'PY'
import json
import os
import sys
from urllib.parse import quote


def emit(key, value):
    if "'" in value or "\n" in value:
        sys.exit(f"{key}: value contains a quote or newline")
    print(f"{key}='{value}'")


def enc(value):
    return quote(value, safe="")


env = os.environ
rds = json.loads(env["RDS_SECRET_JSON"])
mq = json.loads(env["MQ_SECRET_JSON"])
amqp_host = env["MQ_ENDPOINT"].split("://", 1)[-1].rstrip("/")

emit("AMQP_URL", f"amqps://{enc(mq['username'])}:{enc(mq['password'])}@{amqp_host}")
emit(
    "DATABASE_URL",
    f"postgresql://{enc(rds['username'])}:{enc(rds['password'])}"
    f"@{env['RDS_ENDPOINT']}/{env['RDS_DB_NAME']}?sslmode=require",
)
emit("S3_BUCKET", env["S3_BUCKET"])
emit("AWS_REGION", env["AWS_REGION"])
config = json.loads(env.get("RUNTIME_CONFIG_JSON") or "{}")
node = "coordinator" if env["NODE_ROLE"] == "coordinator" else env["POOL"]
if node == "coordinator":
    required = {"COGNITO_ISSUER_URL", "COGNITO_CLIENT_ID", "COGNITO_USER_POOL_ID", "ALERTS_TOPIC_ARN"}
    missing = [name for name in sorted(required) if not config.get(node, {}).get(name)]
    if missing:
        sys.exit("RUNTIME_CONFIG_JSON.coordinator missing: " + ", ".join(missing))
allowed = {"M1_MAX_ACTIVE_CASES", "M1_MAX_CASE_INPUT_BYTES", "M1_MEMORY_BUDGET_BYTES",
 "M1_TASK_BASE_BYTES", "M1_OPERATION_BASE_BYTES", "M1_INPUT_MEMORY_MULTIPLIER", "M1_RETRY_AFTER_S",
 "COORDINATOR_TLS", "COGNITO_ISSUER_URL", "COGNITO_CLIENT_ID", "COGNITO_USER_POOL_ID", "ALERTS_TOPIC_ARN",
 "SCHEDULER_POLICY", "SCHEDULER_AGING_S", "OUTBOX_QUEUE_LIMIT", "WORKER_CONCURRENCY",
 "OP_TIMEOUT_S", "MODEL_DEVICE", "MODEL_THREADS", "HF_HUB_OFFLINE", "AUDIO_MODEL", "AUDIO_COMPUTE_TYPE",
 "OMP_NUM_THREADS", "MKL_NUM_THREADS", "BROKER_MAX_LENGTH", "BROKER_MANAGEMENT_URL"}
defaults = {"M1_MAX_ACTIVE_CASES": 20} if node == "coordinator" else {
 "WORKER_CONCURRENCY": 1, "MODEL_THREADS": 2, "OMP_NUM_THREADS": 2, "MKL_NUM_THREADS": 2}
for key, value in {**defaults, **config.get(node, {})}.items():
    if key in {"WORKER_MEMORY_LIMIT", "WORKER_CPUS", "WORKER_PIDS_LIMIT"}:
        continue
    if key not in allowed:
        sys.exit(f"Unknown runtime setting: {key}")
    emit(key, json.dumps(value) if isinstance(value, dict) else str(value))
if env["NODE_ROLE"] == "coordinator":
    emit("ORIGIN_VERIFY_SECRET", env["ORIGIN_VERIFY_SECRET_VALUE"].rstrip("\n"))
PY
mv app.env.new app.env

# Compose limits derive from the same scoped runtime config.
eval "$(python3 - <<'PY'
import json, os, shlex
pool = os.environ.get("POOL", "text")
config = json.loads(os.environ.get("RUNTIME_CONFIG_JSON") or "{}")
values = config.get(pool, {})
for key, default in {"WORKER_MEMORY_LIMIT": "256m" if pool == "text" else "1g",
                     "WORKER_CPUS": "2", "WORKER_PIDS_LIMIT": "128"}.items():
    print(key+"="+shlex.quote(str(values.get(key, default))))
PY
)"

# Compose interpolation values (no secrets).
if [ -f .env ]; then
  cp .env .env.previous
fi
cat >.env.new <<ENV
ECR_REGISTRY=$ECR_REGISTRY
ECR_REPO_PREFIX=$ECR_REPO_PREFIX
IMAGE_TAG=$IMAGE_TAG
AWS_REGION=$AWS_REGION
LOG_GROUP=$LOG_GROUP
POOL=$POOL
STOP_GRACE_PERIOD=$STOP_GRACE_PERIOD
WORKER_MEMORY_LIMIT=$WORKER_MEMORY_LIMIT
WORKER_CPUS=$WORKER_CPUS
WORKER_PIDS_LIMIT=$WORKER_PIDS_LIMIT
ENV
mv .env.new .env

printf '%s' "$COMPOSE_B64" | base64 -d >compose.yml.new
mv compose.yml.new compose.yml

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null

docker compose pull --quiet
docker compose up -d --remove-orphans

container="$(docker compose ps -q | head -n 1)"
[ -n "$container" ] || {
  echo "[deploy] FAILED: no container was created" >&2
  exit 1
}

# Coordinator: wait for the image HEALTHCHECK. Workers have none, so require
# the container to stay up without restarts for 20 s.
fail() {
  echo "[deploy] FAILED: $1" >&2
  docker compose ps >&2 || true
  docker compose logs --no-color --tail 20 >&2 || true
  echo "[deploy] previous tag, if any: $(grep '^IMAGE_TAG=' .env.previous 2>/dev/null || echo none)" >&2
  exit 1
}

waited=0
while [ "$waited" -lt "$HEALTH_TIMEOUT_S" ]; do
  state="$(docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}' "$container")"
  status="${state%%|*}"
  rest="${state#*|}"
  health="${rest%%|*}"
  restarts="${rest#*|}"

  [ "$status" = running ] || fail "container is $status"
  [ "$restarts" = 0 ] || fail "container restarted $restarts time(s)"
  [ "$health" != unhealthy ] || fail "container is unhealthy"

  if [ "$NODE_ROLE" = coordinator ]; then
    [ "$health" != healthy ] || break
  elif [ "$waited" -ge 20 ]; then
    break
  fi
  sleep 5
  waited=$((waited + 5))
done
[ "$waited" -lt "$HEALTH_TIMEOUT_S" ] || fail "not healthy after ${HEALTH_TIMEOUT_S}s"

if [ "$NODE_ROLE" = coordinator ]; then
  docker compose exec -T coordinator python -m shared.broker_policy || fail "queue policy update failed"
fi

docker image prune -af --filter "until=24h" >/dev/null || true
docker compose ps
echo "[deploy] OK role=$NODE_ROLE pool=${POOL:-n/a} tag=$IMAGE_TAG"
