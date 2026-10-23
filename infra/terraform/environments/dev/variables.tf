variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name, used in names and tags."
  type        = string
  default     = "msg-analysis"
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "dev"
}

variable "alert_email" {
  description = "Email that receives alarm and budget notifications."
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Prefix of the Cognito hosted UI domain (globally unique per region)."
  type        = string
}

variable "callback_urls" {
  description = "Static OAuth callback URLs. The CloudFront URL is added automatically."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "logout_urls" {
  description = "Static sign-out URLs. The CloudFront URL is added automatically."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
  default     = 60
}

variable "enable_nat_gateway" {
  description = "Create NAT Gateway(s) for private subnets."
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Share one NAT Gateway across AZs."
  type        = bool
  default     = true
}

variable "coordinator_instance_type" {
  description = "Instance type of the coordinator."
  type        = string
  default     = "t3.small"
}

variable "worker_nodes" {
  description = "Worker nodes. Change audio to g4dn.xlarge for GPU (module M5)."
  type = map(object({
    instance_type = string
    volume_gb     = number
  }))
  default = {
    text  = { instance_type = "t3.large", volume_gb = 40 }
    image = { instance_type = "t3.large", volume_gb = 40 }
    audio = { instance_type = "t3.large", volume_gb = 40 }
  }
}

variable "origin_protocol" {
  description = "Protocol CloudFront uses toward the coordinator (http-only, https-only, match-viewer)."
  type        = string
  default     = "http-only"
}

variable "multi_az" {
  description = "Multi-AZ RDS."
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "mq_instance_type" {
  description = "Amazon MQ broker instance type."
  type        = string
  default     = "mq.t3.micro"
}

variable "force_destroy" {
  description = "Allow deleting non-empty S3 buckets and ECR repositories on destroy (dev convenience)."
  type        = bool
  default     = true
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window (0 = delete immediately)."
  type        = number
  default     = 0
}
