#!/usr/bin/env bash
# Publishes the generated dataset (docs/dataset.md) to S3 with the layout
# <prefix>cases/<case_id>/<manifest path>, plus every manifest.json and the
# ground truth under <prefix>ground_truth/. Raw media is not in git, so this
# bucket copy (or rerunning the generators) is how others get it.
#
# Usage:  bash scripts/dataset/upload-dataset.sh <bucket> [prefix]
#   prefix defaults to "dataset/" (e.g. s3://<bucket>/dataset/cases/case-001/...).
#   Pass "" to write at the bucket root.
# Terraform has no dedicated dataset bucket; use one agreed with the team. The
# files are synthetic or permissively licensed (see docs/dataset.md).
# Uses `aws s3 sync`, so reruns only upload changed files.
set -euo pipefail

BUCKET="${1:?usage: upload-dataset.sh <bucket> [prefix]}"
PREFIX="${2-dataset/}"
ROOT="$(cd "$(dirname "$0")/../../dataset" && pwd)"

uv run --group dataset python "$ROOT/generators/validate_manifests.py" --verify-files >/dev/null

aws s3 sync "$ROOT/cases" "s3://${BUCKET}/${PREFIX}cases" --exclude "*/build/*"
aws s3 sync "$ROOT/ground_truth" "s3://${BUCKET}/${PREFIX}ground_truth"
echo "published s3://${BUCKET}/${PREFIX}{cases,ground_truth}"
