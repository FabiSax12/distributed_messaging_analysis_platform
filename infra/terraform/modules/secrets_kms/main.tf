data "aws_caller_identity" "current" {}

# The default key policy delegates access control to IAM in this account.
resource "aws_kms_key" "this" {
  description             = "${var.name_prefix} CMK for evidence bucket, RDS and secrets"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.this.key_id
}

# Special characters are restricted to those accepted by both RDS and Amazon MQ.
resource "random_password" "rds" {
  length           = 24
  special          = true
  override_special = "!#%^*()-_+"
}

resource "random_password" "mq" {
  length           = 24
  special          = true
  override_special = "!#%^*()-_+"
}

# 32 alphanumeric characters: safe to use as an HTTP header value.
resource "random_password" "origin_verify" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "rds" {
  name                    = "${var.name_prefix}/rds-master"
  description             = "Master credentials of the PostgreSQL database"
  kms_key_id              = aws_kms_key.this.arn
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "rds" {
  secret_id = aws_secretsmanager_secret.rds.id
  secret_string = jsonencode({
    username = var.rds_master_username
    password = random_password.rds.result
  })
}

resource "aws_secretsmanager_secret" "mq" {
  name                    = "${var.name_prefix}/mq-credentials"
  description             = "Administrator credentials of the Amazon MQ broker"
  kms_key_id              = aws_kms_key.this.arn
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "mq" {
  secret_id = aws_secretsmanager_secret.mq.id
  secret_string = jsonencode({
    username = var.mq_username
    password = random_password.mq.result
  })
}

resource "aws_secretsmanager_secret" "origin_verify" {
  name                    = "${var.name_prefix}/origin-verify"
  description             = "Shared secret CloudFront sends in X-Origin-Verify to the coordinator"
  kms_key_id              = aws_kms_key.this.arn
  recovery_window_in_days = var.secret_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "origin_verify" {
  secret_id     = aws_secretsmanager_secret.origin_verify.id
  secret_string = random_password.origin_verify.result
}
