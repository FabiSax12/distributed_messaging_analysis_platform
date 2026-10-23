variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the CMK used for server-side encryption."
  type        = string
}

variable "force_destroy" {
  description = "Allow destroying the bucket even if it contains objects (dev only)."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Days after which noncurrent object versions are deleted."
  type        = number
  default     = 90
}
