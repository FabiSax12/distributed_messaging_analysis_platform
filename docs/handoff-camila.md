# Camila handoff: dataset, frontend and M5

Updated: 2026-11-10. Start here when implementing Camila Hidalgo's remaining work.

## 1. Starting point

David's backend changes are in [PR #6](https://github.com/FabiSax12/distributed_messaging_analysis_platform/pull/6), branch `feat/complete-platform`, implementation commit `a7c7148`. This document is a subsequent documentation commit on the same branch. At handoff creation the PR is open and CI is running; check its current status rather than assuming it merged.

1. Read the repository's [CLAUDE.md](../CLAUDE.md), then the section below for your task. Code and repository documentation use English.
2. Fetch the latest remote state and inspect existing branches/PRs for Camila's changes before implementing anything twice.
3. Once #6 merges, branch from updated `main`. If starting sooner, branch from `origin/feat/complete-platform` and record that dependency in your PR. Avoid checking out an unrelated `main` that lacks these backend contracts.
4. Work in your own branch; preserve other members' changes. Record your actual AI usage and corrections in [bitacora_ia.md](bitacora_ia.md). David's entries are not evidence of Camila's contribution.

Camila owns **dataset generators/ground truth, frontend authentication and findings/role views, and measured M5 results/routing decision**. These can proceed independently of additional backend work. Fabián handles the manual AWS prerequisites in [handoff-completion.md](handoff-completion.md). Dataset-wide accuracy, heavy-case storage measurements and cloud campaigns depend on the finished dataset.

## 2. What already works, and what remains unverified

- Text rules, YOLOv8n image analysis and faster-whisper tiny/int8 audio analysis are implemented and registered. Video goes to the audio worker, which extracts its audio with ffmpeg.
- Coordinator admission, FIFO/aging scheduling, retries, cancellation, reports, custody, JWT/roles, administrator APIs and verified S3 downloads/CAS are implemented. Reuse their contracts rather than rebuilding them.
- Local Docker/PostgreSQL/RabbitMQ/MinIO fault scenarios passed using fake operations. A separate production-model campaign completed 20/20 cases, with a final real-model smoke. This establishes pipeline behavior, **not final dataset accuracy**.
- Local M1 and M2 evidence exists. See [report/README.md](report/README.md), [load-tests/README.md](load-tests/README.md) and the corresponding module reports for methods and limitations.
- Dataset has schemas and a validator, but no completed case collection or generator. M5 has a README and report template, with no benchmark implementation/results. Frontend is an existing React dashboard whose REST client and socket still need user tokens.
- AWS deployment, live Cognito users, SNS/CloudWatch delivery and origin TLS activation have not been verified. Frontend build success does not establish working cloud login.

## 3. Shared setup

Run Python commands from the repository root:

```sh
uv sync --all-packages --group dataset
uv run --group dataset python dataset/generators/validate_manifests.py
```

For the infrastructure smoke, with Docker available:

```sh
docker compose -f infra/docker/compose.dev.yml up -d --wait
uv run --all-packages python services/workers/scripts/local_e2e.py
```

`local_e2e.py` launches its own processes and uses fake operations. To keep a local API available for frontend development instead, use Bash/WSL terminals:

```sh
# Terminal 1, repository root
set -a; source infra/docker/dev.env; set +a
uv run --package coordinator uvicorn coordinator.main:app --port 8000

# Other terminals: source the same env, then one fake worker per pool
uv run --package workers python services/workers/scripts/fake_worker.py --pool text
# Repeat with --pool image and --pool audio in separate terminals.
```

Local defaults: API `http://localhost:8000`; RabbitMQ `5673`, management `15673`; PostgreSQL `5434`; MinIO `9100`. These are development services, not AWS endpoints. Local unauthenticated API mode requires **both** `COGNITO_ISSUER_URL` and `ORIGIN_VERIFY_SECRET` empty. Keep real authentication enabled for the cloud environment.

On David's Windows machine Docker was available through WSL Ubuntu-24.04. Use separate virtual environments for Windows and Linux: set `UV_PROJECT_ENVIRONMENT` to a Linux path when running uv inside WSL against the Windows checkout. Sharing the checkout's Windows `.venv` with Linux broke the initial setup. Build heavy images sequentially. Stop local services with `docker compose -f infra/docker/compose.dev.yml stop` to retain volumes.

## 4. Dataset: implement first for M5 and final measurements

