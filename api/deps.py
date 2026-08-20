"""Request dependencies: who is calling, and on which tenant's data.

Nothing reaches a router without passing through here, and nothing reads tenant
data without a connection already scoped to that tenant.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.db import check_rate_limit, connection
from api.errors import Forbidden, RateLimited, Unauthorized
from api.security import TokenError, decode_access_token
from api.settings import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)

ROLE_RANK = {"viewer": 0, "analyst": 1, "owner": 2}


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID
    email: str
    role: str

    def at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


def current_user(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise Unauthorized("Falta el token de acceso")

    try:
        payload = decode_access_token(settings, credentials.credentials)
    except TokenError as exc:
        raise Unauthorized(str(exc)) from exc

    try:
        return CurrentUser(
            id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tid"]),
            email=payload["email"],
            role=payload["role"],
        )
    except (KeyError, ValueError) as exc:
        raise Unauthorized("El token no tiene la forma esperada") from exc


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require_role(minimum: str):
    """Dependency factory. `require_role("owner")` gates destructive endpoints."""

    def dependency(user: CurrentUserDep) -> CurrentUser:
        if not user.at_least(minimum):
            raise Forbidden(
                f"Esta acción requiere rol '{minimum}' o superior. El tuyo es '{user.role}'."
            )
        return user

    return dependency


def db_for_user(user: CurrentUserDep) -> Iterator[psycopg.Connection]:
    """A connection already scoped to the caller's tenant."""
    with connection(user.tenant_id) as conn:
        yield conn


DbDep = Annotated[psycopg.Connection, Depends(db_for_user)]


def db_unscoped() -> Iterator[psycopg.Connection]:
    """For endpoints that run before a tenant exists: login, register, health."""
    with connection() as conn:
        yield conn


UnscopedDbDep = Annotated[psycopg.Connection, Depends(db_unscoped)]


def client_ip(request: Request) -> str:
    """Best-effort caller identity, for rate limiting. Always a string."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def client_ip_inet(request: Request) -> str | None:
    """The same value, but only if it really parses as an IP address.

    `X-Forwarded-For` is attacker-controlled and the test client reports a
    hostname. Storing either straight into an `inet` column turns a log line into
    a failed transaction, so anything unparseable becomes NULL.
    """
    import ipaddress

    candidate = client_ip(request)
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def rate_limit(scope: str, limit_attr: str):
    """Dependency factory for per-IP fixed-window limiting."""

    def dependency(
        request: Request,
        settings: SettingsDep,
        conn: UnscopedDbDep,
    ) -> None:
        limit = getattr(settings, limit_attr)
        if not check_rate_limit(conn, scope=scope, subject=client_ip(request), limit=limit):
            raise RateLimited()

    return dependency
