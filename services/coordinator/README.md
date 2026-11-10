# Coordinator

Coordinator node of the distributed messaging-analysis platform (IC-6600).
It plans sub-tasks from cases, keeps all case and sub-task state in
PostgreSQL, publishes work to RabbitMQ, consumes results, decides retries,
applies the closing barrier and exposes the REST API used by the dashboard.
S3 only stores bytes: dataset inputs, analysis outputs, reports and metrics.

## Concurrency model

One `uvicorn --workers 1` process with background threads. pika is not
thread-safe, so every thread that talks to RabbitMQ owns its own connection;
PostgreSQL is shared through one thread-safe `psycopg_pool.ConnectionPool`.

- `results-consumer-0..N` (`RESULTS_CONSUMER_THREADS`): consume `results`
  (`consumers/results.py`), ack only after the effect is committed.
- `dlq-consumer`: consumes `dead-letter` (`consumers/dlq.py`).
- `outbox-relay`: the only publisher of work messages (`routing/outbox.py`).
- `sweeper-loop`: `barrier/sweeper.run_sweep` every `SWEEP_INTERVAL_S`.
- `snapshot-loop` / `samples-loop`: cached `StateResponse` for `/api/state`
  and `/api/ws`, load samples flushed to S3.

Endpoints doing blocking I/O are plain `def`, so FastAPI runs them in its
threadpool. `GET /healthz` reports whether every thread is alive.

## Live state (WebSocket)

`WS /api/ws` pushes the same `StateResponse` that `snapshot-loop` builds every
`SNAPSHOT_INTERVAL_S`, without the dashboard having to poll. `GET
/api/state` still exists and the dashboard should use it as a fallback while
the socket is down (see "What the dashboard must do" below).

- **URL**: `wss://<cloudfront-domain>/api/ws` in production (the CloudFront
  `/api/*` behavior already forwards the WebSocket upgrade headers and adds
  `X-Origin-Verify`, same as for REST routes); `ws://localhost:8000/api/ws`
  when running the coordinator locally.

- **Message format**, two kinds of JSON object distinguished by `type`:

  ```json
  {"type": "state", "data": <serialized StateResponse, same as GET /api/state>}
  {"type": "ping"}
  ```

  The `{"type": "state", "data": ...}` envelope is built only once in
  `Broadcaster.publish` (serializing the `StateResponse` once); each connected
  client receives that same text as is, without the server re-parsing/
  re-serializing it per connection.

- **Thread -> event loop bridge** (`monitor/broadcaster.py`): `snapshot-loop`
  runs in a Python thread, not a coroutine, so it cannot touch a `WebSocket`
  or an `asyncio.Queue` directly (they are not thread-safe). The `Broadcaster`
  keeps the real event loop (`asyncio.get_running_loop()`, captured in the
  `lifespan` before the threads start) and, when `snapshot-loop` calls
  `publish()`, schedules the actual delivery with
  `loop.call_soon_threadsafe(...)`. Only code running inside the loop touches
  the queues or the sockets.

  Why the per-client queue has size 1: each connection has its own
  `asyncio.Queue(maxsize=1)`. If a new state arrives before that client has
  read the previous one, the old one is dropped and the new one is kept -- a
  slow client (background tab, high-latency connection) never accumulates a
  backlog of stale states; it always ends up seeing the latest available
  state, never a stale one, and never blocks sending to the other connected
  clients.

  `publish()` also drops a publication if the content did not change since
  the last send, ignoring `generated_at` (which changes on every snapshot
  cycle even if nothing else does) -- this avoids flooding clients with
  non-changes.

- **Why authentication happens in the handshake and not in a middleware**:
  `OriginVerifyMiddleware` (`security.py`) is a `BaseHTTPMiddleware`, and
  Starlette does not run it on WebSocket connections (it lets through,
  untouched, any scope that is not `http`). So the `/api/ws` handler
  re-validates `X-Origin-Verify` by hand, BEFORE accepting the connection
  (`websocket.accept()`), reusing `security.verify_origin` -- the same
  `hmac.compare_digest` comparison (avoids timing attacks) and the same
  "empty `ORIGIN_VERIFY_SECRET` = local mode, let through" criterion the HTTP
  middleware already uses. If the header is missing or does not match, the
  connection is closed with code `1008` without ever having been accepted.
  There is also a cap on simultaneous connections (`WS_MAX_CLIENTS`, default
  20): when exceeded, the connection is closed with `1013`.

