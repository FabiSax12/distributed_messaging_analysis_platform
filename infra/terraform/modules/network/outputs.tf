output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets (one per AZ)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets (one per AZ)."
  value       = aws_subnet.private[*].id
}

output "coordinator_security_group_id" {
  description = "Security group of the coordinator instance."
  value       = aws_security_group.coordinator.id
}

output "workers_security_group_id" {
  description = "Security group of the worker instances."
  value       = aws_security_group.workers.id
}

output "mq_security_group_id" {
  description = "Security group of the Amazon MQ broker."
  value       = aws_security_group.mq.id
}

output "rds_security_group_id" {
  description = "Security group of the RDS instance."
  value       = aws_security_group.rds.id
}

output "nat_gateway_count" {
  description = "Number of NAT Gateways created."
  value       = local.nat_count
}
