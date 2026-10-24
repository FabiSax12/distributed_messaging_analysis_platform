# Coordinator

Coordinator node of the distributed multimedia processing platform (IC-6600).
It plans sub-tasks from cases, enqueues them in SQS, consumes results,
applies the closing barrier and exposes the REST API consumed by the dashboard.

## Concurrency model

A single `uvicorn --workers 1` process (the exact command will live in the planned Docker Compose setup).
Inside that process, several Python threads:

- `results-consumer-0..N` (`RESULTS_CONSUMER_THREADS`, default 4): consume the
  results queue with `SqsConsumerThread` + the handler in
  `consumers/results.py`. Several threads against the SAME queue is fine: SQS
  distributes messages among concurrent receivers.
- `dlq-consumer`: consumes the DLQ with the handler in `consumers/dlq.py`.
- `sweeper-loop`: runs `barrier/sweeper.run_sweep` every `SWEEP_INTERVAL_S`.
- `snapshot-loop`: runs `monitor/snapshot.run_snapshot_once` every
  `SNAPSHOT_INTERVAL_S` and caches the result in memory.
- `samples-loop`: runs `monitor/samples.take_sample_once` every
  `SAMPLE_INTERVAL_S` and flushes to S3 every `METRICS_FLUSH_S`.

All of them are I/O-bound: they spend almost all their time blocked on boto3
network calls (SQS long-polling, DynamoDB Scans/Queries, S3 Get/PutObject),
which release the GIL while waiting. That is why threads are enough and no
`multiprocessing` or separate task framework is needed. A single process also
keeps the shared in-memory state simple: the snapshot in
`monitor/snapshot.py` and the ring buffer in `monitor/samples.py` are
module-level variables protected by a `threading.Lock`, with no need for IPC
or an external backend (Redis, etc.) just for this.

API endpoints that call boto3 are deliberately declared `def` (not
`async def`): FastAPI runs them in its threadpool instead of the asyncio loop,
so a blocking DynamoDB/S3 call does not stall the rest of the API.

On shutdown (`SIGTERM`/systemd shutdown), the `lifespan` in `main.py` sets a
shared `threading.Event` and `join(timeout=10s)`s each thread. The SQS
consumers may take up to `wait_time` (20s) to notice the event if they are in
the middle of a long-polling `ReceiveMessage` -- they are `daemon=True`, so
the process terminates anyway, but a "clean" shutdown can take a few seconds
longer than systemd's default timeout. Before waiting for the threads, all
open `/api/ws` WebSocket connections are also closed (code `1001`, see below).

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

## Case flow

1. `POST /api/cases` (`api/cases.py`) validates that each `input_key` exists
   in the dataset bucket (`head_object` in parallel), builds sub-tasks with
   `routing/planner.plan_case`, and writes in this order (outbox):
   `SubTasks` (BatchWriteItem) -> `Cases` (PutItem) -> SQS
   (`routing/enqueue.enqueue_case`). If the process dies between the
   BatchWriteItem and the case's PutItem, orphaned sub-tasks are left in
   DynamoDB with no `Cases` item referencing them; since the PutItem never
   ran, they are never enqueued -- they stay inert until a manual cleanup.
   Acceptable: the client retries the `POST` with a new `case_id`.
2. A file with an unsupported extension, or an operation invalid for its
   type, is born as an already `failed` sub-task (code `unsupported_format`),
   without being enqueued. If ALL of a case's sub-tasks are born this way,
   `pending_count == 0` from the start and the case is closed in the same
   request (`barrier/finalize.finalize`).
3. Each worker (out of scope of this code; `services/coordinator/scripts/fake_worker.py` is the
   "protocol" it will implement) consumes its queues according to
   `shared.routing.POLL_ORDER` and publishes a `ResultMessage` for each state
   change to the results queue. Workers **never** touch `Cases`/`SubTasks`
   directly.
4. `consumers/results.py` applies non-terminal progress
   (`assigned`/`running`/`retrying`) with `SubTasksRepo.update_progress`
   (conditioned on `state_order` advancing) and closes terminal sub-tasks
   with `barrier/close.close_subtask`.
5. `close_subtask` decrements `pending_count` and marks the sub-task closed in
   a single transaction (`TransactWriteItems`); if that leaves
   `pending_count` at 0, it calls `barrier/finalize.finalize` in the same
   thread.
6. `finalize` computes the final status (`shared.states.final_case_status`),
   builds the consolidated report (`reports/builder.build_report`), uploads it
   to S3 **before** writing the case's final status, and only then marks the
   case terminal (`CasesRepo.finalize_write`, conditioned on it not being
   terminal already).
