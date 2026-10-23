# Environment: prod (TEMPLATE ONLY)

> **This environment is a template. It is not intended to be deployed as-is.**
> It was only checked with `terraform validate`; it has never been planned or applied.
> Review sizes, costs and security settings before using it.

Same wiring as `../dev`, with these differences:

| Setting | dev | prod |
|---|---|---|
| RDS `multi_az` | false | true |
| RDS class / deletion protection / final snapshot | db.t4g.micro / off / skipped | db.t4g.small / on / taken |
| NAT Gateways | one shared | one per AZ (`single_nat_gateway = false`) |
| Coordinator / workers | t3.small / t3.large 40 GB | t3.medium / t3.xlarge 100 GB |
| Amazon MQ type | mq.t3.micro | mq.m5.large (still SINGLE_INSTANCE; use `deployment_mode = "CLUSTER_MULTI_AZ"` in the messaging module for real HA) |
| `force_destroy` / secret recovery window | true / 0 days | false / 7 days |
| Budget | 60 USD | 400 USD |

Coordinator is still a single EC2 instance (no autoscaling), so this is not a full HA design.
