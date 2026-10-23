data "aws_caller_identity" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Names are decided here so that compute and observability can both use them
  # without depending on each other (breaks the compute <-> observability cycle).
  alerts_topic_name = "${local.name_prefix}-alerts"
  log_components    = concat(["coordinator"], [for k in keys(var.worker_nodes) : "worker-${k}"])
  log_group_names   = { for c in local.log_components : c => "/${var.project_name}/${var.environment}/${c}" }
}

module "secrets_kms" {
  source = "../../modules/secrets_kms"

  name_prefix                 = local.name_prefix
  secret_recovery_window_days = var.secret_recovery_window_days
}

module "network" {
  source = "../../modules/network"

  name_prefix        = local.name_prefix
  enable_nat_gateway = var.enable_nat_gateway
  single_nat_gateway = var.single_nat_gateway
}

module "registry" {
  source = "../../modules/registry"

  name_prefix  = local.name_prefix
  force_delete = var.force_destroy
}

module "storage_evidence" {
  source = "../../modules/storage_evidence"

  name_prefix   = local.name_prefix
  kms_key_arn   = module.secrets_kms.kms_key_arn
  force_destroy = var.force_destroy
}

module "storage_frontend" {
  source = "../../modules/storage_frontend"

  name_prefix   = local.name_prefix
  force_destroy = var.force_destroy
}

module "messaging" {
  source = "../../modules/messaging"

  name_prefix        = local.name_prefix
  subnet_ids         = module.network.private_subnet_ids
  security_group_ids = [module.network.mq_security_group_id]
  username           = module.secrets_kms.mq_username
  password           = module.secrets_kms.mq_password
  host_instance_type = var.mq_instance_type
}

module "database" {
  source = "../../modules/database"

  name_prefix         = local.name_prefix
  subnet_ids          = module.network.private_subnet_ids
  security_group_ids  = [module.network.rds_security_group_id]
  kms_key_arn         = module.secrets_kms.kms_key_arn
  master_username     = module.secrets_kms.rds_master_username
  master_password     = module.secrets_kms.rds_master_password
  instance_class      = var.rds_instance_class
  multi_az            = var.multi_az
  skip_final_snapshot = false
  deletion_protection = true
}

module "compute" {
  source = "../../modules/compute"

  name_prefix                   = local.name_prefix
  public_subnet_id              = module.network.public_subnet_ids[0]
  private_subnet_ids            = module.network.private_subnet_ids
  coordinator_security_group_id = module.network.coordinator_security_group_id
  workers_security_group_id     = module.network.workers_security_group_id
  coordinator_instance_type     = var.coordinator_instance_type
  worker_nodes                  = var.worker_nodes

  evidence_bucket_arn = module.storage_evidence.bucket_arn
  kms_key_arn         = module.secrets_kms.kms_key_arn
  coordinator_secret_arns = [
    module.secrets_kms.rds_secret_arn,
    module.secrets_kms.mq_secret_arn,
    module.secrets_kms.origin_verify_secret_arn,
  ]
  worker_secret_arns = [
    module.secrets_kms.rds_secret_arn,
    module.secrets_kms.mq_secret_arn,
  ]
  ecr_repository_arns = module.registry.repository_arns
  log_group_names     = local.log_group_names
  alerts_topic_name   = local.alerts_topic_name
}

module "cdn" {
  source = "../../modules/cdn"

  name_prefix                          = local.name_prefix
  frontend_bucket_id                   = module.storage_frontend.bucket_name
  frontend_bucket_arn                  = module.storage_frontend.bucket_arn
  frontend_bucket_regional_domain_name = module.storage_frontend.bucket_regional_domain_name
  coordinator_origin_domain            = module.compute.coordinator_public_dns
  origin_protocol                      = var.origin_protocol
  origin_verify_secret                 = module.secrets_kms.origin_verify_value
}

# No cycle: cdn does not depend on identity, so identity can consume the CloudFront URL.
module "identity" {
  source = "../../modules/identity"

  name_prefix         = local.name_prefix
  domain_prefix       = var.cognito_domain_prefix
  callback_urls       = var.callback_urls
  logout_urls         = var.logout_urls
  extra_callback_urls = [module.cdn.url]
  extra_logout_urls   = [module.cdn.url]
}

module "observability" {
  source = "../../modules/observability"

  name_prefix        = local.name_prefix
  log_group_names    = local.log_group_names
  alerts_topic_name  = local.alerts_topic_name
  alert_email        = var.alert_email
  monthly_budget_usd = var.monthly_budget_usd

  instance_ids = merge(
    { coordinator = module.compute.coordinator_instance_id },
    { for k, v in module.compute.worker_instance_ids : "worker-${k}" => v },
  )
}
