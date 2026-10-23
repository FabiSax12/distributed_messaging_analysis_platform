output "broker_id" {
  description = "ID of the Amazon MQ broker."
  value       = aws_mq_broker.this.id
}

output "broker_arn" {
  description = "ARN of the Amazon MQ broker."
  value       = aws_mq_broker.this.arn
}

output "amqps_endpoint" {
  description = "AMQPS endpoint (amqps://host:5671)."
  value       = aws_mq_broker.this.instances[0].endpoints[0]
}
