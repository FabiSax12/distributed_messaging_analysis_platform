# Module: cdn

CloudFront distribution: SPA from S3 (OAC) and /api/* proxied to the coordinator (WebSocket capable).

## Inputs

- `name_prefix`, `frontend_bucket_id`, `frontend_bucket_arn`, `frontend_bucket_regional_domain_name`.
- `coordinator_origin_domain`, `coordinator_port` (8000), `origin_protocol` (http-only).
- `origin_verify_secret` (sensitive), `price_class` (PriceClass_100).

## Outputs

- `distribution_id`, `distribution_arn`, `domain_name`, `url`.

## Resources

Origin access control, distribution (default root object index.html, /api/* behavior with CachingDisabled + AllViewerExceptHostHeader, X-Origin-Verify header, 403/404 -> /index.html 200), frontend bucket policy.
