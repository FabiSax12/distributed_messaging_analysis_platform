# Module: registry

ECR repositories for coordinator and the three workers.

## Inputs

- `name_prefix`.
- `repository_names` (coordinator, worker-text, worker-image, worker-audio).
- `force_delete` (true), `images_to_keep` (10).

## Outputs

- `repository_urls`, `repository_arns` (maps by short name).

## Resources

`aws_ecr_repository` (scan on push) and `aws_ecr_lifecycle_policy` per repository.
