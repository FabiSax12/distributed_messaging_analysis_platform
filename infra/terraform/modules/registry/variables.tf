variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "repository_names" {
  description = "Short names of the ECR repositories to create."
  type        = list(string)
  default     = ["coordinator", "worker-text", "worker-image", "worker-audio"]
}

variable "force_delete" {
  description = "Delete repositories even if they still contain images (convenient in dev)."
  type        = bool
  default     = true
}

variable "images_to_keep" {
  description = "Number of most recent images kept per repository."
  type        = number
  default     = 10
}