| Read/edit | Purpose |
|---|---|
| [dataset.md](dataset.md) | Required counts, WhatsApp/Telegram formats, indexing, directory layout, provenance table |
| [case_manifest.schema.json](../dataset/schema/case_manifest.schema.json) | Manifest contract |
| [ground_truth.schema.json](../dataset/schema/ground_truth.schema.json) | Label/evidence contract |
| [validate_manifests.py](../dataset/generators/validate_manifests.py) | Existing schema/semantic validator; `--verify-files` checks sizes and hashes |
| [analysis.py](../services/shared/shared/analysis.py) | Authoritative categories, severity, locators and result schema |
| [text.py](../services/workers/src/workers/ops/analysis/text.py) | Existing export parser and text rules |
| [routing.py](../services/shared/shared/routing.py) | Supported extensions and operation/pool mapping |

Create reproducible generators under `dataset/generators/`, `dataset/cases/<case_id>/manifest.json`, and `dataset/ground_truth/<case_id>.json`. Include generation instructions, seeds and media provenance/licenses. Commit manifests, labels and small text exports; keep raw media outside git under the ignored case media paths. Check [dataset/.gitignore](../dataset/.gitignore) before adding files.

Completion criteria:

- At least **40 cases**, **20,000 messages**, **300 images**, **150 voice notes**, **10 heterogeneous cases**, and **5 heavy cases** containing an attachment of at least 200 MB. Exact definitions are in `dataset.md`; the validator's heavy-case test is per source, not just a sum of small files.
- Ground truth includes known positives and negatives, with an explicit labeled sample for precision/recall. An empty findings list is a known negative; an omitted source is unlabeled. Document the evaluation matching rule and compute real per-category results; an evaluator/results table still needs to be supplied.
- Message indexes are zero-based in export order, including system entries. Audio evidence uses segment start/end seconds; image boxes are normalized. Use real file hashes/sizes, valid media and videos with actual audio tracks.
- Documents can appear in manifests but currently have no analysis operation and are reported unsupported. Prefer text+image+audio+video for successful heterogeneous pipeline examples; unsupported documents can be intentional negative/fault examples.
- `uv run --group dataset python dataset/generators/validate_manifests.py --verify-files` passes with media present, and recorded counts meet the minima. A successful run over zero cases is not completion. Independently count messages; the summary's source counts do not establish 20,000 messages.

For upload/integration use [services/load_client/run.py](../services/load_client/run.py). It reads relative source files from one manifest, requests presigned uploads, forwards `chat_format`, `timezone` and `sha256`, creates cases and polls terminal states:

```sh
uv run --all-packages python services/load_client/run.py dataset/cases/case-001/manifest.json --url http://127.0.0.1:8000 --cases 1 --concurrency 1 --out experiments/fault_tests/burst/results/camila-smoke.csv
```

`--cases N` repeats that **one manifest** N times; it does not select N different dataset cases. Uploads receive new source keys and the API generates runtime case IDs, so evaluation must retain a mapping to the manifest/ground-truth source paths rather than equating those IDs. For cloud use the CloudFront URL and supply `ACCESS_TOKEN` through the environment. Bearer tokens belong only on API calls, never presigned S3 requests. The existing [upload-dataset.sh](../scripts/dataset/upload-dataset.sh) is still the Project I script that makes 30 copies under `batch-*`; adapt it for persistent `cases/<case_id>/<path>` publication if needed. It is not the finished manifest uploader.

## 5. Frontend: integrate existing backend contracts

Read [frontend-contract.md](frontend-contract.md), [frontend/README.md](../frontend/README.md), then these files:

| File | Integration point |
|---|---|
| [api/client.js](../frontend/src/api/client.js) | Shared REST request function, API methods, presigned S3 uploads and development URL rewriting |
| [hooks/useLiveState.jsx](../frontend/src/hooks/useLiveState.jsx) | WebSocket connection, polling fallback and reconnect lifecycle |
| [App.jsx](../frontend/src/App.jsx), [main.jsx](../frontend/src/main.jsx) | Auth/session boundary and view composition |
| [config.js](../frontend/src/config.js), [vite.config.js](../frontend/vite.config.js) | Same-origin API/socket URLs and local proxies |
| [views/CaseDetail.jsx](../frontend/src/views/CaseDetail.jsx) | Cancellation, report download/rendering and findings UI |
| [views/NewCase.jsx](../frontend/src/views/NewCase.jsx) | Dataset/manual uploads and create-case payload |
| [shared/api.py](../services/shared/shared/api.py), [shared/models.py](../services/shared/shared/models.py) | API and report payload models |
| [auth.py](../services/coordinator/src/coordinator/auth.py), [api/admin.py](../services/coordinator/src/coordinator/api/admin.py) | Implemented authorization and administrator endpoints |

