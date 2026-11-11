# Completion status and manual contract

## Code delivered in one PR

- ECR manifest-read permissions, CPU-only PyTorch lock and baked YOLO/Whisper weights.
- CI runs on PR updates or manual dispatch, avoiding the previous duplicate push/PR builds.
- Concurrent manifest load client; local/cloud fault runners with latency and state traces.
- Cgroup-aware heartbeat, compose memory/swap/CPU/PID limits, admission configuration forwarding.
- Killable analysis processes: hard deadline, in-flight cancellation and descendant cleanup.
- FIFO/aging scheduling in the durable outbox; one waiting broker message per pool by default.
- Verified chunk downloads and conditional S3 content/<SHA-256> CAS publication.
- Cognito access-token validation, REST/WebSocket role checks, administrator role API.
- Append-only hash-chained case/subtask events, actor attribution and verification tool.
- Findings/categories/timeline aggregation, severe-finding SNS and operational alerts/metrics.
- Case-finished SNS notification: the `finalize()` call that wins the guarded terminal
  `UPDATE ... RETURNING` publishes one message per case (status, sub-task counts, duration,
  findings and max severity, report key) to the alerts topic after the commit; a publish
  failure is only logged. No-op when `ALERTS_TOPIC_ARN` is unset.
- Application dashboard/alarms, CloudWatch host-memory agent config and optional TLS-origin infrastructure.

The original uploads are retained; CAS dedup is not a claim that total S3 bytes
shrank. Admission estimates are not hard memory guarantees. Isolated workers
load a model per operation; the fork comparison measures idle loaded weights,
not a production inference-sharing implementation. This favors reliable process
termination over persistent native model caches.

## Local verification

Linux Docker/WSL was available even though Docker was absent from Windows PATH.
Four production images build sequentially. Real RabbitMQ/PostgreSQL/MinIO E2E
covered basic, unsupported, retries, worker crash, coordinator restart,
PostgreSQL outage and in-flight cancellation. These fault scenarios use fake
operations. The separate real-model smoke/load script checks actual CPU models,
report findings and timeline under cgroup limits. Raw outcomes are stored under
`experiments/fault_tests/burst/results/`; M1 limits/PSS/sharing profiles under
`experiments/m1_memory/results/linux/`; scheduling campaigns under
`experiments/m2_scheduling/results/local/`.

Cached YOLO and Whisper weights also loaded successfully with Docker network disabled. PostgreSQL admission/cross-connection lock and immutable custody were checked in
a transaction that rolled back its temporary schema. Token checks verify real
RSA signatures, issuer, client, access-token use, expiry and role denials without
contacting Cognito. Terraform dev/prod validation is credential-free; it is not
a cloud plan/apply. Run commands are in module/experiment READMEs.

## Fabián: manual steps after merge

1. Use Terraform >= 1.9 (cross-variable TLS validation). Review `terraform plan`, then apply dev. Changes include ECR read permissions,
   Cognito role-management IAM, application metric permissions/alarms, RabbitMQ
   management-port ingress and host-memory agent configuration. Existing EC2
   bootstrap changes may require replacement or an explicit agent-config update;
   inspect the plan before accepting instance replacements.
2. Keep the existing 15 repository variables. Add **RUNTIME_CONFIG_JSON**, a
   nonsecret JSON object scoped by `coordinator`, `text`, `image`, `audio`.
   Credentials stay in Secrets Manager and are fetched on each node.
3. Configure the coordinator's Cognito issuer/client/user-pool IDs and SNS ARN
   from Terraform outputs. Origin protection without Cognito fails closed, so
   configure this before the first real rollout. Create role users manually.
4. If activating origin TLS, provide `origin_tls_hostname` and
   `origin_tls_zone_id` in dev tfvars. The additional ALB, ACM validation and
   Route53 alias are created only when a hostname is set. Set coordinator
   `COORDINATOR_TLS: true` in runtime JSON at the same rollout. This adds ALB
   cost; an owned public DNS zone is required. Without these manual inputs the
   previous HTTP origin remains and must not be described as end-to-end TLS.
   CloudFront validates the ALB certificate; the ALB encrypts to the target
   without validating its self-signed certificate. Target SG and origin secret
   provide the target-access boundary. Global SPA error rewrites are removed
   so API auth errors retain their HTTP status (the SPA uses hash routes).
