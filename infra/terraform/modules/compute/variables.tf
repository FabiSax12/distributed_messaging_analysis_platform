variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet for the coordinator."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnets for the workers (spread round-robin)."
  type        = list(string)
}

variable "coordinator_security_group_id" {
  description = "Security group of the coordinator."
  type        = string
}

variable "workers_security_group_id" {
  description = "Security group of the workers."
  type        = string
}

variable "coordinator_instance_type" {
  description = "Instance type of the coordinator."
  type        = string
  default     = "t3.small"
}

variable "coordinator_volume_gb" {
  description = "Root volume size of the coordinator in GB."
  type        = number
  default     = 30
}

variable "worker_nodes" {
  description = "Worker nodes keyed by name. Each one becomes a separate EC2 instance."
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

variable "ami_ssm_parameter" {
  description = "SSM parameter that resolves to the AMI ID."
  type        = string
  default     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

variable "docker_compose_version" {
  description = "Docker Compose release installed as CLI plugin."
  type        = string
  default     = "v2.29.7"
}

variable "evidence_bucket_arn" {
  description = "ARN of the evidence bucket."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the CMK protecting the evidence bucket and secrets."
  type        = string
}

variable "coordinator_secret_arns" {
  description = "Secrets the coordinator may read."
  type        = list(string)
}

variable "worker_secret_arns" {
  description = "Secrets the workers may read."
  type        = list(string)
}

variable "ecr_repository_arns" {
  description = "Map of short ECR repository name to ARN. The key 'coordinator' is pulled by the coordinator, all others by workers."
  type        = map(string)
}

variable "log_group_names" {
  description = "Map of component name (coordinator, worker-<node>) to log group name."
  type        = map(string)
}

variable "alerts_topic_name" {
  description = "Name of the alerts SNS topic (the ARN is built from it, so compute does not depend on observability)."
  type        = string
}