Start/build:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
# Before delivering:
pnpm build
```

Create `frontend/.env.local` yourself; the README refers to `.env.example`, but that file is absent. For local API work set `VITE_PROXY_TARGET=http://localhost:8000`. The existing proxy supports REST and WebSocket. For a deployed API, set the target to CloudFront and set `VITE_S3_BUCKETS` to the actual evidence bucket(s). Keep same-origin defaults for `VITE_API_BASE`/`VITE_WS_URL` unless intentionally overriding them. The S3 development rewrite currently recognizes AWS bucket hosts, not MinIO URLs; verify local uploads separately rather than assuming the AWS proxy handles MinIO.

Required behavior:

1. Hosted UI authorization-code **PKCE** login for a public SPA client; validate OAuth state, handle the callback before normal hash routing, refresh/expire the session and sign out. Cognito callback URLs are regular URLs, not `#/...` routes, and must exactly match the allowed URLs. Client configuration is public; a SPA must not have a client secret. Keep tokens out of URLs/logs and avoid persistent localStorage storage.
2. Send the **access token**, not ID token, as `Authorization: Bearer ...` from the shared REST request function, including polling. Distinguish expired/invalid session (401), forbidden role (403) and unavailable backend/configuration (5xx).
3. Connect with `new WebSocket(url, ['bearer', accessToken])`. The server selects `bearer`, checks before accept and closes on expiry. Reconnect with the refreshed token; stop sockets/polling on logout. The current hook connects without tokens and retries unconditionally, so adapt its lifecycle to authenticated sessions.
4. Read `GET /api/me` -> `{subject, roles}`. Roles are `analyst`, `supervisor`, `administrator`: analyst reads/uploads/creates; supervisor and administrator also cancel; administrator manages roles. Hide/disable unavailable actions and still handle server denials. Users with no recognized group are denied.
5. Administrator UI uses `GET /api/admin/users?cursor=...` -> `{users, cursor}` and `POST /api/admin/roles` with `{username, role, enabled}`. `enabled` adds/removes the named group; it does **not** disable a Cognito account. User invitations/creation remain manual. Refreshed tokens are needed to reflect changed group claims.
6. Report retrieval is **two steps**: `GET /api/cases/{id}/report` returns `{case_id, url, expires_in}`, then read JSON from that presigned URL without a bearer header. Nonterminal cases return 409. Render `findings`, `categories`, `timeline` and each subtask's `analysis`; in case details the analysis lives under `output_meta.analysis`. Render text escaped, allow empty findings, show evidence source/hash/message/box/segment, and distinguish processing timestamps from evidence timestamps. Unknown original dates remain unknown; rule scores are not calibrated probabilities.
7. Preserve existing upload signing behavior in `putToS3`: do not attach the API bearer header or casually change Content-Type on presigned requests. `/api/healthz` is liveness, not an authentication test. Browsers reach the cloud API through CloudFront, which adds the origin header; never ship its secret in Vite configuration.

Done means build passes **and** login/callback/logout, expired session, refresh/socket reconnect, three roles, denied actions, upload/create/cancel and actual report rendering are exercised. Record which checks used a mock/local environment and which used live Cognito. Backend auth self-checks are in [check_auth.py](../experiments/m4_security/scripts/check_auth.py).

### Public dev configuration supplied by the team

These values were supplied earlier; verify them against current Terraform outputs before use. They do not prove the new backend is deployed.

```text
CloudFront: https://d46arnaly1f90.cloudfront.net
Cognito Hosted UI: https://madp-dev.auth.us-east-1.amazoncognito.com
Client ID: 4o57lmunggp96lripjqr84qb1g
Issuer: https://cognito-idp.us-east-1.amazonaws.com/us-east-1_yIWk3A7R6
User pool: us-east-1_yIWk3A7R6
Evidence bucket: msg-analysis-dev-evidence-114823617609
Region: us-east-1
```

Allowed callbacks/logout URLs and groups are configured by [modules/identity](../infra/terraform/modules/identity/main.tf) and the dev environment. Confirm them with Fabián, together with test users and backend runtime settings. Credentials, origin secret and real tokens stay outside the repository. The complete manual sequence is in [handoff-completion.md](handoff-completion.md).

## 6. M5: measure and justify compute routing

