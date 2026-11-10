# B.6 runners

Run from the repository root after starting the infrastructure and real workers.
`uv run --all-packages python experiments/fault_tests/<scenario>/run.py --help`
describes each runner. The common driver uses the manifest load client and
writes per-case outcome/latency CSV, injection metadata JSON and sampled API
state JSONL (workers/container memory and queues). `ACCESS_TOKEN` supplies a
Cognito access token; it is never sent to S3 presigned URLs or written to results.

```sh
uv run --all-packages python experiments/fault_tests/burst/run.py dataset/cases/case-001/manifest.json --url https://CLOUDFRONT --out experiments/fault_tests/burst/results/cases.csv
uv run --all-packages python experiments/fault_tests/worker_crash/run.py MANIFEST --container CONTAINER --instance i-WORKER --out experiments/fault_tests/worker_crash/results/cases.csv
uv run --all-packages python experiments/fault_tests/coordinator_restart/run.py MANIFEST --container CONTAINER --instance i-COORDINATOR --out experiments/fault_tests/coordinator_restart/results/cases.csv
uv run --all-packages python experiments/fault_tests/cancel_inflight/run.py MANIFEST --cancel-after 1 --out experiments/fault_tests/cancel_inflight/results/cases.csv
```

Without `--instance`, injection uses local Docker; with it, AWS CLI sends a
quoted Docker command through SSM and waits for its exit status. These commands
are explicit fault injections, not normal deployment. A cloud worker must
have `restart: unless-stopped` to recover after `docker kill`.

For memory saturation first set a below-model `WORKER_MEMORY_LIMIT` on the
chosen worker and redeploy; the runner records the configured cgroup limit
with inspect while cases run. Collect `docker inspect` OOMKilled/restart and
M1's memory-limit sweep as well: a failed case alone does not prove OOM. Increase
the limit, rerun and compare. The runner does not silently resize live nodes.

Cancellation after a fixed delay only proves in-flight cancellation if a
sample records an assigned/running task first. For deterministic local evidence,
`local_e2e.py --scenario cancel_inflight` waits for running before cancelling a
30-second operation and asserts prompt terminal cancellation.

The existing `local_e2e.py` scenarios use fake operations against real
RabbitMQ/PostgreSQL/MinIO. `scripts/check_real_pipeline.py` runs actual CPU
models in cgroup-limited containers with synthetic fixtures. Neither substitutes
for the required cloud campaign over the completed project dataset.