5. Start nodes, wait for SSM online, run deploy dry run then real deployment.
   Coordinator rollout now applies the mutable RabbitMQ max-length/reject-publish
   policy using the node's broker credentials; no durable queue deletion is
   required. A policy failure fails the rollout. `BROKER_MANAGEMENT_URL` can
   override the derived endpoint if needed.
6. Confirm the SNS subscription and test a real case/role denial via CloudFront.
   Smoke uses `/api/healthz`, with no user token. Confirm CloudWatch metric and
   awslogs delivery, alert mail and SSM behavior. These cloud checks cannot be
   replaced by local tests.
7. Once Camila's dataset is available, upload it and run the full cloud campaigns
   and dataset-wide M3 measurements. Commit CSVs, plots and screenshots with
   actual host/model/configuration metadata. Stop nodes after each session.
8. Supply the delivered Project I commit/tag, instructor feedback and each
   member's own AI-log entries. Do not fabricate authorship or observations.

### Runtime JSON example

Replace the output placeholders before setting the repository variable:

```json
{
  "coordinator": {
    "COGNITO_ISSUER_URL": "<cognito_issuer_url output>",
    "COGNITO_CLIENT_ID": "<cognito_client_id output>",
    "COGNITO_USER_POOL_ID": "<cognito_user_pool_id output>",
    "ALERTS_TOPIC_ARN": "<SNS topic ARN>",
    "COORDINATOR_TLS": false,
    "M1_MAX_ACTIVE_CASES": 20,
    "M1_MAX_CASE_INPUT_BYTES": 1073741824,
    "M1_MEMORY_BUDGET_BYTES": 3221225472,
    "M1_TASK_BASE_BYTES": 67108864,
    "M1_OPERATION_BASE_BYTES": {"image_analyze": 536870912, "audio_analyze": 402653184},
    "M1_INPUT_MEMORY_MULTIPLIER": 4,
    "SCHEDULER_POLICY": "aging",
    "SCHEDULER_AGING_S": 30,
    "OUTBOX_QUEUE_LIMIT": 1
  },
  "text": {"WORKER_MEMORY_LIMIT": "256m", "WORKER_CONCURRENCY": 1, "OP_TIMEOUT_S": 120},
  "image": {"WORKER_MEMORY_LIMIT": "1g", "WORKER_CPUS": 2, "WORKER_PIDS_LIMIT": 128, "WORKER_CONCURRENCY": 1, "OP_TIMEOUT_S": 120},
  "audio": {"WORKER_MEMORY_LIMIT": "1g", "WORKER_CONCURRENCY": 1, "OP_TIMEOUT_S": 600, "MODEL_THREADS": 2, "AUDIO_MODEL": "tiny", "AUDIO_COMPUTE_TYPE": "int8", "MODEL_DEVICE": "cpu"}
}
```

The numeric memory reservations are starting estimates with margin over the
synthetic local profiles, not a production calibration for compressed media.
The cgroup limits are enforced independently. Optional keys include
`M1_RETRY_AFTER_S`, `BROKER_MAX_LENGTH`, `BROKER_MANAGEMENT_URL`, `OMP_NUM_THREADS`,
`MKL_NUM_THREADS`; the deploy rejects unknown application keys. A custom audio
model needs its weights baked or an explicitly accepted runtime download with
`HF_HUB_OFFLINE=0` (the production audio image defaults to offline cached weights).

## Camila: independent deliverables

- Full dataset generators, 40 manifests, ground truth, licensed media and counts.
- Frontend Hosted UI/PKCE, token refresh, REST/WebSocket token handling, role views
  and findings UI. `docs/frontend-contract.md` gives the backend contract.
- M5 measured benchmark and routing decision. CPU threads, audio model/device/
  quantization settings are already exposed. A GPU image/driver remains her
  optional implementation if the benchmark uses GPU.

These tasks do not block backend/local validation; only the final dataset-wide
accuracy/storage/cloud evidence and complete user interface depend on them.
No frontend login, full dataset, M5 results or live cloud rollout is claimed here.
