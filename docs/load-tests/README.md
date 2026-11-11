# Local and cloud load/fault evidence

## Local runs executed

- `local_e2e.py`: basic, unsupported, transient retry, worker kill/requeue,
  coordinator restart without duplicate rows, PostgreSQL outage without retry
  exhaustion. All passed against real RabbitMQ/PostgreSQL/MinIO with fake ops.
- `local_e2e.py --scenario cancel_inflight`: waits for running, cancels a
  30-second isolated operation and asserts terminal cancellation before 15 s.
- `scripts/check_real_pipeline.py --cases 20`: all 20 cases completed with
  actual CPU text/YOLO/Whisper operations. The tool rejects fake-model results.
  Source hashes, S3 conditional CAS, report findings and timelines were checked.
- Final real-model smoke after the report/security integration completed.
- M1 cgroup OOM and higher-limit success are recorded in `limits.csv`;
  worker-crash E2E demonstrates the corresponding bounded retry/requeue path.
  A live AWS OOM failover campaign is still required.

Raw per-case latency/outcomes, worker cgroup memory traces and container
user/limit/OOM status: `experiments/fault_tests/burst/results/`. The final smoke
has its own subdirectory so it does not overwrite the 20-case evidence.
Per-model Linux raw evidence and figures are in M1; per-policy campaigns in M2.
Local E2E summary CSVs under `experiments/fault_tests/local-results/` record the
executed scenario verdicts; the initial six-scenario summary is explicitly transcribed from its successful console output because that run predated CSV logging. Raw service logs are intentionally not committed.

## Cloud campaign still manual

Use the five scenario entry points and commands in
`experiments/fault_tests/README.md` after configuring AWS and uploading the full
dataset. Record model/image SHA, machine type, limits, policy, dataset revision,
case mix, injection time and outcomes. Capture CloudWatch/SNS and non-root/
capability evidence. Do not label a timed cancellation as in-flight unless the
sampled state confirms work was running. Do not label a failed case as OOM
without cgroup/Docker evidence.

These local results are useful engineering checks, not a substitute for the
assignment's cloud measurements, final dataset accuracy or M5 benchmark.
