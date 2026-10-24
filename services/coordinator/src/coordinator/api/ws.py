"""`WS /api/ws`: pushes the live `StateResponse` (computed by
`monitor/snapshot.py` and distributed by `monitor/broadcaster.Broadcaster`)
instead of having the dashboard poll `GET /api/state`. Polling remains as a
client-side fallback if the socket drops.

Authentication at the handshake: `OriginVerifyMiddleware` (`security.py`) is a
`BaseHTTPMiddleware`, and Starlette does NOT run it on WebSocket connections
(it lets any scope that is not `http` through untouched). That is why here we
revalidate `X-Origin-Verify` by hand, BEFORE accepting the connection
(`accept()`), with `security.verify_origin`: the same `hmac.compare_digest`
comparison and the same "empty secret = local mode" rule the HTTP middleware
already uses.

Message format (contract with the dashboard), two types via the `type`
field:

    {"type": "state", "data": <StateResponse serializado>}
    {"type": "ping"}
The `"state"` envelope already comes assembled from `Broadcaster.publish`; it is
never re-parsed/re-serialized here, only forwarded as is to each client.
The `"state"` envelope already comes assembled from `Broadcaster.publish`; here it is never
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..security import verify_origin
from .deps import SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

_HEADER = "X-Origin-Verify"
_CLOSE_UNAUTHORIZED = 1008
_CLOSE_TOO_MANY_CLIENTS = 1013
_PING_MESSAGE = json.dumps({"type": "ping"})


@router.websocket("/ws")
async def ws_state(websocket: WebSocket, settings: SettingsDep) -> None:
    provided = websocket.headers.get(_HEADER, "")
    if not verify_origin(provided, settings.ORIGIN_VERIFY_SECRET):
        await websocket.close(code=_CLOSE_UNAUTHORIZED)
        return

    broadcaster = websocket.app.state.broadcaster
    if broadcaster.client_count >= settings.WS_MAX_CLIENTS:
        await websocket.close(code=_CLOSE_TOO_MANY_CLIENTS)
        return

    # The slot is reserved HERE, before `accept()`, not after: between the
    # check above and this `register` there is no `await`, so the
    # check-and-reserve pair is atomic with respect to other incoming
    # connections (which can only interleave at the `await` of `accept()`
    # below, with this connection's slot already reserved). If `register()`
    # stayed after `accept()` as before, several concurrent connections
    # could pass the check before any of them got to register, exceeding
    # `WS_MAX_CLIENTS`.
    queue = broadcaster.register(websocket)
    try:
        await websocket.accept()
        if broadcaster.latest is not None:
            await websocket.send_text(broadcaster.latest)

        send_task = asyncio.create_task(_send_loop(websocket, queue))
        ping_task = asyncio.create_task(
            _ping_loop(websocket, settings.WS_PING_INTERVAL_S)
        )
        recv_task = asyncio.create_task(_recv_loop(websocket))
        pending = {send_task, ping_task, recv_task}

        try:
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    if not _is_expected_disconnect(task.exception()):
                        logger.exception(
                            "a WS connection task ended with an unexpected error"
                        )
    finally:
        broadcaster.unregister(queue)


def _is_expected_disconnect(exc: BaseException | None) -> bool:
    """`True` if `exc` is the expected result of the client having
    closed the connection (a `WebSocketDisconnect`, or the `RuntimeError`
    Starlette raises when trying to send on an already-closed socket), not a
    real failure worth logging."""
    if exc is None:
        return False
    if isinstance(exc, WebSocketDisconnect):
        return True
    return isinstance(exc, RuntimeError) and "close message" in str(exc)


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
    """Forward as is whatever comes out of the client's own queue."""
    while True:
        message = await queue.get()
        await websocket.send_text(message)


async def _ping_loop(websocket: WebSocket, interval_s: float) -> None:
    """CloudFront closes idle connections; this application-level ping prevents
    that and also helps detect dead clients."""
    while True:
        await asyncio.sleep(interval_s)
        await websocket.send_text(_PING_MESSAGE)


async def _recv_loop(websocket: WebSocket) -> None:
    """The dashboard sends nothing; this only exists to detect
    `WebSocketDisconnect` when the client closes."""
    while True:
        await websocket.receive_text()
