#!/usr/bin/env bash
# Uploads a local dataset folder to the dataset bucket as 30 copies (batch-01 to batch-30),
# plus dataset/manifest.json when it exists.
#
# NOTE: inherited from Project I (video/audio/metadata files). The messaging dataset
# (text, image, audio; see docs/dataset.md) replaces it, and its layout and manifest
# are not defined yet, so adapt the batch/manifest handling when they are.
#
# Usage:  DATASET_BUCKET=<bucket> bash scripts/dataset/upload-dataset.sh <folder> [bucket]
set -euo pipefail

SRC="${1:?missing the folder with the base files}"
BUCKET="${2:-${DATASET_BUCKET:-}}"
if [ -z "$BUCKET" ]; then
  echo "missing the bucket: pass it as the 2nd argument or set DATASET_BUCKET" >&2
  exit 1
fi
HERE="$(cd "$(dirname "$0")/../../dataset" && pwd)"

for i in $(seq -w 1 30); do
  aws s3 cp "$SRC" "s3://${BUCKET}/batch-${i}/" --recursive --exclude "manifest.json" --exclude "*.sh"
done
if [ -f "$HERE/manifest.json" ]; then
  aws s3 cp "$HERE/manifest.json" "s3://${BUCKET}/manifest.json"
fi