7. A message that exhausts its SQS retries lands in the DLQ; the coordinator
   consumes it (`consumers/dlq.py`) and records it as `failed`/
   `retries_exhausted` via `shared.messages.result_from_dlq`, exactly as if
   the worker had reported the failure.
8. `barrier/sweeper.run_sweep` runs at startup (before accepting traffic) and
   then every `SWEEP_INTERVAL_S`: it re-enqueues sub-tasks left with
   `enqueued=false` (a partially failed `SendMessageBatch`) and repairs cases
   that reached `pending_count == 0` but were never finalized (the process
   died between `close_subtask`'s `TransactWriteItems` and the call to
   `finalize`).

## The barrier

`pending_count` in `Cases` is the barrier counter: it starts at
`total - prefailed_count` and each terminal sub-task decrements it by 1. Four
invariants protect it (see the docstrings in `barrier/close.py`):

- **Never subtract twice for the same sub-task**: `build_close_update` in
  `repo/cases.py` uses `ADD closed :id_set` (string-set, idempotent) with
  `ConditionExpression: NOT contains(closed, :id)`. A duplicate
  `ResultMessage` (SQS Standard may redeliver) makes the condition fail; it is
  detected as a `TransactionCanceledException` with `ConditionalCheckFailed`
  and the close is ignored (but it still checks whether to finalize, in case
  the duplicate arrives after the counter is already at 0 without having been
  finalized).
- **SubTasks and Cases are updated atomically**: both writes go in a single
  `TransactWriteItems`. If only `SubTasks` were updated and the process died
  before touching `Cases`, `pending_count` would hang forever; the other way
  around, it would go down without the sub-task reflecting its final state.
- **`finalize` is idempotent**: protected by `finalize_write`, conditioned on
  the case not being terminal already. It can run in parallel from several
  threads/calls (the normal barrier, a late duplicate, the sweeper) without
  duplicating the report or corrupting the state -- the one that loses the
  race simply writes nothing.
- **SQS Standard disorder**: each sub-task state carries a monotonic
  `state_order` (`shared.states.state_order`); progress and close writes are
  conditioned on `state_order < :new`, so a late message (`running` from an
  old attempt) never overwrites a more recent one (`completed`).

## Crash guarantees and known limits

- **Outbox orphans**: sub-tasks written with no associated `Cases` item if
  the process dies between the `BatchWriteItem` and the case's `PutItem` (see
  "Case flow", step 1). They are not cleaned up automatically.
- **Duplicate re-enqueue possible, but idempotent**: if `SendMessageBatch`
  reports success but the confirmation (`mark_enqueued`) never gets written,
  the sweeper may re-enqueue the same sub-task again. The worker processes it
  twice; the barrier only counts the first close that wins the condition (see
  above), so the final result is correct even with duplicated work.
- **Duplicate or late result**: counted only once (see the barrier).
- **Crash between `pending_count == 0` and `finalize`**: repaired by the next
  duplicate that goes through `close_subtask`, or by the sweeper on its next
  pass (at most `SWEEP_INTERVAL_S` of delay).
- **SQS consumer shutdown**: may take up to `wait_time` (20s) to notice the
  `stop_event` (see "Concurrency model").
- **Scans instead of GSIs**: `CasesRepo.scan_non_terminal`/`list_recent` and
  `SubTasksRepo.scan_orphan_enqueue_candidates` are filtered `Scan`s, O(whole
  table) even if they filter afterwards. Acceptable at the scale of a course
  project (hundreds of cases); in production they would be GSIs on
  `status`/`(status, enqueued)`.

Scope decisions deliberately left out of this iteration (not pending bugs,
conscious limits of the current scope):

- **No user authentication/authorization on `/api/*`**: there is only a shared
  origin verification (`X-Origin-Verify`, see `security.py`), no user identity
  or roles. Acceptable for the current scope (course project, no
  multi-tenancy), but if the dashboard ends up exposed to more than one
  user/team this needs revisiting.
- **`/healthz` triggers no infrastructure action**: it correctly reports
  whether an internal thread died (see "Concurrency model"), but nothing in
  the current infrastructure acts on that signal -- there is no ALB/target
  group in front of the coordinator, it is a single EC2 with an Elastic IP.
  It is a limitation of the current architecture, not something to fix in the
  coordinator code.
