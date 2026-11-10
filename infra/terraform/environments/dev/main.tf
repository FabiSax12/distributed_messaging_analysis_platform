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
  skip_final_snapshot = true
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

  cognito_user_pool_arn = module.identity.user_pool_arn
  evidence_bucket_arn   = module.storage_evidence.bucket_arn
  kms_key_arn           = module.secrets_kms.kms_key_arn
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
  source     = "../../modules/cdn"
  depends_on = [module.tls_origin]

  name_prefix                          = local.name_prefix
  frontend_bucket_id                   = module.storage_frontend.bucket_name
  frontend_bucket_arn                  = module.storage_frontend.bucket_arn
  frontend_bucket_regional_domain_name = module.storage_frontend.bucket_regional_domain_name
  coordinator_origin_domain            = var.origin_tls_hostname != "" ? var.origin_tls_hostname : module.compute.coordinator_public_dns
  coordinator_port                     = var.origin_tls_hostname != "" ? 443 : 8000
  origin_protocol                      = var.origin_tls_hostname != "" ? "https-only" : var.origin_protocol
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

# GitHub Actions deploys through OIDC (no stored keys). Terraform itself is not run by CI.
module "ci_oidc" {
  source = "../../modules/ci_oidc"

  name_prefix                = local.name_prefix
  github_repository          = var.github_repository
  create_oidc_provider       = var.create_github_oidc_provider
  existing_oidc_provider_arn = var.existing_github_oidc_provider_arn

  ecr_repository_arns      = values(module.registry.repository_arns)
  frontend_bucket_arn      = module.storage_frontend.bucket_arn
  distribution_arn         = module.cdn.distribution_arn
  instance_tag_project     = var.project_name
  instance_tag_environment = var.environment
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

module "tls_origin" {
  count             = var.origin_tls_hostname != "" ? 1 : 0
  source            = "../../modules/tls_origin"
  name_prefix       = local.name_prefix
  hostname          = var.origin_tls_hostname
  zone_id           = var.origin_tls_zone_id
  vpc_id            = module.network.vpc_id
  subnet_ids        = module.network.public_subnet_ids
  instance_id       = module.compute.coordinator_instance_id
  coordinator_sg_id = module.network.coordinator_security_group_id
}
