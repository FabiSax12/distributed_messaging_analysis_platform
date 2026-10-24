"""`WS /api/ws` (`api/ws.py`) + `monitor/broadcaster.Broadcaster`.

Own minimal app (same pattern as `test_security.py`): we do not use the real
`coordinator.main.app`, which starts background threads against AWS. Here we
only build a minimal FastAPI app with the WS router and its own `Broadcaster`,
injecting `Settings` via `app.dependency_overrides` instead of real
environment variables.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from coordinator.api import ws as ws_router
from coordinator.config import get_settings
from coordinator.monitor.broadcaster import Broadcaster
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from shared.api import Alert, CaseSummary, QueueDepth, StateResponse, WorkerView
from shared.messages import utcnow
from shared.models import CaseItem
from shared.routing import Pool
from starlette.websockets import WebSocketDisconnect

_HEADER = "X-Origin-Verify"


def _settings(
    *, secret: str = "", ping_interval_s: float = 9999.0, max_clients: int = 20
) -> SimpleNamespace:
    return SimpleNamespace(
        ORIGIN_VERIFY_SECRET=secret,
        WS_PING_INTERVAL_S=ping_interval_s,
        WS_MAX_CLIENTS=max_clients,
    )


def _build_app(settings: SimpleNamespace) -> tuple[FastAPI, Broadcaster]:
    broadcaster = Broadcaster()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        broadcaster.bind_loop(asyncio.get_running_loop())
        app.state.broadcaster = broadcaster
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router.router, prefix="/api")
    app.dependency_overrides[get_settings] = lambda: settings
    return app, broadcaster


def _state(case_id: str = "c1") -> StateResponse:
    return StateResponse(
        cases=[
            CaseSummary(
                case=CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1),
                by_status={"pending": 1},
            )
        ],
        workers=[],
        queues=[QueueDepth(queue="video-alta", visible=0, in_flight=0)],
        alerts=[],
    )


def _case_id_of(message: dict) -> str:
    return message["data"]["cases"][0]["case"]["case_id"]


# ---- Handshake authentication ----------------------------------------- #


def test_rejects_connection_without_header_when_secret_configured() -> None:
    app, _broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/ws"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_rejects_connection_with_wrong_header_value_when_secret_configured() -> None:
    # Header present but with a value that does not match the secret: it must
    # be rejected just like a missing header (code 1008), not mistaken for an
    # "authenticated" case.
    app, _broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/ws", headers={_HEADER: "wrong-value"}),
    ):
        pass
    assert exc_info.value.code == 1008


def test_accepts_connection_with_correct_header_when_secret_configured() -> None:
    app, broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", headers={_HEADER: "s3cr3t"}),
    ):
        assert broadcaster.client_count == 1


def test_empty_secret_lets_connection_through() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1


def test_exceeding_max_clients_is_rejected_with_1013() -> None:
    app, broadcaster = _build_app(_settings(secret="", max_clients=1))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/ws"),
        ):
            pass
        assert exc_info.value.code == 1013


def test_rejected_connection_never_calls_accept_and_never_exceeds_max_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOCTOU regression: the `WS_MAX_CLIENTS` check and the slot reservation
    (`broadcaster.register`) must not have any `await` in between.
    We verify it indirectly: the connection that exceeds the limit must be
    rejected BEFORE calling `websocket.accept()`, and the client count must
    never exceed the maximum, not even momentarily."""
    accept_calls: list[WebSocket] = []
    original_accept = WebSocket.accept

    async def _tracking_accept(
        self: WebSocket, *args: object, **kwargs: object
    ) -> None:
        accept_calls.append(self)
        await original_accept(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "accept", _tracking_accept)

    app, broadcaster = _build_app(_settings(secret="", max_clients=1))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1
        assert len(accept_calls) == 1

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/ws"),
        ):
            pass

        assert exc_info.value.code == 1013
        # The rejected connection never reached `accept()`.
        assert len(accept_calls) == 1
        # The limit was never exceeded, not even momentarily.
        assert broadcaster.client_count == 1


# ---- Initial state on connect ---------------------------------------------- #


def test_new_client_receives_last_published_state_immediately() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as warmup_ws:
            broadcaster.publish(_state("c1"))
            warmup_ws.receive_json()  # make sure `latest` is already set

        with client.websocket_connect("/api/ws") as ws:
            message = ws.receive_json()
            assert message["type"] == "state"
            assert _case_id_of(message) == "c1"