Read [experiments/m5_heterogeneous_compute/README.md](../experiments/m5_heterogeneous_compute/README.md) and fill [module-m5-heterogeneous-compute.md](module-m5-heterogeneous-compute.md). Put reproducible scripts/notebook exports under `experiments/m5_heterogeneous_compute/scripts/`; raw CSVs and plots go under its `results/`. Results are ignored by default: explicitly add only the final evidence files with `git add -f`.

Use the project dataset and choose either CPU vs GPU or controlled CPU thread/quantization configurations. GPU is optional for this route. Existing entry points:

```sh
# Text needs no ML extra. Audio needs ffmpeg/ffprobe installed on the host.
uv run --package workers python -m workers.ops.run_local text_analyze dataset/cases/case-001/chat/whatsapp.txt --out build/m5-text --param chat_format=whatsapp
uv run --package workers --extra audio python -m workers.ops.run_local audio_analyze PATH_TO_AUDIO --out build/m5-audio --timeout 600
uv run --package workers --extra image python -m workers.ops.run_local image_analyze PATH_TO_IMAGE --out build/m5-image
```

Use ignored/local output directories for generated media. `run_local` prints JSON with `wall_time_s`, process peak RSS, child-process peak RSS and `result` (including model/timings/findings). It calls the operation directly; it does not include broker/upload latency or production process supervision. Separate cold-load time from warmed inference and end-to-end latency. Use repeated runs and record failures, input duration/size, host/CPU/GPU, software/model versions, threads and compute type. Label memory measurement scope; GPU memory requires separate collection.

- [audio.py](../services/workers/src/workers/ops/analysis/audio.py) reads environment settings `AUDIO_MODEL` (default `tiny`), `AUDIO_COMPUTE_TYPE` (`int8`), `MODEL_THREADS` (`2`), `MODEL_DEVICE` (`cpu`). Set them before process/model creation; the model is cached within a process, so change configurations in separate processes.
- [image.py](../services/workers/src/workers/ops/analysis/image.py) uses YOLOv8n COCO and `MODEL_DEVICE`. Only knife/scissors detections currently create sensitive-image findings requiring human context review; it is not a general harmful-image classifier. Ground-truth evaluation should make this limitation visible.
- Production images bake default model weights. The audio image defaults to `HF_HUB_OFFLINE=1`; another model needs a baked cache or an explicit runtime-download decision. CPU PyTorch is pinned; `MODEL_DEVICE=cuda` alone does not add CUDA libraries, drivers or GPU container support.
- For limits/profiling reuse [m1_memory/README.md](../experiments/m1_memory/README.md) and [measure_images.py](../experiments/m1_memory/scripts/measure_images.py). Existing M1 data is a reference, not M5 evidence for your chosen inputs/configurations.
- Current routing is static operation -> pool in `shared/routing.py`, used by [routing/planner.py](../services/coordinator/src/coordinator/routing/planner.py). There is no implemented audio-duration GPU threshold or separate GPU queue. If measurements justify retaining CPU pools, document that decision. If they justify new routing, implement and verify it with the matching deployment/device setup; describe a proposal as a proposal until exercised.

Completion: table with per-configuration p50/p95 latency, throughput and peak memory; raw repeated runs and figures; justified routing/crossover or reason to retain CPU; documented cost source/date for compared options; explanation tied to CPU/GPU/NPU parallelism, bandwidth, transfer costs and quantization. Synthetic smoke times and copied M1 numbers do not replace this benchmark.

## 7. Delivery checklist and agent starter

- Dataset: generators, manifests, labels, provenance, verified counts/files, evaluation method/results and media reproduction/download instructions.
- Frontend: authenticated flows, role-aware actions, findings/timeline, passing build and explicit local/live verification record.
- M5: scripts, raw data, plots, completed module report and measured routing decision.
- Update [responsabilidades.md](responsabilidades.md), relevant READMEs and your own AI log as work lands. Preserve David's raw evidence; write new runs to new output paths.
- Keep AWS activation/cloud campaigns explicitly pending until executed. Coordinate upload and final measurement runs with David/Fabián; avoid simultaneous campaigns sharing the dev broker.

Copy this as an agent's starting prompt, selecting one area:

> Read `docs/handoff-camila.md` and the repository instructions. Implement Camila's [dataset / frontend / M5] work using the linked contracts and existing code. First inspect current main and open PRs so you preserve any work already delivered. State the files you will change and the completion checks from that section, implement only that area, run those checks, and record measured evidence and remaining external prerequisites honestly. Backend integration is in PR #6; confirm its merge state before choosing the base branch. Keep credentials and raw media out of git.
