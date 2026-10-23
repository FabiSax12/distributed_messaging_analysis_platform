output "user_pool_id" {
  description = "ID of the Cognito user pool."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "ARN of the Cognito user pool."
  value       = aws_cognito_user_pool.this.arn
}

output "client_id" {
  description = "ID of the SPA app client."
  value       = aws_cognito_user_pool_client.spa.id
}

output "issuer_url" {
  description = "OIDC issuer URL (used to validate JWTs)."
  value       = "https://${aws_cognito_user_pool.this.endpoint}"
}

output "hosted_ui_domain" {
  description = "Base URL of the Cognito hosted UI."
  value       = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}

output "groups_claim" {
  description = "Name of the token claim carrying group membership."
  value       = "cognito:groups"
}

output "group_names" {
  description = "Names of the created groups."
  value       = keys(aws_cognito_user_group.this)
}
