#!/usr/bin/env sh
# Builds the four images from the repo root and pushes them to ECR.
#
# Usage:   IMAGE_TAG=<tag> scripts/deploy/build-and-push.sh
#
# Environment:
#   IMAGE_TAG      tag to build (default: git short SHA, "-dirty" if the tree is dirty)
#   PUSH_LATEST    1 = also tag and push :latest (default 0)
#   PUSH           0 = build only, tag as dmap-local/<name>:<tag>, no ECR (default 1)
#   IMAGES         space-separated subset (default: coordinator worker-text worker-image worker-audio)
#   PLATFORM       target platform (default linux/amd64: the EC2 nodes are x86_64)
#   ECR_REGISTRY, ECR_REPO_PREFIX, AWS_REGION
#                  override what is read from terraform outputs (see common.sh)
# Needs docker, plus the aws CLI and credentials when PUSH=1.
set -eu

# shellcheck source=common.sh source-path=SCRIPTDIR
. "$(dirname "$0")/common.sh"

IMAGE_TAG="${IMAGE_TAG:-$(default_image_tag)}"
PUSH="${PUSH:-1}"
PUSH_LATEST="${PUSH_LATEST:-0}"
PLATFORM="${PLATFORM:-linux/amd64}"
IMAGES="${IMAGES:-coordinator worker-text worker-image worker-audio}"
GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [ "$PUSH" = 1 ]; then
  load_tf_outputs
  resolve_ecr
  repo_base="$ECR_REGISTRY/$ECR_REPO_PREFIX"
  ecr_login
else
  repo_base="dmap-local"
fi

for name in $IMAGES; do
  dockerfile="$ROOT/infra/docker/$name/Dockerfile"
  [ -f "$dockerfile" ] || die "unknown image '$name' (no $dockerfile)"
  ref="$repo_base/$name"
  echo "==> building $ref:$IMAGE_TAG ($PLATFORM)"
  docker build --platform "$PLATFORM" --build-arg "GIT_SHA=$GIT_SHA" \
    -f "$dockerfile" -t "$ref:$IMAGE_TAG" "$ROOT"
  if [ "$PUSH_LATEST" = 1 ]; then
    docker tag "$ref:$IMAGE_TAG" "$ref:latest"
  fi
  if [ "$PUSH" = 1 ]; then
    echo "==> pushing $ref:$IMAGE_TAG"
    docker push --quiet "$ref:$IMAGE_TAG"
    if [ "$PUSH_LATEST" = 1 ]; then
      docker push --quiet "$ref:latest"
    fi
  fi
done

echo "Done. IMAGE_TAG=$IMAGE_TAG"
