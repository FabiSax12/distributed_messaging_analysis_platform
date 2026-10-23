output "coordinator_instance_id" {
  description = "Instance ID of the coordinator."
  value       = aws_instance.coordinator.id
}

output "coordinator_public_ip" {
  description = "Elastic IP of the coordinator."
  value       = aws_eip.coordinator.public_ip
}

output "coordinator_public_dns" {
  description = "Public DNS name of the coordinator Elastic IP (used as CloudFront origin)."
  value       = aws_eip.coordinator.public_dns
}

output "worker_instance_ids" {
  description = "Map of worker node name to instance ID."
  value       = { for k, v in aws_instance.worker : k => v.id }
}

output "coordinator_role_arn" {
  description = "ARN of the coordinator IAM role."
  value       = aws_iam_role.coordinator.arn
}

output "worker_role_arn" {
  description = "ARN of the shared worker IAM role."
  value       = aws_iam_role.worker.arn
}
