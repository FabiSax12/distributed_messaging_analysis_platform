"""`GET /api/state` and `GET /api/metrics`: expose what `monitor/` already computed in
the background. Neither touches AWS within the request."""

from __future__ import annotations

from fastapi import APIRouter

from shared.api import MetricsResponse, StateResponse

from ..monitor.samples import get_recent_samples
from ..monitor.snapshot import get_latest_snapshot

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateResponse)
def get_state() -> StateResponse:
    snapshot = get_latest_snapshot()
    if snapshot is not None:
        return snapshot
    # The snapshot loop has not completed its first pass yet (process just
    # started): an empty StateResponse is more useful to the dashboard than
    # a 500.
    return StateResponse(cases=[], workers=[], queues=[], alerts=[])


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(minutes: int = 30) -> MetricsResponse:
    return MetricsResponse(samples=get_recent_samples(minutes))
