#!/usr/bin/env sh
# Shared helpers for the deploy scripts. Source it, do not run it:
#   . "$(dirname "$0")/common.sh"
#
# Configuration resolution order, for every value: explicit environment variable,
# then `terraform output -json` (TF_OUTPUT_JSON / TF_OUTPUT_FILE if set, else
# the dev environment's local state). `terraform output -json` contains no
# secrets (only ARNs, endpoints and names), so CI can keep it as a plain
# variable: TF_OUTPUT_JSON="$(terraform output -json)".

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
TF_DIR="${TF_DIR:-$ROOT/infra/terraform/environments/dev}"

die() {
  echo "error: $*" >&2
  exit 1
}

# Default image tag: short git SHA, with a -dirty suffix when the tree has
# uncommitted changes so a local build never masquerades as a commit.
default_image_tag() {
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)" || die "IMAGE_TAG is not set and git has no commit"
  if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    sha="$sha-dirty"
  fi
  echo "$sha"
}

# Loads the terraform outputs into TF_JSON ("" when unavailable).
load_tf_outputs() {
  TF_JSON=""
  if [ -n "${TF_OUTPUT_JSON:-}" ]; then
    TF_JSON="$TF_OUTPUT_JSON"
  elif [ -n "${TF_OUTPUT_FILE:-}" ]; then
    TF_JSON="$(cat "$TF_OUTPUT_FILE")" || die "cannot read TF_OUTPUT_FILE=$TF_OUTPUT_FILE"
  elif command -v terraform >/dev/null 2>&1 && [ -d "$TF_DIR" ]; then
    TF_JSON="$(terraform -chdir="$TF_DIR" output -json 2>/dev/null)" || TF_JSON=""
  fi
  if [ -n "$TF_JSON" ]; then
    command -v jq >/dev/null 2>&1 || die "jq is required to read terraform outputs"
    # An empty state prints {}: treat it as "no outputs".
    if ! printf '%s' "$TF_JSON" | jq -e 'length > 0' >/dev/null 2>&1; then
      TF_JSON=""
    fi
  fi
}

# tf_value '<jq filter over the outputs>' -> value, or "" when absent.
tf_value() {
  [ -n "$TF_JSON" ] || return 0
  printf '%s' "$TF_JSON" | jq -r "($1) // empty"
}

# Sets ECR_REGISTRY ("<account>.dkr.ecr.<region>.amazonaws.com") and
# ECR_REPO_PREFIX ("<name_prefix>", e.g. msg-analysis-dev) so that an image is
# "$ECR_REGISTRY/$ECR_REPO_PREFIX/<short name>:<tag>".
resolve_ecr() {
  if [ -z "${ECR_REGISTRY:-}" ] || [ -z "${ECR_REPO_PREFIX:-}" ]; then
    url="$(tf_value '.ecr_repository_urls.value.coordinator')"
    [ -n "$url" ] || die "ECR_REGISTRY and ECR_REPO_PREFIX are not set and terraform outputs are unavailable"
    ECR_REGISTRY="${ECR_REGISTRY:-${url%%/*}}"
    path="${url#*/}"
    ECR_REPO_PREFIX="${ECR_REPO_PREFIX:-${path%/coordinator}}"
  fi
  if [ -z "${AWS_REGION:-}" ]; then
    AWS_REGION="${AWS_DEFAULT_REGION:-}"
  fi
  if [ -z "${AWS_REGION:-}" ]; then
    rest="${ECR_REGISTRY#*.dkr.ecr.}"
    AWS_REGION="${rest%%.*}"
  fi
  export AWS_REGION
}

ecr_login() {
  aws ecr get-login-password --region "$AWS_REGION" |
    docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
}
