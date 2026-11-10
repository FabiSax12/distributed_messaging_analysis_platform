# M2: Scheduling

## 1. Implementation

The coordinator switches `SCHEDULER_POLICY=fifo|aging`. Aging adds one level per
`SCHEDULER_AGING_S` to the four-level priority, capped at critical; equal
priorities use creation time then ID. Selection happens in SQL before the batch
limit, independently per pool so one blocked pool cannot hide other work.
The broker publish window defaults to one waiting message per pool; other
work remains durable in the outbox, where its age is recalculated. Raising
that window trades responsiveness/fairness for throughput. A mature low-priority
task reaches critical and wins ties over new critical arrivals.

## 2. OS concept

Priority scheduling can starve lower priorities; aging raises waiting work.
FIFO provides an arrival-order baseline. Broker prefetch is one per consumer.

## 3. Method

`experiments/m2_scheduling/scripts/local_compare.py` runs two sequential campaigns
against real PostgreSQL/RabbitMQ with one isolated fake text worker, 40 cases,
20 concurrent clients, 0.5 seconds fake work and low/critical/medium/high priority
rotation by client index. Aging interval is shortened to 2 seconds. Do not run
it beside the real-model campaign on the shared dev broker. Raw CSVs and exact
parameters are under `results/local/`. `measure.py` runs labeled campaigns on
an externally configured server; restart the coordinator to switch policy.

## 4. Results

Both sequential campaigns completed all 40 cases. End-to-end latency includes
upload, admission, queueing, spawned-process startup, fake work and reporting:
FIFO p50 32.61 s / p95 37.16 s; aging p50 32.44 s / p95 35.18 s (nearest-rank).
These single runs do not establish statistical superiority. See raw campaign
CSVs for actual per-case latencies and method.json for parameters.
The executable scheduler check proves an old low-priority task ties a new
critical task at critical and is selected first, and confirms FIFO ordering.
The local campaign uses fake work to isolate scheduling rather than model
speed. Cloud comparison and the final dataset workload remain Fabi?n's run.

## 5. Interpretation

An execution-order guarantee is provided at the durable scheduling boundary,
not by retroactively changing messages already accepted by RabbitMQ. One
waiting broker message per pool prevents a large stale broker-priority backlog.
PSS, model speed or GPU routing are not conclusions of this experiment.
