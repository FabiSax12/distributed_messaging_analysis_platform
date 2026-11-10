# Workers

A single codebase for the three pools. `POOL` (`text`, `image` or `audio`)
selects the RabbitMQ queue it consumes (`work.<pool>`). The worker downloads
the input from S3, runs the analysis op, uploads its outputs to
`results/{case_id}/{subtask_id}/` and reports every state change on the
`results` queue. It never writes `cases` or `subtasks`; its only PostgreSQL
write is its heartbeat row in `workers` (plus a read-only pre-start check).

## Concurrency model

One process per machine with `WORKER_CONCURRENCY` consumer threads (default 1)
plus a heartbeat thread. Each consumer owns its AMQP connection (pika is not
thread-safe) with `prefetch=1` and manual acks. The op runs on a helper
thread while the consumer thread keeps servicing AMQP heartbeats and
publishes the op's progress, so long model runs do not drop the connection.

## Sub-task protocol (`src/workers/runner/process.py`)

1. Unreadable message -> rejected without requeue (`dead-letter`).
2. Pre-start check in PostgreSQL: unknown, closed, or older `attempt` than
   the current one -> stale duplicate, ack and drop; case
   `cancel_requested` -> report `cancelled` without processing.
3. `redelivered` (the previous holder died) -> report `failed` /
   `worker_lost` without running it; the coordinator schedules the retry.
4. `assigned` -> download -> `running` (progress every 10 points) -> op ->
   upload -> `completed`; `OpError` -> `failed` with its code; any other
   exception -> `failed` / `internal`.
5. Ack only after the terminal result is confirmed by the broker.

The worker never retries by itself: retries, backoff and the maximum are
decided by the coordinator (`RETRY_*` settings there).

## Operations

`ANALYSIS_OPERATIONS` (`src/workers/ops/analysis/__init__.py`) maps each
`AnalysisOperation` to a function following `Operation` in
`src/workers/ops/base.py`. It is empty until the text, image and audio ops are
written, so a real worker fails every sub-task with `processing_error`; use
`scripts/fake_worker.py` to drive cases meanwhile. `src/workers/ops/ffmpeg.py`
keeps the ffprobe/ffmpeg subprocess helpers with timeout and progress (the
audio pool needs ffmpeg to extract the audio track of videos).

## Writing an analysis operation

The analysis ops (`text_analyze`, `image_analyze`, `audio_analyze`) can be
written and measured today, without RabbitMQ, PostgreSQL or S3. Their result
contract is `shared/analysis.py`.

**Protocol.** An op is a plain function that follows `Operation` in
`src/workers/ops/base.py`:

```python
def text_analyze(task: SubTaskMessage, src: Path, out_dir: Path, *,
                 timeout_s: float, on_progress: Callable[[int], None]) -> OpOutput:
    ...
    result = AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE, findings=[...])
    return OpOutput(files=[...], meta=result.to_meta())
```

- `src` is the already-downloaded input; never modify it. Write any output
  files (e.g. an annotated image, a transcript `.json`) into `out_dir` and list
  them in `OpOutput.files`.
- Use `task.input_key` as `Evidence.source_key` and `task.params` for options.
  `task.media_type` tells a video from an audio file under `audio_analyze`.
- Respect `timeout_s` (pass it to subprocesses / model calls) and call
  `on_progress(pct)` with 0-100 now and then.
- `audio_analyze` transcribes and then runs the same text analysis on the
  transcript: share that code instead of duplicating it.

**Errors.** Raise `OpError(ErrorCode.X, "message")` for expected failures:
`CORRUPT_INPUT` (unreadable file, not a chat export), `UNSUPPORTED_FORMAT`
(bad `params`), `TIMEOUT`, `PROCESSING_ERROR` (model/ffmpeg failed),
`EXTERNAL_API_ERROR`. The runner reports them as `failed`; any other
exception is reported as `internal`. The coordinator retries `internal` and
`external_api_error` with backoff, the rest are final, so do not catch
everything.

**Register** the op in `ANALYSIS_OPERATIONS` in
`src/workers/ops/analysis/__init__.py` (one module per op in that package);
the runner dispatches every sub-task through it, and an unregistered
operation fails with `processing_error`. Import heavy libraries inside the
function, not at module level.

**Run it locally** (prints the `AnalysisResult` as JSON plus wall time and
peak RSS; progress and errors go to stderr):

