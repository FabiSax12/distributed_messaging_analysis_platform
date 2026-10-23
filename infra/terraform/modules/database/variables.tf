variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the DB subnet group (at least two AZs)."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security groups attached to the instance."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "CMK used to encrypt storage."
  type        = string
}

variable "master_username" {
  description = "Master username."
  type        = string
}

variable "master_password" {
  description = "Master password."
  type        = string
  sensitive   = true
}

variable "db_name" {
  description = "Name of the initial database."
  type        = string
  default     = "platform"
}

variable "engine_version" {
  description = "PostgreSQL engine version."
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  description = "Allocated storage in GB (gp3)."
  type        = number
  default     = 20
}

variable "multi_az" {
  description = "Deploy a standby in a second AZ."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
  default     = 1
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy (dev)."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Protect the instance against accidental deletion."
  type        = bool
  default     = false
}
