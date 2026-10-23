output "distribution_id" {
  description = "ID of the CloudFront distribution."
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_arn" {
  description = "ARN of the CloudFront distribution."
  value       = aws_cloudfront_distribution.this.arn
}

output "domain_name" {
  description = "Domain name of the distribution (without scheme)."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "url" {
  description = "Public HTTPS URL of the platform."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}