```bash
uv run --package workers python -m workers.ops.run_local text_analyze path/to/chat.txt \
    --out /tmp/out --timeout 120 --param lang=es
```

An unknown or unregistered operation exits with code 2 and lists the
registered ones; an `OpError` or a `meta` that breaks the contract exits with 1.
`peak_rss_bytes` is the whole process peak (`getrusage`, includes the
interpreter and imports); `peak_rss_children_bytes` covers subprocesses such as
ffmpeg. You can also fill `AnalysisResult.timings_s` and `peak_rss_bytes` from
inside the op (e.g. model load vs inference).

**Under a memory limit (M1).** Run the same command in a container with a cgroup
limit to find the smallest limit each model survives and to provoke an OOM:

```bash
# From the repo root. UV_PROJECT_ENVIRONMENT keeps the container's venv out of
# the host's .venv. `--extra text` assumes that extra exists (see below).
docker run --rm --memory=1g --memory-swap=1g \
    -v "$PWD":/app -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    ghcr.io/astral-sh/uv:python3.12-bookworm \
    uv run --package workers --extra text python -m workers.ops.run_local text_analyze dataset/text/sample.txt
# exit code 137 = killed by the OOM killer; `docker stats` shows usage over time
```

Measure PSS (shared pages split across processes) with `smem` or
`/proc/<pid>/smaps_rollup` when comparing one model per process against a
model shared through fork/copy-on-write or mmap. Put the scripts and CSVs in
`experiments/m1_memory/`.

**Dependencies per pool.** Heavy ML libraries go in optional dependency groups
in `services/workers/pyproject.toml`, one per pool, so each node installs only
what it runs:

```toml
[project.optional-dependencies]
text = ["..."]    # e.g. a tokenizer / small classifier
image = ["..."]   # e.g. ultralytics or onnxruntime
audio = ["..."]   # e.g. faster-whisper (plus whatever `text` needs)
```

Install with `uv sync --package workers --extra audio` (or `--extra text`,
`--extra image`). After changing dependencies run `uv lock` at the repo root
and commit `uv.lock`. Keep the base `dependencies` free of ML libraries.

## Errors

| Situation | `ErrorCode` | Retried by the coordinator |
|---|---|---|
| Input missing in S3, unreadable file | `corrupt_input` | no |
| Model or ffmpeg failed | `processing_error` | no |
| `params` requests something unsupported | `unsupported_format` | no |
| Op exceeded `timeout_s` | `timeout` | no |
| External API failed | `external_api_error` | yes |
| Unexpected exception | `internal` | yes |
| Worker died mid-task | `worker_lost` | yes |

## Configuration

`src/workers/config.py`: `POOL`, `AMQP_URL`, `DATABASE_URL`, `S3_BUCKET`,
optional `S3_ENDPOINT_URL` (MinIO), `WORKER_CONCURRENCY`, `OP_TIMEOUT_S`
(default 120 s text/image, 600 s audio) and `HEARTBEAT_INTERVAL_S` (5 s).
Locally use `infra/docker/dev.env`; on AWS fill them from `terraform output`
and Secrets Manager exactly as described in `services/coordinator/README.md`
("Configuration"). On EC2 the `worker_id` is `{pool}-{instance-id}`, so a
restart reuses the same `workers` row; elsewhere it is `{pool}-{host}-{pid}`.

## Running locally

```bash
docker compose -f infra/docker/compose.dev.yml up -d --wait
set -a; source infra/docker/dev.env; set +a
POOL=text uv run --package workers python -m workers.main     # real ops
uv run --package workers python services/workers/scripts/fake_worker.py --pool text \
    --fail-rate 0.3 --crash-rate 0.1                          # fake op
# everything (coordinator + fake workers + B.6 scenarios), from the repo root:
uv run --all-packages python services/workers/scripts/local_e2e.py
uv run ruff check services/workers
```

## Process supervision

A worker exits if PostgreSQL is unreachable for more than 30 s at startup.
Run it under a restart policy (Docker `restart: unless-stopped`, or systemd
`Restart=always` with `TimeoutStopSec` above `op_timeout_s`, since SIGTERM
lets the current sub-task finish). Database and broker outages after
startup are retried in place.

## Out of scope

- Cancelling a sub-task that is already running (checked before starting).
- Autoscaling: scale by hand through the `worker_nodes` map in Terraform.
