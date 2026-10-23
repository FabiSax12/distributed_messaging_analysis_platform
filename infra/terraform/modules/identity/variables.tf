variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "domain_prefix" {
  description = "Prefix of the Cognito hosted UI domain (must be globally unique in the region)."
  type        = string
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs (static ones, for example http://localhost:5173)."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "logout_urls" {
  description = "Allowed sign-out URLs (static ones)."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "extra_callback_urls" {
  description = "Additional callback URLs computed by the caller (for example the CloudFront URL)."
  type        = list(string)
  default     = []
}

variable "extra_logout_urls" {
  description = "Additional logout URLs computed by the caller (for example the CloudFront URL)."
  type        = list(string)
  default     = []
}

variable "allow_self_signup" {
  description = "Let users register themselves. When false only administrators create users."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the user pool against deletion."
  type        = bool
  default     = false
}
