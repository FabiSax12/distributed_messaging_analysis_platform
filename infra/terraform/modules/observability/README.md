# Module: observability

Log groups, SNS alerts topic, EC2 alarms and monthly budget.

## Inputs

- `name_prefix`, `log_group_names` (map), `log_retention_days` (14).
- `alerts_topic_name`, `alert_email`.
- `instance_ids` (map name -> instance ID; keys must be known at plan time).
- `cpu_alarm_threshold` (80), `monthly_budget_usd` (60).

## Outputs

- `alerts_topic_arn`, `alerts_topic_name`, `log_group_names`.

## Resources

Log groups, SNS topic + email subscription (must be confirmed by email), CPU and StatusCheckFailed alarms per instance, `aws_budgets_budget` with an 80% notice.
