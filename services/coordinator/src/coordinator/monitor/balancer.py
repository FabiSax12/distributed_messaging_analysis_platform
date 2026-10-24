"""Dashboard alert rules: PURE functions, they do not touch AWS.

`monitor/snapshot.py` calls them with the data it already gathered (queues,
workers) and puts the result in `StateResponse.alerts`. Separating them like
this allows testing the rules without `moto` (see `services/coordinator/tests/test_balancer.py`).

TODO(actuation): for now these rules only generate `Alert`s for the
dashboard to show. The natural next step (out of scope for this
batch) would be for the balancer to publish a configuration that workers
read to decide when to help another pool (beyond the static `POLL_ORDER`
of `shared.routing`), e.g. an item in a table/S3 with per-pool weights
that the worker runner consults before choosing a queue.
"""

from __future__ import annotations

from shared.api import Alert, QueueDepth, WorkerView
from shared.routing import DLQ, Pool, Priority, queue_key


def check_pool_saturation(
    queues: list[QueueDepth],
    workers: list[WorkerView],
    *,
    queue_threshold: int,
    cpu_threshold: float,
) -> list[Alert]:
    """A pool is saturated if its high-priority queue is above the
    threshold, or if all of its live workers exceed the CPU threshold.

    "All of its live workers" on purpose: if a single worker is above
    the CPU threshold but others have headroom, the pool as a whole can still
    absorb work; we do not alert for a single hot worker.
    """
    alerts: list[Alert] = []
    depth_by_queue = {q.queue: q for q in queues}

    for pool in Pool:
        high_queue = queue_key(pool, Priority.HIGH)
        depth = depth_by_queue.get(high_queue)
        if depth is not None and depth.visible > queue_threshold:
            alerts.append(
                Alert(
                    level="warning",
                    pool=pool.value,
                    message=(
                        f"queue '{high_queue}' has {depth.visible} visible messages "
                        f"(threshold {queue_threshold})"
                    ),
                )
            )

        pool_workers = [w for w in workers if w.pool is pool and w.alive]
        if pool_workers and all(w.cpu_percent >= cpu_threshold for w in pool_workers):
            alerts.append(
                Alert(
                    level="warning",
                    pool=pool.value,
                    message=(
                        f"all live workers of pool '{pool.value}' exceed "
                        f"{cpu_threshold}% CPU"
                    ),
                )
            )

    return alerts


def check_pools_without_workers(workers: list[WorkerView]) -> list[Alert]:
    """A pool with no live worker cannot process anything from its queue."""
    alerts: list[Alert] = []
    for pool in Pool:
        if not any(w.pool is pool and w.alive for w in workers):
            alerts.append(
                Alert(
                    level="critical",
                    pool=pool.value,
                    message=f"pool '{pool.value}' has no live workers",
                )
            )
    return alerts


def check_dlq(queues: list[QueueDepth]) -> list[Alert]:
    """Any message in the DLQ is a subtask that exhausted its retries."""
    depth_by_queue = {q.queue: q for q in queues}
    dlq = depth_by_queue.get(DLQ)
    if dlq is not None and (dlq.visible > 0 or dlq.in_flight > 0):
        return [
            Alert(
                level="critical",
                pool="-",
                message=f"DLQ has {dlq.visible} visible messages and {dlq.in_flight} in flight",
            )
        ]
    return []


def check_dead_workers(workers: list[WorkerView]) -> list[Alert]:
    """A worker that stopped sending heartbeats.

    Uses the `alive` field already computed by `monitor/snapshot.py` (with
    `WorkerItem.is_alive()`/`WORKER_DEAD_AFTER_S`) instead of recomputing it here,
    so that all the rules in this pass see the same `now` instant.
    """
    return [
        Alert(
            level="warning",
            pool=worker.pool.value,
            message=f"worker '{worker.worker_id}' has no recent heartbeat",
        )
        for worker in workers
        if not worker.alive
    ]


def build_alerts(
    queues: list[QueueDepth],
    workers: list[WorkerView],
    *,
    queue_threshold: int,
    cpu_threshold: float,
) -> list[Alert]:
    """Collect all alert rules in the order they are displayed."""
    return [
        *check_dlq(queues),
        *check_pools_without_workers(workers),
        *check_pool_saturation(
            queues,
            workers,
            queue_threshold=queue_threshold,
            cpu_threshold=cpu_threshold,
        ),
        *check_dead_workers(workers),
    ]
