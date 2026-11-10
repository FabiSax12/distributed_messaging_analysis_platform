# AI usage log

> At least two documented cases of AI errors or inaccuracies

| Date · member | Tool | Component | Purpose | Summarized prompt | Result obtained | Validated / corrected / discarded | How it was validated |
|---|---|---|---|---|---|---|---|
| 2026-11-10 ? David Molina | Codex | Analysis workers | Implement local analysis | Follow AnalysisResult and existing runner contracts | Bilingual rules, YOLOv8n and Whisper tiny with source evidence | Validated | Synthetic WhatsApp/Telegram, invalid JSON, deadlines, actual YOLO blank image and Whisper video extraction; check_analysis.py |
| 2026-11-10 ? David Molina | Codex | Audio dependency error | Choose compatible Whisper dependencies | Add faster-whisper optional extra | Unbounded PyAV selected 19, which rejected metadata_errors used by Whisper 1.2.1 | Corrected: av<19 | First real video check failed with TypeError; rerun with PyAV 18.1.0 passed |
| 2026-11-10 ? David Molina | Codex | M1 reservation ordering error | Persist admission reservation without schema migration | Store reservation in first subtask | Initial code assumed list order matched SQL ORDER BY subtask_id | Corrected: use minimum subtask_id explicitly | Reopened planner/repository ordering; admission executable checks passed; PostgreSQL integration still pending |
| 2026-11-10 ? David Molina | Codex | M1 | Admission and memory experiments | Limit accepted work and measure real models | Atomic advisory-lock admission; native RSS profiles; Linux cgroup/fork scripts | Partially validated | check_admission.py with SQL fakes; native profile CSVs. Docker/PSS/OOM/distributed recovery not run |
| 2026-11-10 ? David Molina | Codex | M3 | Implement existing storage Protocols | Bounded streaming, dedup and queue | stdlib components plus synthetic benchmarks | Validated locally; real dataset pending | Corruption, duplicate retries, concurrent puts, queue timeout/close checks; 200 MiB transfer at 3 chunk sizes, burst traces |

2026-11-10, David Molina: integrated the new main CI/CD changes into the analysis branch before opening stacked PRs. Image/audio production Dockerfiles now install their analysis extras; image runtime includes OpenCV libraries. Container builds remain delegated to GitHub CI because Docker is unavailable locally.
