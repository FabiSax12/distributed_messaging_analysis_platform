variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "kms_deletion_window_days" {
  description = "Waiting period before the KMS key is deleted after destroy (7-30)."
  type        = number
  default     = 7
}

variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window. 0 deletes immediately (lets dev be destroyed and re-created with the same names)."
  type        = number
  default     = 0
}

variable "rds_master_username" {
  description = "Master username of the PostgreSQL database."
  type        = string
  default     = "dbadmin"
}

variable "mq_username" {
  description = "Administrator username of the Amazon MQ broker."
  type        = string
  default     = "mqadmin"
}
