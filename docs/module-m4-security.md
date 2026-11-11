# M4: Security

## 1. Implementation

Cognito access tokens are validated with cached JWKS, RS256 signature, issuer,
expiry/issued-at, client_id and token_use=access. REST routes and WebSocket
handshakes enforce group roles. Analysts read/create/upload; supervisors also
cancel; administrators manage role membership. WebSockets pass the token as a
subprotocol, not a URL query, and close on expiry. Missing Cognito with a
configured origin secret fails closed. Pure local mode remains explicit.

PostgreSQL statement locks serialize case/subtask mutation before row locks;
AFTER triggers append exact event payloads to a SHA-256 custody chain in the
same transaction. Case creation/cancellation include the authenticated subject.
UPDATE/DELETE/TRUNCATE on custody are rejected. Verification streams the chain;
keep a recorded head outside the database (`verify_custody.py --anchor FILE` checks its prefix) to detect malicious privileged full
rewrites. This is tamper evidence, not protection from a database superuser.

AMQPS, RDS TLS, S3 HTTPS/SSE-KMS and non-root/capability-dropped containers remain.
Optional origin TLS adds a validated ACM/Route53 ALB endpoint and encrypts its
connection to a self-signed coordinator target. ALB does not validate the target
certificate; SG restrictions and the app origin secret remain the trust boundary.
CloudFront no longer rewrites API 403/404 responses into SPA success responses.

## 2. OS concept

Least privilege, process isolation, restricted capabilities, authenticated
access and append-only event provenance limit cross-component impact.

## 3. Method

`experiments/m4_security/scripts/check_auth.py` signs synthetic RSA tokens and
checks signature, expiry, issuer, client, token-use and role denials offline.
`verify_custody.py --self-check` detects a changed payload. M1's real PostgreSQL
check creates a rolled-back schema, verifies chained inserts, transaction lock
exclusion and rejected custody mutation. The standalone verify tool checks the
live local chain. `cloud_probe.py` records origin bypass/role checks and a
container non-root/capability inspection when run against a configured target.

## 4. Results

Signed-token/role and synthetic tamper checks passed. Real PostgreSQL admission,
immutable custody and local chains of 370 and later 2976 records were verified during development.
Production images run as UID 10001 and the real-model campaign records their
configured user, cgroup limit and OOM status. The coordinator target served TLS locally, default trust rejected its self-signed certificate, and an actual container reported UID 10001, zero effective capabilities and no-new-privileges. Terraform dev/prod validate.

## 5. Interpretation and cloud work

No live Cognito user login, real AWS role-denial/bypass campaign, SNS email,
ACM validation or TLS rollout has been performed here. Fabi?n must configure
users, issuer/client/pool, apply IAM/network/TLS changes and retain cloud results.
Frontend PKCE/token integration remains Camila work. AWS role changes can take
time to appear in already issued tokens; reconnect/refresh after changing roles.
