# Module: messaging

Amazon MQ for RabbitMQ, single instance, private.

## Inputs

- `name_prefix`, `subnet_ids`, `security_group_ids`.
- `username`, `password` (sensitive).
- `engine_version` (3.13), `host_instance_type` (mq.t3.micro), `deployment_mode` (SINGLE_INSTANCE).

## Outputs

- `broker_id`, `broker_arn`, `amqps_endpoint`.

## Resources

`aws_mq_broker` (not public, general logs enabled).
