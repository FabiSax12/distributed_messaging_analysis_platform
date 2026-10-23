# Module: storage_frontend

Private S3 bucket for the SPA. Its bucket policy lives in the cdn module (needs the distribution ARN).

## Inputs

- `name_prefix`, `force_destroy` (false).

## Outputs

- `bucket_name`, `bucket_arn`, `bucket_regional_domain_name`.

## Resources

Bucket, SSE-S3 configuration, public access block, ownership controls.
