variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "frontend_bucket_id" {
  description = "Name (ID) of the frontend S3 bucket."
  type        = string
}

variable "frontend_bucket_arn" {
  description = "ARN of the frontend S3 bucket."
  type        = string
}

variable "frontend_bucket_regional_domain_name" {
  description = "Regional domain name of the frontend bucket."
  type        = string
}

variable "coordinator_origin_domain" {
  description = "Public DNS name of the coordinator (Elastic IP DNS)."
  type        = string
}

variable "coordinator_port" {
  description = "Port the coordinator listens on."
  type        = number
  default     = 8000
}

variable "origin_protocol" {
  description = "Protocol CloudFront uses to reach the coordinator: http-only, https-only or match-viewer."
  type        = string
  default     = "http-only"

  validation {
    condition     = contains(["http-only", "https-only", "match-viewer"], var.origin_protocol)
    error_message = "origin_protocol must be http-only, https-only or match-viewer."
  }
}

variable "origin_verify_secret" {
  description = "Value of the X-Origin-Verify header sent to the coordinator."
  type        = string
  sensitive   = true
}

variable "price_class" {
  description = "CloudFront price class."
  type        = string
  default     = "PriceClass_100"
}
