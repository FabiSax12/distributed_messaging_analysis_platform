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

variable "cors_allowed_origins" {
  description = "Browser origins allowed to use presigned URLs on the bucket (for example the CloudFront URL). Empty means no CORS configuration."
  type        = list(string)
  default     = []
}

variable "cors_max_age_seconds" {
  description = "How long browsers may cache the CORS preflight response."
  type        = number
  default     = 3000
}