- **`CaseFileIn.input_key` is only validated for existence, not for shape**:
  `create_case` confirms with `head_object` that the key exists in the
  dataset bucket, but does not sanitize its content. If the real worker ends
  up deriving a local file path from that key, it should sanitize it again
  (same criterion as `_sanitize_filename` in `uploads.py`) instead of trusting
  it raw.

## Dataset convention

- Dataset files live in `DATASET_BUCKET` under arbitrary prefixes (e.g.
  `batch1/`, `uploads/{upload_id}/`).
- `shared.routing.EXTENSIONS` defines which extensions are recognized and
  which `MediaType` they map to (`.mp4`/`.mkv`/... -> video, `.mp3`/`.wav`/...
  -> audio, `.jpg`/`.png`/... -> image). Any other extension is "unsupported".
- An **optional** `manifest.json` per prefix (`GET /api/dataset?prefix=X`
  looks for `{prefix}manifest.json`): a dict `input_key -> arbitrary
  metadata`. If it does not exist, the entries under that prefix simply carry
  no `metadata`. It is used by the load generator (another component) to
  annotate its batches with context (e.g. source camera, capture date).
- `.json` files under a prefix (the manifest and any other) NEVER appear as
  media entries in `GET /api/dataset`.

## Changes to `shared/`

Made by the previous batch; documented here for the record:

1. `shared/init.py` renamed to `shared/__init__.py` (scaffolding typo, empty
   file).
2. `MetricsResponse` added to `shared/api.py` (response of
   `GET /api/metrics`).
3. `Alert` + field `alerts: list[Alert] = []` in `StateResponse`, added to
   `shared/api.py` (operational dashboard alerts, computed by
   `monitor/balancer.py`).
4. `load_samples_chunk_key(ts)` added to `shared/models.py`, leaving
   `load_samples_key` untouched (per-minute chunk key for the periodic flush
   in `monitor/samples.py`, instead of rewriting the whole day's file on
   every flush).

## Deploying to EC2 (planned)

The infrastructure is built by Terraform (`infra/terraform/README.md`); instances
are reachable only through SSM Session Manager (no SSH). The application
deployment is planned as Docker Compose per node (`infra/docker/`) pushed by
scripts in `scripts/deploy/` that read `terraform output`. Neither exists yet.
The environment variables the coordinator reads are listed in
`src/coordinator/config.py`.

## Running locally and testing the full pipeline (smoke test)

Needs a `.env.local` at the repo root with the variables from
`src/coordinator/config.py`, pointing at already-provisioned resources.

1. Start the coordinator:

   ```bash
   uv run --package coordinator uvicorn coordinator.main:app --reload
   ```

2. Start 2-3 `fake_worker.py` instances, one per pool (in separate terminals):

   ```bash
   uv run --package coordinator python services/coordinator/scripts/fake_worker.py --pool video
   uv run --package coordinator python services/coordinator/scripts/fake_worker.py --pool audio
   uv run --package coordinator python services/coordinator/scripts/fake_worker.py --pool metadatos
   ```

   (`--fail-rate 0.1 --crash-rate 0.05` to exercise failures/retries/DLQ.)

3. Run the smoke test (uses real AWS, only the user runs it by hand):

   ```bash
   uv run --package coordinator python services/coordinator/scripts/smoke.py
   ```

## Tests

```bash
uv run --package coordinator python -m pytest services/coordinator/tests -v
uv run ruff check services/coordinator
uv run ruff format --check services/coordinator
```

Note: `uv run --package coordinator pytest ...` (invoking the `pytest` script
directly, without `python -m`) may fail with
`ModuleNotFoundError: No module named 'shared'` in this repo. It is a problem
with the **editable install** of `shared/` (its `pyproject.toml` remaps the
flat code layout to a `shared/` package via
`[tool.hatch.build.targets.wheel.sources]`, but uv's editable install points
`sys.path` straight at the source directory without applying that remapping
-- it works for non-editable builds/installs, not for editable ones). `python
-m pytest` (or any invocation with the repo root as cwd, which is the normal
case) does not suffer from it because `python -m` adds the cwd to
`sys.path`, where `shared/` is a valid subdirectory with `__init__.py`.
`shared/pyproject.toml` was not touched so as not to exceed the scope of that
batch; to fix it at the root, check how uv generates the editable `.pth` for
packages with remapped `sources` in `hatchling`.

All tests use `moto` (in-memory SQS + DynamoDB + S3); none touches real AWS.
`services/coordinator/tests/conftest.py` explicitly sets all `Settings` variables
(including `TABLE_*`) so as not to inadvertently inherit the values of a real
`.env.local` if one exists in the repo.
