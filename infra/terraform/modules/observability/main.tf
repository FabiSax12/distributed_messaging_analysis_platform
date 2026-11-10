resource "aws_cloudwatch_log_group" "this" {
  for_each = var.log_group_names

  name              = each.value
  retention_in_days = var.log_retention_days
}

resource "aws_sns_topic" "alerts" {
  name = var.alerts_topic_name
}

# The recipient must confirm the subscription from the email AWS sends.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  for_each = var.instance_ids

  alarm_name          = "${var.name_prefix}-${each.key}-cpu-high"
  alarm_description   = "CPU above ${var.cpu_alarm_threshold}% on ${each.key}"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = each.value }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.cpu_alarm_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "status_check_failed" {
  for_each = var.instance_ids

  alarm_name          = "${var.name_prefix}-${each.key}-status-check-failed"
  alarm_description   = "EC2 status check failed on ${each.key}"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}

resource "aws_cloudwatch_metric_alarm" "queue" {
  for_each            = toset(["work.text", "work.image", "work.audio", "dead-letter"])
  alarm_name          = "${var.name_prefix}-${each.value}-depth"
  namespace           = "MessagingAnalysis"
  metric_name         = "QueueDepth"
  dimensions          = { Queue = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = each.value == "dead-letter" ? 0 : 100
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_memory" {
  for_each            = toset(["text", "image", "audio"])
  alarm_name          = "${var.name_prefix}-${each.value}-memory"
  namespace           = "MessagingAnalysis"
  metric_name         = "WorkerMemoryPercent"
  dimensions          = { Pool = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

data "aws_region" "dashboard" {}

resource "aws_cloudwatch_dashboard" "application" {
  dashboard_name = "${var.name_prefix}-application"
  dashboard_body = jsonencode({ widgets = [
    { type = "metric", x = 0, y = 0, width = 12, height = 6, properties = {
      title = "Queue depth", region = data.aws_region.dashboard.name, period = 60,
      metrics = [for queue in ["work.text", "work.image", "work.audio", "dead-letter"] :
      ["MessagingAnalysis", "QueueDepth", "Queue", queue]]
    } },
    { type = "metric", x = 12, y = 0, width = 12, height = 6, properties = {
      title = "Container memory percent", region = data.aws_region.dashboard.name, period = 60,
      metrics = [for pool in ["text", "image", "audio"] :
      ["MessagingAnalysis", "WorkerMemoryPercent", "Pool", pool]]
    } }
  ] })
}
