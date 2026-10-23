data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

# AWS-managed list of CloudFront origin-facing IP ranges.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

locals {
  azs                 = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  public_subnet_cidr  = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnet_cidr = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i + 10)]
  nat_count           = var.enable_nat_gateway ? (var.single_nat_gateway ? 1 : var.az_count) : 0
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_subnet_cidr[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${var.name_prefix}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnet_cidr[count.index]
  availability_zone = local.azs[count.index]

  tags = { Name = "${var.name_prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  count = local.nat_count

  domain = "vpc"

  tags = { Name = "${var.name_prefix}-nat-${count.index}" }
}

resource "aws_nat_gateway" "this" {
  count = local.nat_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = { Name = "${var.name_prefix}-nat-${count.index}" }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route" "private_nat" {
  count = local.nat_count > 0 ? var.az_count : 0

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[var.single_nat_gateway ? 0 : count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Free gateway endpoint: S3 traffic from private subnets bypasses the NAT.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "${var.name_prefix}-s3-endpoint" }
}

# ---------------------------------------------------------------------------
# Security groups (rules are separate resources to avoid circular references)
# ---------------------------------------------------------------------------

resource "aws_security_group" "coordinator" {
  name        = "${var.name_prefix}-coordinator"
  description = "Coordinator: app port reachable only from CloudFront"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-coordinator" }
}

resource "aws_security_group" "workers" {
  name        = "${var.name_prefix}-workers"
  description = "Workers: no inbound traffic"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-workers" }
}

resource "aws_security_group" "mq" {
  name        = "${var.name_prefix}-mq"
  description = "Amazon MQ (RabbitMQ) AMQPS from coordinator and workers"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-mq" }
}

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds"
  description = "RDS PostgreSQL from coordinator and workers"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-rds" }
}

resource "aws_vpc_security_group_ingress_rule" "coordinator_from_cloudfront" {
  security_group_id = aws_security_group.coordinator.id
  description       = "App port from CloudFront origin-facing prefix list"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = var.coordinator_port
  to_port           = var.coordinator_port
}

resource "aws_vpc_security_group_egress_rule" "coordinator_all" {
  security_group_id = aws_security_group.coordinator.id
  description       = "Open egress"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "workers_all" {
  security_group_id = aws_security_group.workers.id
  description       = "Open egress"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

locals {
  client_sgs = {
    coordinator = aws_security_group.coordinator.id
    workers     = aws_security_group.workers.id
  }
}

resource "aws_vpc_security_group_ingress_rule" "mq_amqps" {
  for_each = local.client_sgs

  security_group_id            = aws_security_group.mq.id
  description                  = "AMQPS from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5671
  to_port                      = 5671
}

resource "aws_vpc_security_group_ingress_rule" "rds_postgres" {
  for_each = local.client_sgs

  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from ${each.key}"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
