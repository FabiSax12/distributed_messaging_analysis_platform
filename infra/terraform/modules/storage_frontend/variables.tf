variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "force_destroy" {
  description = "Allow destroying the bucket even if it contains objects (dev only)."
  type        = bool
  default     = false
}
