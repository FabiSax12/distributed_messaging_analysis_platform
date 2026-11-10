# Workers

A single codebase for the three pools. `POOL` (`video`, `audio` or `metadatos`)
selects which queues it consumes and in what order (`shared.routing.POLL_ORDER`).
The worker processes the sub-task, uploads the output to S3 and reports every
state change to the coordinator through the `resultados` queue. It never writes
`Cases` or `SubTasks`; its only DynamoDB write is its heartbeat in `Workers`.

> **Status: inherited code, being adapted.** This package was copied from the
> team's Project I (SQS/DynamoDB/ffmpeg pipeline). The target for this project is
> RabbitMQ + PostgreSQL with text, image and audio workers (see the root
> `README.md` and `docs/reutilizacion.md`). Queue, pool and operation names below are the
> inherited wire contract and will change in a later coordinated step.

## Concurrency model

One process per machine with `WORKER_CONCURRENCY` consumer threads (2 by
default in video and audio, 4 in metadatos) plus one heartbeat thread. The heavy
work runs in ffmpeg subprocesses and the rest is network waiting, so the threads
do not compete for the GIL.

## Sub-task protocol

1. It walks its queues in `POLL_ORDER`: first its pool's `alta` (high) queue,
   then the `normal` one, and finally the `normal` queues of other pools it can
   help. This redistributes load without touching the coordinator.
2. It reads `Cases.cancel_requested` (consistent read). If the case was
   cancelled, it reports `cancelled` and does not process.
3. If SQS already delivered the message before (`ApproximateReceiveCount > 1`),
   it reports `retrying`.
4. It reports `assigned`, downloads the file from the dataset and reports
   `running` with ffmpeg progress every 10 points.
5. It uploads the output to `results/{case_id}/{subtask_id}/` and reports
   `completed`, or `failed` with its `ErrorCode`.
6. Only then does it delete the work message.

If something unexpected fails (S3 down, a bug), the worker reports no terminal
status and does not delete: SQS retries and, on the third attempt, the DLQ hands
it to the coordinator as `retries_exhausted`.

Each operation is capped at its queue's visibility timeout minus 30 s (inherited
layout: video 870 s, audio 270 s, metadatos 90 s). Past that time the worker kills ffmpeg and
reports `timeout` before SQS redelivers the message.

## Operations

The operation registry (`src/workers/ops/__init__.py`) is currently empty.
The text, image and audio operations are planned and will be registered there;
each one follows the `Operation` interface in `src/workers/ops/base.py`, and
`src/workers/ops/ffmpeg.py` holds the ffmpeg/ffprobe subprocess helpers.
Project I's video and metadata operations were intentionally not copied.

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
  Do not branch on `task.media_type`/`task.operation`: they still use the legacy
  routing enums and are filled loosely by the local runner.
- Respect `timeout_s` (pass it to subprocesses / model calls) and call
  `on_progress(pct)` with 0-100 now and then.
- `audio_analyze` transcribes and then runs the same text analysis on the
  transcript: share that code instead of duplicating it.

**Errors.** Raise `OpError(ErrorCode.X, "message")` for expected failures:
`CORRUPT_INPUT` (unreadable file, not a chat export), `UNSUPPORTED_FORMAT`
(bad `params`), `TIMEOUT`, `PROCESSING_ERROR` (model/ffmpeg failed),
`EXTERNAL_API_ERROR`. The runner reports them as `failed` without retrying.
Any other exception is treated as transient and retried, so do not catch
everything.

**Register** the op in `ANALYSIS_OPERATIONS` in
`src/workers/ops/analysis/__init__.py` (one module per op in that package).
It is separate from the legacy `OPERATIONS`; the migration wires it into the
runner. Import heavy libraries inside the function, not at module level.

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

| Situation | `ErrorCode` |
|---|---|
| ffprobe cannot read the file, or it does not exist in the dataset | `corrupt_input` |
| ffmpeg exits with an error | `processing_error` |
| `params` requests a format that does not exist | `unsupported_format` |
| The queue's time limit is exceeded | `timeout` |
| An external API does not respond | `external_api_error` |

## Monitoring

Every 5 s the worker writes its `WorkerItem`: the machine's CPU and memory
(psutil), `concurrency` and the `subtask_id`s in flight. On EC2 the `worker_id`
is `{pool}-{instance-id}`, with no pid, so a restart overwrites the
same item instead of leaving a worker "dead" forever on the dashboard.

## Deployment (planned)

The infrastructure is built by Terraform (`infra/terraform/README.md`). Worker
instances come from the `worker_nodes` map in the dev environment (`text`,
`image`, `audio`) and are managed only through SSM Session Manager (no SSH).
The application deployment is planned as Docker Compose per node
(`infra/docker/`) pushed by scripts in `scripts/deploy/` that read
`terraform output`. Neither exists yet.

## Running locally

`scripts/local_e2e.py` starts moto in-process with the inherited queue layout, the
real coordinator and one worker per pool. It cannot complete a case until
operations are registered (see Operations) and it still targets the SQS/DynamoDB
layout, so it is kept as a reference for the future RabbitMQ/PostgreSQL version.

To run a worker by hand against provisioned resources, export the variables read
by `src/workers/config.py` (or put them in a repo root `.env.local`), then:

```bash
POOL=video uv run --package workers python -m workers.main
```

## Tests

```bash
uv run --package workers python -m pytest services/workers/tests -v
uv run ruff check services/workers
uv run ruff format --check services/workers
```

All of them use moto; none touches AWS or the internet. The runner tests use
small fake operations registered with `monkeypatch`, so no ffmpeg is needed.
These are legacy Project I tests; analysis ops are checked with `run_local`, not
unit tests (see `CLAUDE.md`).

## Out of scope

- Cancelling a sub-task that is already running: cancellation is checked before
  starting.
- Extending the visibility timeout: the per-operation cap makes it unnecessary.
- Autoscaling: scale by hand through the `worker_nodes` map in Terraform.
