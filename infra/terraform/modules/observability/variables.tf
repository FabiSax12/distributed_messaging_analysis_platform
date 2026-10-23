variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "log_group_names" {
  description = "Map of component name to CloudWatch log group name."
  type        = map(string)
}

variable "log_retention_days" {
  description = "Retention of the log groups in days."
  type        = number
  default     = 14
}

variable "alerts_topic_name" {
  description = "Name of the SNS alerts topic."
  type        = string
}

variable "alert_email" {
  description = "Email address subscribed to the alerts topic and to the budget notification."
  type        = string
}

variable "instance_ids" {
  description = "Map of component name to EC2 instance ID to alarm on. Keys must be known at plan time."
  type        = map(string)
}

variable "cpu_alarm_threshold" {
  description = "CPU utilization percentage that triggers the alarm."
  type        = number
  default     = 80
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
  default     = 60
}
