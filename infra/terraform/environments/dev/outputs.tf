output "cloudfront_url" {
  description = "Public HTTPS URL of the platform."
  value       = module.cdn.url
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (for cache invalidations after frontend deploys)."
  value       = module.cdn.distribution_id
}

output "evidence_bucket_name" {
  description = "Evidence bucket name."
  value       = module.storage_evidence.bucket_name
}

output "frontend_bucket_name" {
  description = "Frontend bucket name."
  value       = module.storage_frontend.bucket_name
}

output "ecr_repository_urls" {
  description = "ECR repository URLs by component."
  value       = module.registry.repository_urls
}

output "mq_endpoint" {
  description = "Amazon MQ AMQPS endpoint."
  value       = module.messaging.amqps_endpoint
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)."
  value       = module.database.endpoint
}

output "rds_db_name" {
  description = "Initial database name."
  value       = module.database.db_name
}

output "secret_arns" {
  description = "ARNs of the Secrets Manager secrets."
  value = {
    rds           = module.secrets_kms.rds_secret_arn
    mq            = module.secrets_kms.mq_secret_arn
    origin_verify = module.secrets_kms.origin_verify_secret_arn
  }
}

output "kms_key_arn" {
  description = "ARN of the project CMK."
  value       = module.secrets_kms.kms_key_arn
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID."
  value       = module.identity.user_pool_id
}

output "cognito_client_id" {
  description = "Cognito SPA app client ID."
  value       = module.identity.client_id
}

output "cognito_issuer_url" {
  description = "Cognito OIDC issuer URL."
  value       = module.identity.issuer_url
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted UI base URL."
  value       = module.identity.hosted_ui_domain
}

output "coordinator_instance_id" {
  description = "Coordinator instance ID."
  value       = module.compute.coordinator_instance_id
}

output "worker_instance_ids" {
  description = "Worker instance IDs by node name."
  value       = module.compute.worker_instance_ids
}

output "coordinator_public_ip" {
  description = "Elastic IP of the coordinator."
  value       = module.compute.coordinator_public_ip
}

output "alerts_topic_name" {
  description = "SNS alerts topic name."
  value       = module.observability.alerts_topic_name
}

output "log_group_names" {
  description = "CloudWatch log groups by component."
  value       = module.observability.log_group_names
}
