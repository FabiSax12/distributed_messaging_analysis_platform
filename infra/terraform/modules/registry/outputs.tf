output "repository_urls" {
  description = "Map of short repository name to ECR repository URL."
  value       = { for k, v in aws_ecr_repository.this : k => v.repository_url }
}

output "repository_arns" {
  description = "Map of short repository name to ECR repository ARN."
  value       = { for k, v in aws_ecr_repository.this : k => v.arn }
}
