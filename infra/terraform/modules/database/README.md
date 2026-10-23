# Module: database

RDS PostgreSQL 16, encrypted with the CMK, private.

## Inputs

- `name_prefix`, `subnet_ids`, `security_group_ids`, `kms_key_arn`.
- `master_username`, `master_password` (sensitive), `db_name` (platform).
- `engine_version` (16), `instance_class` (db.t4g.micro), `allocated_storage_gb` (20), `multi_az` (false).
- `backup_retention_days` (1), `skip_final_snapshot` (true), `deletion_protection` (false).

## Outputs

- `endpoint`, `address`, `port`, `db_name`.

## Resources

`aws_db_subnet_group`, `aws_db_instance` (gp3, encrypted, not public).
