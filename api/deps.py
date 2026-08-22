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

# The ladder, for the three roles that ARE a ladder. `uploader` is deliberately
# absent: it is not "below viewer", it is sideways - it may write loads and may
# not read a single number. Asking `at_least` about it always says no, which is
# the safe answer for every endpoint that gates on seniority.
ROLE_RANK = {"viewer": 0, "analyst": 1, "owner": 2}

# What each role may do. This is the real authorisation table; ROLE_RANK only
# survives for the endpoints already written against it.
#
#   read    see numbers, orders, customers, the copilot
#   ingest  upload files and watch their own loads
#   config  edit countries, connections, costs, aliases
#   manage  invite people and change their grants
CAPABILITIES: dict[str, frozenset[str]] = {
    "owner": frozenset({"read", "ingest", "config", "manage"}),
    "analyst": frozenset({"read", "ingest", "config"}),
    "viewer": frozenset({"read"}),
    "uploader": frozenset({"ingest"}),
}

CAPABILITY_DENIED = {
    "read": "Tu usuario solo puede cargar archivos, no ver resultados.",
    "ingest": "Tu usuario no tiene permiso para cargar datos.",
    "config": "Tu usuario no puede cambiar la configuración.",
    "manage": "Solo el propietario de la sociedad puede administrar usuarios.",
}


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID | None
    email: str
    role: str
    org_id: UUID | None = None
    is_org_admin: bool = False
    # None = every country the company operates in. A tuple = the only ones this
    # membership may read.
    countries: tuple[str, ...] | None = None

    def at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]

    def can(self, capability: str) -> bool:
        return capability in CAPABILITIES.get(self.role, frozenset())

    def may_read_country(self, country: str) -> bool:
        return self.countries is None or country.upper() in self.countries


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
        countries = payload.get("cty")
        return CurrentUser(
            id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tid"]) if payload.get("tid") else None,
            email=payload["email"],
            role=payload["role"],
            org_id=UUID(payload["oid"]) if payload.get("oid") else None,
            is_org_admin=bool(payload.get("adm", False)),
            countries=tuple(c.upper() for c in countries) if countries else None,
        )
    except (KeyError, ValueError, TypeError) as exc:
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


def require_cap(capability: str):
    """Dependency factory keyed on capability instead of seniority.

    `require_role` asks "is this person senior enough". That question has no
    answer for `uploader`, who outranks nobody and still must be allowed to
    POST a file. Mount this on the router instead: one line decides a whole
    surface, and a new role only has to be added to CAPABILITIES.
    """

    def dependency(user: CurrentUserDep) -> CurrentUser:
        if not user.can(capability):
            raise Forbidden(
                CAPABILITY_DENIED.get(capability, f"Falta el permiso '{capability}'.")
            )
        return user

    return dependency


def require_any_cap(*capabilities: str):
    """Pass if the caller holds ANY of these.

    For the surfaces both readers and uploaders legitimately need: choosing a
    country and a connection is part of uploading a file, so `/config` cannot be
    read-only without breaking the one screen an `uploader` is allowed to use.
    The endpoints inside that router that DO expose people or change settings
    carry their own, stricter guard.
    """

    def dependency(user: CurrentUserDep) -> CurrentUser:
        if not any(user.can(cap) for cap in capabilities):
            raise Forbidden(CAPABILITY_DENIED.get(capabilities[0], "No tienes permiso"))
        return user

    return dependency


def _guard_country(user: CurrentUser, request: Request) -> None:
    """A limited membership may not read a country outside its scope.

    Read straight off the query string rather than from a typed parameter, so
    the rule covers every endpoint that takes `country` - present and future -
    without each one having to remember it. The endpoints with no `country` at
    all (the multi-country roll-ups) filter their rows instead; see
    `visible_countries`.
    """
    if user.countries is None:
        return
    country = request.query_params.get("country")
    if country and not user.may_read_country(country):
        raise Forbidden(
            f"Tu usuario solo tiene acceso a: {', '.join(user.countries)}."
        )


def db_for_user(user: CurrentUserDep, request: Request) -> Iterator[psycopg.Connection]:
    """A connection already scoped to the caller's tenant."""
    if user.tenant_id is None:
        raise Forbidden(
            "Tu usuario no pertenece a ninguna sociedad. Pide que te agreguen a una."
        )
    _guard_country(user, request)
    with connection(user.tenant_id) as conn:
        yield conn


DbDep = Annotated[psycopg.Connection, Depends(db_for_user)]


def db_unscoped() -> Iterator[psycopg.Connection]:
    """For endpoints that run before a tenant exists: login, register, health."""
    with connection() as conn:
        yield conn


UnscopedDbDep = Annotated[psycopg.Connection, Depends(db_unscoped)]


def filter_by_scope(user: CurrentUser, rows: list[dict], key: str = "country_code") -> list[dict]:
    """Drop rows for countries this membership may not read.

    For the multi-country endpoints, where there is no `country` parameter to
    guard: `/kpis/global` IS the comparison between countries, so a partner
    limited to Guatemala must see one row, not four.
    """
    if user.countries is None:
        return rows
    return [row for row in rows if str(row.get(key) or "").upper() in user.countries]


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
