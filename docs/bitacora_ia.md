# AI usage log

> At least two documented cases of AI errors or inaccuracies

| Date · member | Tool | Component | Purpose | Summarized prompt | Result obtained | Validated / corrected / discarded | How it was validated |
|---|---|---|---|---|---|---|---|
| 2026-11-10 - David Molina | Codex | Analysis workers | Implement local analysis | Follow AnalysisResult and existing runner contracts | Bilingual rules, YOLOv8n and Whisper tiny with source evidence | Validated | Synthetic WhatsApp/Telegram, invalid JSON, deadlines, actual YOLO blank image and Whisper video extraction; check_analysis.py; Windows synthetic speech produced threat/offensive findings with segment times |
| 2026-11-10 - David Molina | Codex | Audio dependency error | Choose compatible Whisper dependencies | Add faster-whisper optional extra | Unbounded PyAV selected 19, which rejected metadata_errors used by Whisper 1.2.1 | Corrected: av<19 | First real video check failed with TypeError; rerun with PyAV 18.1.0 passed |
| 2026-11-10 - David Molina | Codex | M1 reservation ordering error | Persist admission reservation without schema migration | Store reservation in first subtask | Initial code assumed list order matched SQL ORDER BY subtask_id | Corrected: use minimum subtask_id explicitly | Reopened planner/repository ordering; admission executable checks passed; PostgreSQL integration still pending |
| 2026-11-10 - David Molina | Codex | M1 | Admission and memory experiments | Limit accepted work and measure real models | Atomic advisory-lock admission; native RSS profiles; Linux cgroup/fork scripts | Partially validated | check_admission.py with SQL fakes; native profile CSVs. Docker/PSS/OOM/distributed recovery not run |
| 2026-11-10 - David Molina | Codex | M3 | Implement existing storage Protocols | Bounded streaming, dedup and queue | stdlib components plus synthetic benchmarks | Validated locally; real dataset pending | Corruption, duplicate retries, concurrent puts, queue timeout/close checks; 200 MiB transfer at 3 chunk sizes, burst traces |

Final CLI check: model setup/download stdout is redirected to stderr so stdout remains parseable JSON. PowerShell ConvertFrom-Json accepted the text result. All touched Python paths passed Ruff; storage/admission/real-model checks passed again. PostgreSQL/Docker E2E and AWS deployment were not run.
2026-11-10, David Molina: integrated the new main CI/CD changes into the analysis branch before opening stacked PRs. Image/audio production Dockerfiles now install their analysis extras; image runtime includes OpenCV libraries. Container builds remain delegated to GitHub CI because Docker is unavailable locally.

## Completion handoff implementation (David Molina, 2026-11-10)

Codex implemented the remaining backend/worker/infra code in one PR; no new
subagents were used. Camila keeps frontend, full dataset and M5. Cloud changes
were authored but no Terraform apply, instance lifecycle or AWS rollout was run.

| Component | AI error / correction | Validation |
|---|---|---|
| CPU image | First weight-baking build imported OpenCV without its system libraries in the build stage. Added required libs to build/runtime stages. | All four production images built sequentially; real CPU inference completed. |
| Load client | Initial streamed presigned PUT omitted Content-Length and MinIO returned 411. Added the exact file size header. | Real manifest uploads and 20 concurrent real-model cases completed. |
| Experiment isolation | An initial scheduling campaign overlapped the real-model campaign on the shared dev broker. Stopped it and reran sequentially; added overlap guard and reject-fake-model assertions. | Separate 40-case FIFO/aging runs and clean 20-case real-model run completed. |
| Linux setup | Initial WSL sync selected the Windows .venv. Restored it and moved Linux's environment to /root/madp-work/venv. | Windows and Linux checks pass independently. |

Executed evidence: signed JWT/role checks; tamper self-check; actual PostgreSQL
admission and custody immutability; all local E2E scenarios including in-flight
cancellation; cgroup OOM/PSS/fork measurements; production model burst; FIFO and
aging campaigns; Terraform validation. Remaining manual/cloud evidence is
explicit in docs/handoff-completion.md; no results or teammate AI use invented.

Final checks also verified offline model caches, TLS target behavior, UID/capabilities/no-new-privileges, shellcheck, frontend build, deployment env quoting/config rejection, and report evidence chronology. Local custody head is anchored in docs/load-tests/local-builds/custody-head.json.

### Camila handoff (David Molina, 2026-11-10)

Codex traced the existing frontend client/socket, dataset validator, analysis
operations, load client and Cognito configuration to write docs/handoff-camila.md.
The handoff records Camila's scope, exact integration points, completion checks
and external prerequisites. Corrected the report contract wording to distinguish
the API's presigned URL envelope from the downloaded report JSON. Documentation
links and referenced paths were checked locally; no new cloud or model run was
needed for this documentation change.

