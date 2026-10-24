"""`GET /healthz`: liveness of the background threads.

Lives outside `/api/*` (mounted at the root by `main.py`) so that the systemd
or load balancer health check can hit it directly without going through
`OriginVerifyMiddleware` or CloudFront (see `security.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    """`app.state.threads` is populated by `main.py` (name -> `threading.Thread`).

    `ws_clients` comes from `app.state.broadcaster.client_count` (see
    `monitor/broadcaster.py`): how many `/api/ws` WS clients are connected
    right now.
    """
    threads: dict[str, object] = getattr(request.app.state, "threads", {})
    alive = {name: thread.is_alive() for name, thread in threads.items()}
    ok = all(alive.values())

    broadcaster = getattr(request.app.state, "broadcaster", None)
    ws_clients = broadcaster.client_count if broadcaster is not None else 0

    return JSONResponse(
        {"ok": ok, "threads": alive, "ws_clients": ws_clients},
        status_code=200 if ok else 503,
    )