- **Application ping**: every `WS_PING_INTERVAL_S` seconds (default 25) the
  server sends `{"type": "ping"}`. It exists because CloudFront drops idle
  connections: the ping keeps the socket alive and also helps detect dead
  clients.

- **What the dashboard must do**:
  - Reconnect with exponential backoff if the socket drops or never manages
    to connect (1 s, 2 s, 4 s, ... up to a 30 s cap).
  - While the socket is down, go back to polling `GET /api/state` as a
    fallback.
  - Ignore `"ping"` messages (they carry no data, they only keep the
    connection alive).


## Queue topology and priorities

Declared idempotently by every process on connect (`shared.broker.declare_topology`):

| Name | Type | Notes |
|---|---|---|
| `work` | direct exchange | routing key = pool (`text`, `image`, `audio`) or `results` |
| `work.text`, `work.image`, `work.audio` | durable queues | `x-max-priority = 4`, dead-letter to `dead-letter` |
| `results` | durable queue | worker -> coordinator `ResultMessage`s |
| `dead-letter` | fanout exchange + durable queue | messages a consumer rejected |

`CasePriority` low/medium/high/critical maps to AMQP priority 1..4
(`shared.routing.message_priority`, the M2 seam). Messages are persistent and
published with publisher confirms; consumers ack manually.

## Case flow

1. `POST /api/cases` checks each `input_key` exists in S3, plans sub-tasks
   (`routing/planner.py`: `.txt`/`.json` -> text, images -> image, audio and
   video -> audio_analyze on the audio pool) and inserts the case and all its
   sub-tasks in **one transaction** (outbox), then wakes the relay.
2. Unknown extensions are born `failed` / `unsupported_format` and never
   published; the case starts with `pending_count = total - prefailed`.
3. The outbox relay publishes rows with `enqueued = false` (new, or
   `retrying` with `retry_at` due) and marks them `enqueued` only after the
   broker confirms.
4. Workers publish `assigned`/`running`/terminal results. Progress is written
   only if `state_order` advances.
5. Terminal results go through `Barrier.apply_terminal` (`barrier/close.py`).

## Retries, barrier and recovery

- **Retries are decided only here.** A `failed` result with a retryable
  code (`internal`, `external_api_error`, `worker_lost`) and
  `attempt < RETRY_MAX_ATTEMPTS` sets the sub-task to `retrying`,
  `attempt + 1`, `retry_at = now + min(RETRY_MAX_DELAY_S, RETRY_BASE_DELAY_S * 2^(attempt-1))`,
  `enqueued = false`; the relay republishes it when due. Past the maximum it
  closes as `failed` / `retries_exhausted`.
- **Barrier.** `SubTasksRepo.close` is one statement (data-modifying CTE):
  close the sub-task (`NOT closed AND state_order < new`) and
  `pending_count - 1 ... RETURNING` on its case, in one transaction. A
  duplicate result updates nothing; at zero the case is finalized (report
  uploaded to S3 first, then the terminal status, guarded so it is written
  once).
- **Coordinator restart (B.6).** Results stay in the durable queue. On
  startup the sweeper runs before traffic is accepted: cases stuck at
  `pending_count = 0` are finalized, and the relay republishes anything
  committed but never confirmed. Sub-tasks are never re-created.
- **Worker crash (B.6).** A killed worker's unacked message is redelivered;
  the next worker reports it as `worker_lost` without running it, so crashes
  count against the retry maximum. Independently, the sweeper marks workers
  with `last_seen` older than `HEARTBEAT_TIMEOUT_S` as `dead` and synthesizes
  `worker_lost` for their in-flight sub-tasks (covers a hung worker whose
  connection stays open).
- **Duplicate publish.** If the coordinator dies after the broker confirmed
  but before `enqueued = true`, the attempt is published twice; the worker's
  pre-start check drops the stale copy and the barrier counts once anyway.

## Configuration

`src/coordinator/config.py` (pydantic-settings, environment variables):

