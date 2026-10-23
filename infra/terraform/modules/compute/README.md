# Module: compute

Coordinator (public subnet, Elastic IP) and worker EC2 instances (private subnets), with least-privilege IAM roles. Access only through SSM Session Manager. The application is deployed later by external scripts.

## Inputs

- `name_prefix`, `public_subnet_id`, `private_subnet_ids`, `coordinator_security_group_id`, `workers_security_group_id`.
- `coordinator_instance_type` (t3.small), `coordinator_volume_gb` (30).
- `worker_nodes`: map(object({ instance_type, volume_gb })), default text/image/audio at t3.large, 40 GB.
- `ami_ssm_parameter`, `docker_compose_version`.
- `evidence_bucket_arn`, `kms_key_arn`, `coordinator_secret_arns`, `worker_secret_arns`, `ecr_repository_arns`.
- `log_group_names` (keys: coordinator, worker-<node>), `alerts_topic_name`.

## Outputs

- `coordinator_instance_id`, `coordinator_public_ip`, `coordinator_public_dns`.
- `worker_instance_ids` (map), `coordinator_role_arn`, `worker_role_arn`.

## Resources

2 IAM roles + inline policies + instance profiles, `aws_instance` coordinator + EIP, `aws_instance` workers (for_each). IMDSv2 required, encrypted gp3 root volumes. ARNs of SNS topic and log groups are built from names so this module does not depend on observability.
