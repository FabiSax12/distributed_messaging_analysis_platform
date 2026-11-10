output "role_arn" {
  description = "ARN of the deploy role (set it as the AWS_DEPLOY_ROLE_ARN GitHub variable)."
  value       = aws_iam_role.deploy.arn
}

output "role_name" {
  description = "Name of the deploy role."
  value       = aws_iam_role.deploy.name
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider the role trusts."
  value       = local.oidc_provider_arn
}

output "trusted_subject" {
  description = "The only OIDC sub claim that can assume the role."
  value       = local.subject
}