| Variable | Local (`infra/docker/dev.env`) | AWS |
|---|---|---|
| `AMQP_URL` | `amqp://dev:dev@127.0.0.1:5673/` | `amqps://USER:PASS@HOST:5671` from `terraform output mq_endpoint` + the `secret_arns.mq` secret |
| `DATABASE_URL` | `postgresql://dev:dev@127.0.0.1:5434/dmap` | `postgresql://USER:PASS@<rds_endpoint>/<rds_db_name>?sslmode=require` + the `secret_arns.rds` secret |
| `S3_BUCKET` | `dmap-dev` | `terraform output evidence_bucket_name` |
| `S3_ENDPOINT_URL` | `http://127.0.0.1:9100` (MinIO) | unset |
| `ORIGIN_VERIFY_SECRET` | empty (unprotected, local only) | the `secret_arns.origin_verify` secret |

Read a secret with
`aws secretsmanager get-secret-value --secret-id <arn> --query SecretString --output text`
(JSON with `username`/`password`). URL-encode passwords in the URLs: the
generated ones contain characters such as `#` and `%`. Tuning knobs:
`RETRY_MAX_ATTEMPTS` (3), `RETRY_BASE_DELAY_S` (2), `RETRY_MAX_DELAY_S` (60),
`HEARTBEAT_TIMEOUT_S` (60), `SWEEP_INTERVAL_S` (5), `OUTBOX_POLL_S` (1). The
schema (`src/coordinator/db/schema.sql`) is applied at every startup. Deploy
scripts (SSM) are not written yet.

## Running locally

```bash
docker compose -f infra/docker/compose.dev.yml up -d --wait
set -a; source infra/docker/dev.env; set +a
uv run --package coordinator uvicorn coordinator.main:app --port 8000
# one terminal per pool (real runner, fake op, no ML models):
uv run --package workers python services/workers/scripts/fake_worker.py --pool text
uv run --package workers python services/workers/scripts/fake_worker.py --pool image
uv run --package workers python services/workers/scripts/fake_worker.py --pool audio --fail-rate 0.3
uv run --package coordinator python services/coordinator/scripts/smoke.py
uv run --package coordinator python services/coordinator/scripts/smoke.py \
    --files chat.txt notes.xyz --expect partially_completed
```

`services/workers/scripts/local_e2e.py` automates all of this plus the
worker-crash and coordinator-restart scenarios. RabbitMQ management UI:
<http://127.0.0.1:15673> (dev/dev). Lint with `uv run ruff check services/coordinator`.

## Process supervision

The coordinator exits if PostgreSQL is unreachable for more than 30 s at
startup (pool open timeout), and so does a worker. Run both under a restart
policy: `restart: unless-stopped` in Docker, or systemd `Restart=always`
with `TimeoutStopSec` above the workers' `op_timeout_s` (a stopping worker
finishes its current sub-task first). Once running, database and broker
outages are retried in place.

## Known limitations

- A late `worker_lost` for an attempt whose `completed` result was published
  but not acked can be applied after a retry was already scheduled; on the
  final attempt this can record `retries_exhausted` for work that actually
  completed.
- Real in-flight cancellation is not implemented: `cancel_requested` is only
  checked by workers before a sub-task starts.

## Seams for the OS modules (not implemented)

- **M1 admission control**: `api/cases.py::create_case`, before the outbox insert.
- **M2 scheduling (aging)**: `routing/outbox.py` decides what is published
  and with which priority (`shared.routing.message_priority`).
- **M3 storage**: S3 access is `aws.s3_client()`; chunking/CAS belong in
  `services/storage`.
- **M4 security**: only `X-Origin-Verify` today (`security.py`); no user
  auth, RBAC, TLS between nodes beyond AMQPS/RDS TLS, or custody log.
- **Cancellation**: `DELETE /api/cases/{id}` sets `cancel_requested`;
  workers skip sub-tasks that have not started. Running ones finish.

## Dataset convention

Inputs live in `S3_BUCKET` under any prefix except `results/` and
`metrics/` (written by the coordinator). An optional `{prefix}manifest.json`
maps `input_key -> metadata` for `GET /api/dataset`.
