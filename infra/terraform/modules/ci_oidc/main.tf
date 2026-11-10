data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  github_oidc_host = "token.actions.githubusercontent.com"
  oidc_provider_arn = (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : var.existing_oidc_provider_arn
  )

  # Only workflow runs on this exact ref get a token whose sub matches.
  subject = "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"
}

# ---------------------------------------------------------------------------
# GitHub OIDC identity provider (one per account)
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]

  # AWS validates GitHub's certificate chain with its own trusted CA library and
  # ignores this value for this provider; it is only here because older
  # provider versions require the argument.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# ---------------------------------------------------------------------------
# Deploy role: assumable only by workflow runs on the deploy branch
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name_prefix}-ci-deploy"
  description          = "GitHub Actions deploy role for ${var.github_repository} (${var.github_branch} only)"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600

  lifecycle {
    precondition {
      condition     = var.create_oidc_provider || var.existing_oidc_provider_arn != null
      error_message = "Set existing_oidc_provider_arn when create_oidc_provider is false."
    }
  }
}

data "aws_iam_policy_document" "deploy" {
  # docker login to ECR; this action does not support resource-level permissions.
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Push manifest checks also require read access to the same four repositories.
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = var.ecr_repository_arns
  }

  # Instance discovery by tag and the "is the node SSM-online" check.
  # Neither action supports resource-level permissions.
  statement {
    sid = "DiscoverNodes"
    actions = [
      "ec2:DescribeInstances",
      "ssm:DescribeInstanceInformation",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "SendCommandDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${local.region}::document/AWS-RunShellScript"]
  }

  # Only this project's instances of this environment can receive commands.
  statement {
    sid       = "SendCommandInstances"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${local.region}:${local.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Project"
      values   = [var.instance_tag_project]
    }

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Environment"
      values   = [var.instance_tag_environment]
    }
  }

  # Polling the result of the deploy command (no resource-level permissions).
  statement {
    sid       = "ReadCommandResult"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  # Dashboard publish: `aws s3 sync --delete` and `aws s3 cp`.
  statement {
    sid       = "DashboardList"
    actions   = ["s3:ListBucket"]
    resources = [var.frontend_bucket_arn]
  }

  statement {
    sid       = "DashboardWrite"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.frontend_bucket_arn}/*"]
  }

  statement {
    sid       = "DashboardInvalidate"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [var.distribution_arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
