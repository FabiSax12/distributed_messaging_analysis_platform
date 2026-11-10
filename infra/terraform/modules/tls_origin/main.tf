variable "name_prefix" { type = string }
variable "hostname" { type = string }
variable "zone_id" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "instance_id" { type = string }
variable "coordinator_sg_id" { type = string }

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}
resource "aws_acm_certificate" "origin" {
  domain_name       = var.hostname
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}
resource "aws_route53_record" "validation" {
  for_each        = { for option in aws_acm_certificate.origin.domain_validation_options : option.domain_name => option }
  zone_id         = var.zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}
resource "aws_acm_certificate_validation" "origin" {
  certificate_arn         = aws_acm_certificate.origin.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
}
resource "aws_security_group" "alb" {
  name_prefix = "${var.name_prefix}-tls-"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "cloudfront" {
  security_group_id = aws_security_group.alb.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "target" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = var.coordinator_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
resource "aws_vpc_security_group_ingress_rule" "target" {
  security_group_id            = var.coordinator_sg_id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
resource "aws_lb" "origin" {
  name_prefix        = "mtls-"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids
}
resource "aws_lb_target_group" "coordinator" {
  name_prefix = "mtls-"
  port        = 8000
  protocol    = "HTTPS"
  vpc_id      = var.vpc_id
  health_check { path = "/healthz" }
}
resource "aws_lb_target_group_attachment" "coordinator" {
  target_group_arn = aws_lb_target_group.coordinator.arn
  target_id        = var.instance_id
  port             = 8000
}
resource "aws_lb_listener" "tls" {
  load_balancer_arn = aws_lb.origin.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.origin.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.coordinator.arn
  }
}
resource "aws_route53_record" "origin" {
  zone_id = var.zone_id
  name    = var.hostname
  type    = "A"
  alias {
    name                   = aws_lb.origin.dns_name
    zone_id                = aws_lb.origin.zone_id
    evaluate_target_health = true
  }
}
output "hostname" { value = aws_route53_record.origin.fqdn }
