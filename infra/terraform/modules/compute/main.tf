data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_ssm_parameter" "al2023" {
  name = var.ami_ssm_parameter
}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name
  ami_id      = nonsensitive(data.aws_ssm_parameter.al2023.value)
  worker_keys = sort(keys(var.worker_nodes))

  # Built from names so that compute does not need to depend on observability.
  alerts_topic_arn = "arn:aws:sns:${local.region}:${local.account_id}:${var.alerts_topic_name}"
  log_group_arns   = [for name in values(var.log_group_names) : "arn:aws:logs:${local.region}:${local.account_id}:log-group:${name}:*"]
  worker_ecr_arns  = [for k, v in var.ecr_repository_arns : v if k != "coordinator"]
}

# ---------------------------------------------------------------------------
# IAM: one role for the coordinator, one shared by all workers
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "coordinator" {
  name               = "${var.name_prefix}-coordinator"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

locals {
  roles = {
    coordinator = aws_iam_role.coordinator.name
    worker      = aws_iam_role.worker.name
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  for_each = local.roles

  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "common" {
  statement {
    sid       = "EvidenceBucketList"
    actions   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [var.evidence_bucket_arn]
  }

  statement {
    sid = "EvidenceObjectsReadWrite"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${var.evidence_bucket_arn}/*"]
  }

  statement {
    sid       = "UseProjectKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "WriteProjectLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = local.log_group_arns
  }

  # PutMetricData does not support resource-level permissions; scope it by namespace.
  statement {
    sid       = "CloudWatchAgentMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["CWAgent"]
    }
  }

  # GetAuthorizationToken cannot be restricted to a resource.
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "coordinator" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]

  statement {
    sid       = "ReadCoordinatorSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = var.coordinator_secret_arns
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [var.ecr_repository_arns["coordinator"]]
  }

  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [local.alerts_topic_arn]
  }
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.common.json]

  statement {
    sid       = "ReadWorkerSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = var.worker_secret_arns
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = local.worker_ecr_arns
  }
}

resource "aws_iam_role_policy" "coordinator" {
  name   = "project-access"
  role   = aws_iam_role.coordinator.id
  policy = data.aws_iam_policy_document.coordinator.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "project-access"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_instance_profile" "coordinator" {
  name = "${var.name_prefix}-coordinator"
  role = aws_iam_role.coordinator.name
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.name_prefix}-worker"
  role = aws_iam_role.worker.name
}

# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------

resource "aws_instance" "coordinator" {
  ami                    = local.ami_id
  instance_type          = var.coordinator_instance_type
  subnet_id              = var.public_subnet_id
  vpc_security_group_ids = [var.coordinator_security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.coordinator.name

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    compose_version = var.docker_compose_version
    log_group_name  = var.log_group_names["coordinator"]
  })

  # Containers need to reach IMDS too, hence hop limit 2.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.coordinator_volume_gb
    encrypted   = true
  }

  tags = { Name = "${var.name_prefix}-coordinator", Role = "coordinator" }

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip" "coordinator" {
  domain   = "vpc"
  instance = aws_instance.coordinator.id

  tags = { Name = "${var.name_prefix}-coordinator" }
}

resource "aws_instance" "worker" {
  for_each = var.worker_nodes

  ami                    = local.ami_id
  instance_type          = each.value.instance_type
  subnet_id              = var.private_subnet_ids[index(local.worker_keys, each.key) % length(var.private_subnet_ids)]
  vpc_security_group_ids = [var.workers_security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.worker.name

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    compose_version = var.docker_compose_version
    log_group_name  = var.log_group_names["worker-${each.key}"]
  })

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = each.value.volume_gb
    encrypted   = true
  }

  tags = { Name = "${var.name_prefix}-worker-${each.key}", Role = "worker-${each.key}" }

  lifecycle {
    ignore_changes = [ami]
  }
}
