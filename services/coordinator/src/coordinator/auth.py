"""Cognito access-token validation and route-wide role authorization."""

from functools import lru_cache

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

ROLES = {"analyst", "supervisor", "administrator"}


@lru_cache(maxsize=4)
def jwks(issuer):
    return PyJWKClient(issuer.rstrip("/") + "/.well-known/jwks.json", timeout=5)


def validate_token(token, settings):
    if not settings.COGNITO_ISSUER_URL:
        if settings.ORIGIN_VERIFY_SECRET:
            raise HTTPException(
                503, "Cognito is required when origin protection is configured"
            )
        return {"sub": "local", "cognito:groups": ["administrator"]}
    try:
        key = jwks(settings.COGNITO_ISSUER_URL).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.COGNITO_ISSUER_URL,
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "sub", "token_use", "client_id"],
            },
        )
        if (
            claims["token_use"] != "access"
            or claims["client_id"] != settings.COGNITO_CLIENT_ID
        ):
            raise jwt.InvalidTokenError("Wrong token use or client")
        if not isinstance(claims.get("cognito:groups", []), list):
            raise jwt.InvalidTokenError("Invalid groups")
        return claims
    except (jwt.PyJWTError, jwt.PyJWKClientError, ValueError) as exc:
        raise HTTPException(
            401,
            "Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def authorize(claims, method, path):
    groups = set(claims.get("cognito:groups", [])) & ROLES
    required = (
        {"administrator"}
        if path.startswith("/api/admin/")
        else ({"supervisor", "administrator"} if method == "DELETE" else ROLES)
    )
    if not groups & required:
        raise HTTPException(403, "Role does not permit this operation")


def bearer(header):
    scheme, _, token = (header or "").partition(" ")
    return token if scheme.lower() == "bearer" else ""
