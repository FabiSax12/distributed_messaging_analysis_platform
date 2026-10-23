output "kms_key_arn" {
  description = "ARN of the CMK."
  value       = aws_kms_key.this.arn
}

output "kms_key_id" {
  description = "ID of the CMK."
  value       = aws_kms_key.this.key_id
}

output "kms_alias_name" {
  description = "Alias of the CMK."
  value       = aws_kms_alias.this.name
}

output "rds_secret_arn" {
  description = "ARN of the RDS master credentials secret (JSON: username, password)."
  value       = aws_secretsmanager_secret.rds.arn
}

output "mq_secret_arn" {
  description = "ARN of the Amazon MQ credentials secret (JSON: username, password)."
  value       = aws_secretsmanager_secret.mq.arn
}

output "origin_verify_secret_arn" {
  description = "ARN of the X-Origin-Verify shared secret."
  value       = aws_secretsmanager_secret.origin_verify.arn
}

output "rds_master_username" {
  description = "Master username for RDS."
  value       = var.rds_master_username
}

output "rds_master_password" {
  description = "Generated master password for RDS."
  value       = random_password.rds.result
  sensitive   = true
}

output "mq_username" {
  description = "Administrator username for Amazon MQ."
  value       = var.mq_username
}

output "mq_password" {
  description = "Generated administrator password for Amazon MQ."
  value       = random_password.mq.result
  sensitive   = true
}

output "origin_verify_value" {
  description = "Value of the X-Origin-Verify header (also stored in Secrets Manager)."
  value       = random_password.origin_verify.result
  sensitive   = true
}
