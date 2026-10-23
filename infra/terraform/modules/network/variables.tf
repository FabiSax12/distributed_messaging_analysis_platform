variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to span (at least 2, required by RDS subnet groups)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2
    error_message = "az_count must be at least 2."
  }
}

variable "enable_nat_gateway" {
  description = "Create NAT Gateway(s) so private subnets have outbound internet access (ECR, SSM, model downloads)."
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Use a single shared NAT Gateway (cheap, dev) instead of one per availability zone (resilient, prod)."
  type        = bool
  default     = true
}

variable "coordinator_port" {
  description = "TCP port the coordinator listens on (reachable only from CloudFront)."
  type        = number
  default     = 8000
}
