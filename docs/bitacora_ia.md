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
