variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs. A single-instance broker uses the first one."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security groups attached to the broker."
  type        = list(string)
}

variable "username" {
  description = "Broker administrator username."
  type        = string
}

variable "password" {
  description = "Broker administrator password."
  type        = string
  sensitive   = true
}

variable "engine_version" {
  description = "RabbitMQ engine version."
  type        = string
  default     = "3.13"
}

variable "host_instance_type" {
  description = "Broker instance type."
  type        = string
  default     = "mq.t3.micro"
}

variable "deployment_mode" {
  description = "SINGLE_INSTANCE or CLUSTER_MULTI_AZ."
  type        = string
  default     = "SINGLE_INSTANCE"
}
