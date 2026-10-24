# Workers

A single codebase for the three pools. `POOL` (`video`, `audio` or `metadatos`)
selects which queues it consumes and in what order (`shared.routing.POLL_ORDER`).
The worker processes the sub-task, uploads the output to S3 and reports every
state change to the coordinator through the `resultados` queue. It never writes
`Cases` or `SubTasks`; its only DynamoDB write is its heartbeat in `Workers`.

> **Status: inherited code, being adapted.** This package was copied from the
> team's Project I (SQS/DynamoDB/ffmpeg pipeline). The target for this project is
> RabbitMQ + PostgreSQL with text, image and audio workers (see the root
> `README.md` and `docs/reuse.md`). Queue, pool and operation names below are the
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
Operation tests will be added together with the real operations.

## Out of scope

- Cancelling a sub-task that is already running: cancellation is checked before
  starting.
- Extending the visibility timeout: the per-operation cap makes it unnecessary.
- Autoscaling: scale by hand through the `worker_nodes` map in Terraform.