def test_new_client_gets_nothing_immediately_when_no_state_published_yet() -> None:
    app, _broadcaster = _build_app(_settings(secret="", ping_interval_s=0.05))
    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        # With no prior state, the first thing it receives is the next ping,
        # not a "state" message.
        message = ws.receive_json()
        assert message == {"type": "ping"}


# ---- Thread -> loop bridge ------------------------------------------------------ #


def test_publish_from_real_thread_reaches_two_connected_clients() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    state = _state("c1")

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws") as ws1,
        client.websocket_connect("/api/ws") as ws2,
    ):
        thread = threading.Thread(target=broadcaster.publish, args=(state,))
        thread.start()
        thread.join()

        msg1 = ws1.receive_json()
        msg2 = ws2.receive_json()

    assert msg1["type"] == "state"
    assert msg2["type"] == "state"
    assert _case_id_of(msg1) == "c1"
    assert _case_id_of(msg2) == "c1"


# ---- Deduplication ignoring generated_at -------------------------------------- #


def test_publishing_same_content_with_different_generated_at_sends_once() -> None:
    app, broadcaster = _build_app(_settings(secret=""))

    state1 = _state("c1")
    state_same_content = state1.model_copy(
        update={"generated_at": state1.generated_at + timedelta(seconds=5)}
    )

    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        broadcaster.publish(state1)
        first = ws.receive_json()
        assert _case_id_of(first) == "c1"

        # Same content except `generated_at`: must not trigger a send.
        broadcaster.publish(state_same_content)

        # We confirm the discard at the broadcaster level (without another
        # publish in between, which would mask the result via the size-1
        # queue's "keep only the latest" behavior).
        assert broadcaster.latest is not None
        assert first == json.loads(broadcaster.latest)


def test_publishing_same_content_with_live_worker_and_alert_sends_once() -> None:
    """Regression: with a `WorkerView` and an `Alert` in the state, `last_seen`
    and `ts` are regenerated on every snapshot cycle even if nothing relevant
    changed (the worker is still alive, the alert is still active). If
    deduplication does not ignore those two fields (besides `generated_at`),
    this fires one message per cycle even though the "real" content is
    identical."""
    app, broadcaster = _build_app(_settings(secret=""))

    worker = WorkerView(
        worker_id="video-i-0abc123-4711",
        pool=Pool.VIDEO,
        instance_type="c7i-flex.large",
        hostname="host1",
        concurrency=2,
        started_at=utcnow(),
        last_seen=utcnow(),
        alive=True,
    )
    alert = Alert(level="warning", pool="video", message="high queue")

    state1 = _state("c1").model_copy(update={"workers": [worker], "alerts": [alert]})

    state2 = state1.model_copy(
        update={
            "generated_at": state1.generated_at + timedelta(seconds=5),
            "workers": [
                worker.model_copy(
                    update={"last_seen": worker.last_seen + timedelta(seconds=5)}
                )
            ],
            "alerts": [
                alert.model_copy(update={"ts": alert.ts + timedelta(seconds=5)})
            ],
        }
    )

    # We count the real deliveries (`_deliver`) instead of looking only at
    # `broadcaster.latest`: since `publish()` schedules the delivery with
    # `call_soon_threadsafe`, comparing `latest` right after publishing,
    # without forcing the loop to process its pending callback queue, is a
    # race that can give a false green even if deduplication is broken (the
    # `_deliver` for `state2` simply has not run yet). Wrapping `_deliver` and
    # forcing a "marker" delivery afterwards gives us a deterministic
    # synchronization point: by the time the client receives the marker, any
    # `_deliver` scheduled earlier (the one for `state2`, if deduplication
    # failed) has already run, because `call_soon_threadsafe` preserves the
    # FIFO order of callbacks in the loop.
    delivered: list[str] = []
    original_deliver = broadcaster._deliver

    def _tracking_deliver(message: str) -> None:
        delivered.append(message)
        original_deliver(message)

    broadcaster._deliver = _tracking_deliver

    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        broadcaster.publish(state1)
        first = ws.receive_json()
        assert _case_id_of(first) == "c1"

        # Same "real" content, only `generated_at`/`last_seen`/`ts` changed:
        # must not trigger a send.
        broadcaster.publish(state2)

        broadcaster.publish(_state("marker"))
        assert _case_id_of(ws.receive_json()) == "marker"

    # Only two real deliveries should have run: the one for `state1` and the
    # marker's. If deduplication does not ignore `last_seen`/`ts`, `state2`
    # triggers a third one.
    assert len(delivered) == 2


