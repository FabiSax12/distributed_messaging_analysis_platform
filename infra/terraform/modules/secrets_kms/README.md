# Module: secrets_kms

Customer managed KMS key (rotation on) and Secrets Manager secrets with generated values.

## Inputs

- `name_prefix`.
- `kms_deletion_window_days` (7), `secret_recovery_window_days` (0).
- `rds_master_username` (dbadmin), `mq_username` (mqadmin).

## Outputs

- `kms_key_arn`, `kms_key_id`, `kms_alias_name`.
- `rds_secret_arn`, `mq_secret_arn`, `origin_verify_secret_arn`.
- `rds_master_username`, `mq_username`.
- Sensitive: `rds_master_password`, `mq_password`, `origin_verify_value`.

## Resources

`aws_kms_key`, `aws_kms_alias`, 3 `random_password`, 3 `aws_secretsmanager_secret` + versions. Generated values live in Terraform state: protect it.
