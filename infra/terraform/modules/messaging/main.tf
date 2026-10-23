resource "aws_mq_broker" "this" {
  broker_name                = "${var.name_prefix}-rabbitmq"
  engine_type                = "RabbitMQ"
  engine_version             = var.engine_version
  host_instance_type         = var.host_instance_type
  deployment_mode            = var.deployment_mode
  publicly_accessible        = false
  auto_minor_version_upgrade = true
  apply_immediately          = true

  subnet_ids      = var.deployment_mode == "SINGLE_INSTANCE" ? [var.subnet_ids[0]] : var.subnet_ids
  security_groups = var.security_group_ids

  logs {
    general = true
  }

  user {
    username = var.username
    password = var.password
  }
}
