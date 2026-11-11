#!/usr/bin/env bash
# Downloads the published dataset media next to the committed manifests and
# checks every size and SHA-256 against them.
#
# Usage:  bash scripts/dataset/download-dataset.sh <bucket> [prefix]   (prefix defaults to "dataset/")
set -euo pipefail

BUCKET="${1:?usage: download-dataset.sh <bucket> [prefix]}"
PREFIX="${2-dataset/}"
ROOT="$(cd "$(dirname "$0")/../../dataset" && pwd)"

aws s3 sync "s3://${BUCKET}/${PREFIX}cases" "$ROOT/cases"
uv run --group dataset python "$ROOT/generators/validate_manifests.py" --verify-files
