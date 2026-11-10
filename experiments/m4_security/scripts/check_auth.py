"""Offline signed-token/role checks; no Cognito credentials or network."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from coordinator.api.ws import router as ws_router
from coordinator.auth import authorize, validate_token
from coordinator.config import get_settings
from coordinator.monitor.broadcaster import Broadcaster
from coordinator.security import OriginVerifyMiddleware
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def main():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = SimpleNamespace(
        COGNITO_ISSUER_URL="https://cognito-idp.us-east-1.amazonaws.com/test",
        COGNITO_CLIENT_ID="client",
        ORIGIN_VERIFY_SECRET="origin",
    )
    base = {
        "iss": settings.COGNITO_ISSUER_URL,
        "client_id": "client",
        "sub": "synthetic",
        "exp": int(time.time()) + 60,
        "iat": int(time.time()),
        "token_use": "access",
        "cognito:groups": ["analyst"],
    }
    client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    with patch("coordinator.auth.jwks", return_value=client):
        token = jwt.encode(base, key, algorithm="RS256", headers={"kid": "test"})
        claims = validate_token(token, settings)
        authorize(claims, "POST", "/api/cases")
        for method, path in [("DELETE", "/api/cases/x"), ("GET", "/api/admin/users")]:
            try:
                authorize(claims, method, path)
            except HTTPException as exc:
                assert exc.status_code == 403
            else:
                raise AssertionError("Role bypass")
        for change in (
            {"exp": 1},
            {"client_id": "other"},
            {"token_use": "id"},
            {"iss": "wrong"},
        ):
            token = jwt.encode({**base, **change}, key, algorithm="RS256")
            try:
                validate_token(token, settings)
            except HTTPException as exc:
                assert exc.status_code == 401
            else:
                raise AssertionError("Invalid token accepted")
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            validate_token(jwt.encode(base, wrong_key, algorithm="RS256"), settings)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("Invalid signature accepted")
        settings.WS_MAX_CLIENTS = 5
        settings.WS_PING_INTERVAL_S = 25
        app = FastAPI()
        app.add_middleware(OriginVerifyMiddleware, secret="origin")
        app.dependency_overrides[get_settings] = lambda: settings
        app.state.broadcaster = Broadcaster()
        app.include_router(ws_router, prefix="/api")

        @app.get("/api/me")
        def identity(request: Request):
            return request.state.identity["sub"]

        @app.delete("/api/cases/probe")
        def cancellation():
            return {"cancelled": True}

        token = jwt.encode(base, key, algorithm="RS256")
        headers = {"X-Origin-Verify": "origin", "Authorization": "Bearer " + token}
        with (
            patch("coordinator.security.get_settings", return_value=settings),
            TestClient(app) as http,
        ):
            assert http.get("/api/me", headers=headers).status_code == 200
            assert (
                http.get(
                    "/api/me", headers={"Authorization": "Bearer " + token}
                ).status_code
                == 403
            )
            assert (
                http.get("/api/me", headers={"X-Origin-Verify": "origin"}).status_code
                == 401
            )
            assert http.delete("/api/cases/probe", headers=headers).status_code == 403
            with http.websocket_connect(
                "/api/ws",
                headers={"X-Origin-Verify": "origin"},
                subprotocols=["bearer", token],
            ) as socket:
                assert socket.accepted_subprotocol == "bearer"
            try:
                with http.websocket_connect(
                    "/api/ws", headers={"X-Origin-Verify": "origin"}
                ):
                    raise AssertionError("Anonymous WebSocket accepted")
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
    print("Signature, expiry, issuer, client, token-use and role checks passed")


if __name__ == "__main__":
    main()
