#!/usr/bin/env sh
# Publishes the dashboard: Vite build -> site bucket -> CloudFront invalidation.
#
# Usage (from frontend/):   npm run deploy
# Reads the bucket and the distribution from `terraform output` in infra/terraform/environments/dev; they
# can also be passed by hand: SITE_BUCKET=<site-bucket> DISTRIBUTION_ID=<distribution-id> npm run deploy
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform/environments/dev"

if [ -z "${SITE_BUCKET:-}" ]; then
  SITE_BUCKET="$(terraform -chdir="$TF_DIR" output -raw frontend_bucket_name)"
fi
if [ -z "${DISTRIBUTION_ID:-}" ]; then
  DISTRIBUTION_ID="$(terraform -chdir="$TF_DIR" output -raw cloudfront_distribution_id)"
fi

echo "Bucket: $SITE_BUCKET · Distribution: $DISTRIBUTION_ID"

cd "$ROOT/frontend"
npm run build

# Assets with a hash in the name: long cache. index.html: always fresh.
aws s3 sync dist "s3://$SITE_BUCKET" --delete --exclude index.html \
  --cache-control "public,max-age=31536000,immutable"
aws s3 cp dist/index.html "s3://$SITE_BUCKET/index.html" \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"

aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/index.html" "/" >/dev/null
echo "Published. The dashboard is served at the CloudFront URL (terraform output cloudfront_url)."