# ---- A slow client does not block the others -------------------------------------- #
#
# `TestClient` carries the WS over an in-memory stream with NO size limit:
# a connection's internal `_send_loop` can always drain its queue as soon as
# a message arrives, even if the test has not read anything yet (the real
# "slowness" happens at the socket, not in our queue). To test the size-1
# queue contract (which is what protects a genuinely slow client -- one whose
# `websocket.send_text` is blocked waiting for the other side to drain the
# socket) we have to exercise it at the `Broadcaster` level: a registered
# client whose queue is never read.


class _FakeWebSocket:
    async def close(self, code: int = 1000) -> None:
        return None


def test_slow_client_queue_never_accumulates_backlog() -> None:
    app, broadcaster = _build_app(_settings(secret=""))

    with TestClient(app) as client:
        # "Slow" client: registered in the broadcaster, but nothing consumes
        # its queue (unlike a real `websocket_connect` connection, which
        # starts its own `_send_loop`).
        slow_queue = broadcaster.register(_FakeWebSocket())

        with client.websocket_connect("/api/ws") as fast_ws:
            broadcaster.publish(_state("c1"))
            assert _case_id_of(fast_ws.receive_json()) == "c1"

            # Two more publishes with nobody reading the slow client's queue.
            broadcaster.publish(_state("c2"))
            broadcaster.publish(_state("c3"))

            # The fast one keeps receiving them without any blocking.
            # `_send_loop` runs in parallel with these `publish` calls, so c2
            # may arrive before c3 has been published: read until c3 shows
            # up, do not assume the next message is already the last.
            last_case_id = None
            for _ in range(3):
                last_case_id = _case_id_of(fast_ws.receive_json())
                if last_case_id == "c3":
                    break
            assert last_case_id == "c3"

        # The slow one never accumulated a backlog: its queue (size 1) holds a
        # single element, and it is the last published state, not a stale one.
        assert slow_queue.qsize() == 1
        message = json.loads(slow_queue.get_nowait())
        assert _case_id_of(message) == "c3"


# ---- Client count ------------------------------------------------------- #


def test_client_count_increases_and_decreases_with_connection_lifecycle() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client:
        assert broadcaster.client_count == 0
        with client.websocket_connect("/api/ws"):
            assert broadcaster.client_count == 1
        assert broadcaster.client_count == 0


# ---- Unexpected exceptions in connection tasks ---------------------- #


def test_unexpected_exception_in_connection_task_is_logged_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If `_send_loop`/`_ping_loop`/`_recv_loop` raises a real exception (not
    a cancellation or a normal client disconnect), it must be logged --
    `contextlib.suppress(asyncio.CancelledError, Exception)` would swallow it
    silently. Cleanup (unregistering the client) must still happen."""

    async def _boom_ping_loop(websocket: WebSocket, interval_s: float) -> None:
        raise ValueError("simulated unexpected failure")

    monkeypatch.setattr(ws_router, "_ping_loop", _boom_ping_loop)

    app, broadcaster = _build_app(_settings(secret=""))

    with (
        caplog.at_level(logging.ERROR, logger="coordinator.api.ws"),
        TestClient(app) as client,
        client.websocket_connect("/api/ws"),
    ):
        pass

    assert "simulated unexpected failure" in caplog.text
    # Normal cleanup (unregistering the queue) must still happen.
    assert broadcaster.client_count == 0


def test_normal_disconnect_is_not_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Counterpart of the previous test: a normal client close (which raises
    `WebSocketDisconnect` in `_recv_loop`) must not produce any error log --
    only unexpected exceptions deserve noise."""
    app, broadcaster = _build_app(_settings(secret=""))

    with (
        caplog.at_level(logging.ERROR, logger="coordinator.api.ws"),
        TestClient(app) as client,
        client.websocket_connect("/api/ws"),
    ):
        pass

    assert caplog.text == ""
    assert broadcaster.client_count == 0


# ---- Shutdown: `close_all` with connected clients ------------------------------ #


def test_close_all_closes_connected_client_and_resets_client_count() -> None:
    """The real `lifespan` calls `broadcaster.close_all(...)` when the
    coordinator shuts down (see `main.py`). The existing tests use an empty
    test `lifespan` that never exercises that path; here we build a real
    `Broadcaster`, connect a client, and call `close_all` directly."""
    app, broadcaster = _build_app(_settings(secret=""))

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as ws:
            assert broadcaster.client_count == 1

            # `close_all` is a coroutine that must run on the app's loop (the
            # same one `bind_loop` stored); we schedule it from this test
            # thread just as the real `lifespan` would on shutdown.
            future = asyncio.run_coroutine_threadsafe(
                broadcaster.close_all(code=1001), broadcaster._loop
            )
            future.result(timeout=5)

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 1001

        assert broadcaster.client_count == 0
