"""`X-Origin-Verify` middleware: prevents anyone from hitting the coordinator's
elastic IP directly, bypassing CloudFront.

CloudFront (Terraform `cdn` module) adds that header with a shared secret to
every request it proxies to `/api/*`; the origin (this coordinator) verifies it.
The secret lives in Secrets Manager; see `infra/terraform/README.md` (the
CloudFront prefix list is shared by all CloudFront customers, so the network
rule alone is not enough).
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_HEADER = "X-Origin-Verify"


def verify_origin(provided: str | None, secret: str) -> bool:
    """`True` if `provided` matches `secret`, comparing with
    `hmac.compare_digest` (avoids timing attacks). If `secret` is empty
    (local mode, no frontend deployed) it is always `True`.

    Extracted from `OriginVerifyMiddleware.dispatch` so that `api/ws.py` can
    reuse exactly the same validation in the WebSocket handshake, which
    `BaseHTTPMiddleware` does not cover (see its docstring).
    """
    if not secret:
        return True
    return hmac.compare_digest(provided or "", secret)


class OriginVerifyMiddleware(BaseHTTPMiddleware):
    """Require `X-Origin-Verify == secret` on every route under `/api/`.

    `/healthz` is always left open (used by the systemd/ALB health check, which
    does not go through CloudFront). If `secret` is empty (local development without
    a deployed frontend) a warning is logged once when building the
    middleware and everything is let through.
    """

    def __init__(self, app, secret: str) -> None:
        super().__init__(app)
        self._secret = secret
        if not secret:
            logger.warning(
                "ORIGIN_VERIFY_SECRET is empty: /api/* is left unprotected "
                "(local mode, do not use like this in production)"
            )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        provided = request.headers.get(_HEADER, "")
        if not verify_origin(provided, self._secret):
            return JSONResponse({"detail": "origin no verificado"}, status_code=403)

        return await call_next(request)
