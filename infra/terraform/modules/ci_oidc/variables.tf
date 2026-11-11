variable "name_prefix" {
  description = "Prefix used to name all resources (for example project-env)."
  type        = string
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the role, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "github_subject_prefix" {
  description = "OIDC sub claim prefix GitHub sends for this repository. Null means repo:<github_repository>. Repositories with use_immutable_subject send repo:<owner>@<owner_id>/<name>@<repo_id>; read it from GET /repos/<owner>/<name>/actions/oidc/customization/sub (sub_claim_prefix)."
  type        = string
  default     = null

  validation {
    condition     = var.github_subject_prefix == null || can(regex("^repo:[^*:]+$", var.github_subject_prefix))
    error_message = "github_subject_prefix must look like repo:<owner>/<name> or repo:<owner>@<id>/<name>@<id>, without wildcards or colons."
  }
}

variable "github_branch" {
  description = "Branch whose workflow runs may assume the role (matched exactly, no wildcards)."
  type        = string
  default     = "main"
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider. An account can hold only one provider per URL: set to false and pass existing_oidc_provider_arn if it already exists."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "ARN of an existing token.actions.githubusercontent.com provider. Required when create_oidc_provider is false."
  type        = string
  default     = null
}

variable "ecr_repository_arns" {
  description = "ARNs of the ECR repositories the role may push images to."
  type        = list(string)
}

variable "frontend_bucket_arn" {
  description = "ARN of the S3 bucket that holds the dashboard (published by sync)."
  type        = string
}

variable "distribution_arn" {
  description = "ARN of the CloudFront distribution to invalidate after publishing the dashboard."
  type        = string
}

variable "instance_tag_project" {
  description = "Value of the Project tag on the instances the role may send deploy commands to."
  type        = string
}

variable "instance_tag_environment" {
  description = "Value of the Environment tag on the instances the role may send deploy commands to."
  type        = string
}
