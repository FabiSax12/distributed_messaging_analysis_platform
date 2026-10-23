# Module: network

VPC across 2 AZs with public/private subnets, NAT, S3 gateway endpoint and security groups.

## Inputs

- `name_prefix`: resource name prefix.
- `vpc_cidr` (10.0.0.0/16), `az_count` (2).
- `enable_nat_gateway` (true), `single_nat_gateway` (true; false = one NAT per AZ).
- `coordinator_port` (8000).

## Outputs

- `vpc_id`, `public_subnet_ids`, `private_subnet_ids`, `nat_gateway_count`.
- `coordinator_security_group_id`, `workers_security_group_id`, `mq_security_group_id`, `rds_security_group_id`.

## Resources

VPC, IGW, subnets, route tables, NAT Gateway(s) + EIP(s), S3 gateway endpoint, 4 security groups with standalone rule resources. The coordinator accepts its port only from the CloudFront origin-facing managed prefix list.
