# Module: ci_oidc

Lets GitHub Actions deploy without stored AWS keys: an IAM role that only workflow runs of one repository, on one branch, can assume through OIDC.

## Inputs

- `name_prefix`, `github_repository` (`owner/name`), `github_branch` (`main`).
- `create_oidc_provider` (true) and `existing_oidc_provider_arn`: an account can hold only one provider for `token.actions.githubusercontent.com`. If another stack already created it, set the first to false and pass its ARN.
- `ecr_repository_arns`, `frontend_bucket_arn`, `distribution_arn`: scope of the permissions.
- `instance_tag_project`, `instance_tag_environment`: tags of the instances that may receive deploy commands.

## Outputs

- `role_arn`, `role_name`, `oidc_provider_arn`, `trusted_subject`.

## Trust

`sts:AssumeRoleWithWebIdentity` with `aud = sts.amazonaws.com` and `sub = repo:<repo>:ref:refs/heads/<branch>` (exact match). Pull request runs, other branches and forks cannot assume it. Gotcha: a job that declares a GitHub `environment:` gets `sub = repo:<repo>:environment:<name>` instead, which this role rejects; change `local.subject` if you add one.

## Permissions

| Actions | Resource | Used by |
|---|---|---|
| `ecr:GetAuthorizationToken` | `*` | `docker login` to ECR |
| `ecr:BatchCheckLayerAvailability`, `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage` | the ECR repositories | `docker push` |
| `ec2:DescribeInstances`, `ssm:DescribeInstanceInformation` | `*` (no resource-level support) | find nodes by tag, skip nodes that are not SSM-online |
| `ssm:SendCommand` | `AWS-RunShellScript` document | roll out a tag |
| `ssm:SendCommand` | `instance/*` where tags `Project` and `Environment` match | same, limited to this project's instances |
| `ssm:GetCommandInvocation` | `*` (no resource-level support) | wait for the command result |
| `s3:ListBucket`, `s3:PutObject`, `s3:DeleteObject` | the dashboard bucket | `aws s3 sync --delete`, `aws s3 cp` |
| `cloudfront:CreateInvalidation` | the distribution | refresh `index.html` |

No pull permissions on ECR, no Terraform, no IAM, no Secrets Manager: the instances read their own secrets with their instance roles.

## Resources

`aws_iam_openid_connect_provider` (optional), `aws_iam_role`, `aws_iam_role_policy`.
