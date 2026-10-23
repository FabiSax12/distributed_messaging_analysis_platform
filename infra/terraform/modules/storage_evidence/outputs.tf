output "bucket_name" {
  description = "Name of the evidence bucket."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "ARN of the evidence bucket."
  value       = aws_s3_bucket.this.arn
}
