# Project II: implementation and evidence report

This is the code/local-evidence report. It does not certify a live AWS rollout
or substitute for the remaining dataset/frontend/M5 work.

## System

The coordinator admits cases, schedules by FIFO or aging, uses a durable
PostgreSQL outbox to publish RabbitMQ work and closes each subtask/case once.
Three CPU pools analyze text, images and audio/video. Analysis is isolated in
killable child processes; source evidence is hashed and report findings retain
message/region/segment locators. PostgreSQL custody triggers record mutations
atomically. Cognito validates user access and role permissions; the edge origin
header remains an independent access boundary.

## Measured local evidence

| Requirement | Evidence | Limits |
|---|---|---|
| Functional distributed pipeline | 20 concurrent real-model cases completed under Docker cgroups; final smoke verified findings and report timeline | Synthetic tiny inputs on local WSL, not AWS accuracy/scale |
| Memory M1 | RSS/PSS, actual cgroup OOM, passing higher limits, spawn vs fork loaded-weight measurements and worker burst trace | Coarse limit sweep; fork inference safety not asserted |
| Scheduling M2 | Sequential FIFO/aging 40-case campaigns, all completed; starvation ordering check | One run per policy, isolated fake text work; cloud comparison pending |
| Storage M3 | Verified 200 MiB transfer benchmark, corruption/concurrent dedup checks, bounded-queue traces; real worker/S3 CAS wiring | Dataset-wide ratio and the five final heavy cases await Camila |
| Security M4 | Signed-token, HTTP/WebSocket role/origin checks; actual PostgreSQL chain immutability; non-root/cgroup containers | Cloud Cognito, TLS activation and SNS receipt remain manual |
| Fault tolerance | Basic, unsupported, retries, worker kill, coordinator restart, DB outage and running-task cancellation passed locally | Fault scenarios use fake ops; separate real-model smoke/burst passed |

Detailed methods, raw paths, figures and limitations are in the module reports
and [load-test report](../load-tests/README.md). No statistical superiority or
real dataset detection accuracy is inferred from these smoke experiments.

## Remaining work by owner

- Fabián: plan/apply, repository runtime configuration, DNS/TLS activation,
  users, subscriptions, AWS rollout/smoke and cloud campaigns/screenshots.
- Camila: full licensed/synthetic dataset and ground truth, frontend PKCE/token/
  role/findings integration, M5 benchmark and measured routing decision.
- All members: interpret final cloud/dataset results and add their own AI-log
  entries. Fabián supplies Project I delivered commit and instructor feedback.

[The manual contract](../handoff-completion.md) is the actionable checklist.
Real cloud results should be appended when collected; missing results remain
marked pending rather than being filled with estimates.
