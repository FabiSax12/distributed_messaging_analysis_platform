# Module: identity

Cognito user pool, groups, public SPA client (authorization code + PKCE) and hosted UI domain.

## Inputs

- `name_prefix`, `domain_prefix`.
- `callback_urls`, `logout_urls` (default http://localhost:5173).
- `extra_callback_urls`, `extra_logout_urls` (for computed URLs such as CloudFront).
- `allow_self_signup` (false), `deletion_protection` (false).

## Outputs

- `user_pool_id`, `user_pool_arn`, `client_id`, `issuer_url`, `hosted_ui_domain`, `groups_claim` (cognito:groups), `group_names`.

## Resources

User pool, groups analyst/supervisor/administrator, app client without secret, user pool domain.
