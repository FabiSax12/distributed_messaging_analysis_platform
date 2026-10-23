# Module: storage_evidence

Evidence S3 bucket: versioned, SSE-KMS, private, TLS-only, multipart cleanup.

## Inputs

- `name_prefix`, `kms_key_arn`.
- `force_destroy` (false), `noncurrent_version_expiration_days` (90).

## Outputs

- `bucket_name`, `bucket_arn`.

## Resources

Bucket, versioning, SSE-KMS configuration, public access block, ownership controls, TLS-only bucket policy, lifecycle (abort incomplete multipart uploads after 1 day, expire noncurrent versions).