## Camila Hidalgo: dataset, M5 and frontend (Claude Code, Claude Opus 5.5)

Claude Code worked from `docs/handoff-camila.md` in Camila's session; Camila
supplied the scope, the team's instructions, the Terraform outputs and the
Cognito test users, and reviewed the results. Rows below are only this work.

| Date · member | Tool | Component | Purpose | Summarized prompt | Result obtained | Validated / corrected / discarded | How it was validated |
|---|---|---|---|---|---|---|---|
| 2026-11-11 - Camila Hidalgo | Claude Code (Opus 5.5) | Dataset generators | Build the 40-case dataset | Implement the dataset part of handoff-camila.md with synthetic or licensed data | `fetch_coco.py`, `corpus.py`, `tts.py`, `generate_dataset.py`, `dataset_stats.py`, `evaluate.py`; 40 cases, 21,000 messages, 300 COCO images, 150 SAPI voice notes, 15 videos, 5 heavy cases | Validated | `validate_manifests.py --verify-files` (0 errors); independent recount with the worker parser and ffprobe; full evaluation over 505 labeled sources with 0 op errors |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: COCO selection | Pick image positives | Select 80 knife/scissors images with permissive licenses | The plan assumed 80 positives; only 73 permissively licensed val2017 images exist, and the first generator sampled 300 of 310 images at random, which could silently drop positives | Corrected: take all 73 positives plus seeded negatives | Script aborted with "only 73 positive images available"; selection rerun; generator now asserts all positives are kept |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: chat group names | Realistic exports | Generate group chats per scenario | Group names were derived from the scenario ("Extortion 35"), which no real chat uses and which made the rules flag group-created system lines as illicit activity | Corrected: neutral group names with the same RNG sequence (labels and media unchanged) | Found in the false-positive breakdown of the first evaluation; after the fix those FPs disappeared and the ground truth diff was empty |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: line endings | Commit chat exports | Commit manifests and small exports | First commit plan relied on default Git settings; with `core.autocrlf=true` a Windows checkout would rewrite the exports and break their SHA-256 in the manifests | Corrected: `dataset/.gitattributes` marks `cases/**` as `-text` | Git CRLF warning on the generator files; `--verify-files` passes on the committed bytes |
| 2026-11-11 - Camila Hidalgo | Claude Code | Ground-truth wording | Hard negatives | Add harmless messages that contain rule keywords | One proposed hard negative ("qué mierda de clima hoy") is arguably offensive language, so labeling it negative would be disputable | Corrected: replaced with "golpear la bola más fuerte" | Manual review of every hard negative in `corpus.py` |
| 2026-11-11 - Camila Hidalgo | Claude Code | M5 benchmark | Measure Whisper/YOLO CPU configurations | Benchmark threads and quantization on the dataset, per the M5 README and handoff | `bench.py`/`bench_one.py`/`plot.py`; 804 warm runs over 20 configurations; module report with routing decision and dated AWS prices | Validated | Every configuration in a fresh process; numbers in the report recomputed from `runs.csv`/`summary.csv` |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: YOLO thread control | Vary YOLO threads | Set OMP_NUM_THREADS / torch.set_num_threads per configuration | The first version believed the env var and later `set_num_threads` controlled YOLO; ultralytics' `select_device()` resets torch to min(8, cpu_count - 1) on the first predict, so all four "thread" configurations really ran with 7 threads | Corrected in the benchmark (pin ultralytics' `NUM_THREADS`) and in `image.py` (honor `MODEL_THREADS`) | Smoke run's `torch_threads` column showed 7 for every row; traced to `ultralytics/utils/torch_utils.py`; after the fix the column shows 1/2/4/8 and `run_local` keeps 2 threads after predict |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: results lost on interruption | Long benchmark | Run the full campaign in the background | `bench.py` wrote its CSVs only at the end; the laptop's crashes (pre-existing Windows bugchecks) killed the run after 4 of 9 audio configurations and every raw row was lost | Corrected: CSVs and `raw/` rewritten after each configuration, `--resume`, detached launch | Two later crashes cost only the configuration in progress; the campaign completed with no configuration mixing sessions |
| 2026-11-11 - Camila Hidalgo | Claude Code | AI error: report numbers | Write the M5 report | Fill method and interpretation | Draft stated 619 s of audio (real 610 s), 523 s for the parallel set (real 580 s), and two speed-up ranges that did not match the CSVs | Corrected | Recomputed every quoted number from the CSVs before committing |
