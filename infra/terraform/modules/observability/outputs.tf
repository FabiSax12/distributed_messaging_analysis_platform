output "alerts_topic_arn" {
  description = "ARN of the alerts SNS topic."
  value       = aws_sns_topic.alerts.arn
}

output "alerts_topic_name" {
  description = "Name of the alerts SNS topic."
  value       = aws_sns_topic.alerts.name
}

output "log_group_names" {
  description = "Map of component name to log group name."
  value       = { for k, v in aws_cloudwatch_log_group.this : k => v.name }
}
