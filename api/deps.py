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
from api.errors import Forbidden, PaymentRequired, RateLimited, Unauthorized
from api.security import TokenError, constant_time_equals, decode_access_token
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
    "manage": "Solo el propietario de la empresa puede administrar usuarios.",
}


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID | None
    email: str
    role: str
    org_id: UUID | None = None
    is_org_admin: bool = False
    # admin | analyst | viewer over the whole holding, or None for someone who
    # only exists inside companies. NEVER grants access to a company's own data:
    # core.membership decides that, per company and per country.
    org_role: str | None = None
    # None = every country the company operates in. A tuple = the only ones this
    # membership may read.
    countries: tuple[str, ...] | None = None

    def at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]

    def can(self, capability: str) -> bool:
        return capability in CAPABILITIES.get(self.role, frozenset())

    def may_read_country(self, country: str) -> bool:
        return self.countries is None or country.upper() in self.countries

    def manages_org(self) -> bool:
        """May create companies and grant access across the holding."""
        return self.org_role == "admin" or self.is_org_admin

    def reads_org(self) -> bool:
        """May see the consolidated roll-up of every company in the holding."""
        return self.org_role in {"admin", "analyst", "viewer"} or self.is_org_admin


def tenant_of(user: CurrentUser) -> UUID:
    """The company the caller stands in, as a plain UUID.

    `CurrentUser.tenant_id` is Optional because an org admin may hold no
    membership at all. Every endpoint that runs on `DbDep` has already been
    refused for that case (see `db_for_user`), so this is the typed way to say
    so at the call site instead of passing `UUID | None` into code that cannot
    take one.
    """
    if user.tenant_id is None:
        raise Forbidden(
            "Tu usuario todavía no tiene empresa. Crea una o pide que te inviten."
        )
    return user.tenant_id


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
            org_role=payload.get("orl"),
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


def require_platform_admin(user: CurrentUserDep) -> CurrentUser:
    """Quien opera la plataforma. El único rol que existe por encima de una org.

    SE LEE DE LA BASE EN CADA PETICIÓN, A PROPÓSITO
    -----------------------------------------------
    Todos los demás roles viajan dentro del JWT, que es más rápido y está bien
    para ellos: si alguien deja de ser `owner` de una empresa, lo peor que pasa
    es que siga siéndolo hasta que su token caduque, dentro de la empresa donde
    ya estaba.

    Este rol no. Cruza organizaciones, así que un token viejo con el flag dentro
    seguiría abriendo la pantalla de plataforma después de habérselo quitado a
    alguien - y quitárselo a alguien es justo el momento en que más importa que
    deje de funcionar. Una consulta por petición, en una pantalla que se abre
    tres veces al día, es un precio que no se nota.

    LO QUE ESTE ROL NO ABRE
    -----------------------
    Nada de los datos de un comerciante. No aparece en ninguna política de RLS
    (migración 053): aunque este guardia dejara pasar a quien no debe, las guías,
    los movimientos y los compradores siguen fuera de su alcance.
    """
    from api.db import fetch_one

    with connection(service=True) as conn:
        row = fetch_one(
            conn,
            "SELECT is_platform_admin FROM core.app_user WHERE id = %s",
            (user.id,),
        )

    if not row or not row["is_platform_admin"]:
        # El mismo mensaje tanto si la cuenta no existe como si no tiene el rol:
        # la diferencia solo le sirve a quien está probando ids ajenos.
        raise Forbidden(
            "Esta pantalla es para quien opera la plataforma. Si crees que "
            "deberías tener acceso, pídeselo a quien administra el servidor."
        )
    return user


PlatformAdminDep = Annotated[CurrentUser, Depends(require_platform_admin)]


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
            "Tu usuario todavía no tiene empresa. Crea una o pide que te inviten."
        )
    _guard_country(user, request)
    with connection(user.tenant_id) as conn:
        _guard_subscription(conn, user)
        yield conn


def _guard_subscription(conn: psycopg.Connection, user: CurrentUser) -> None:
    """The free month ended, or the plan ran out: every data endpoint is closed.

    Checked here because every data endpoint runs on `DbDep`; the unscoped
    endpoints (login, /auth/me, /billing, the org chart) stay open so the
    person can see what happened and choose a plan. One small query per
    request, on a table outside row-level security.
    """
    if user.org_id is None:
        return
    from api.billing import subscription_state

    state = subscription_state(conn, user.org_id)
    if state.blocked:
        raise PaymentRequired(
            state.message,
            detail={"status": state.status, "trial_ends_at": state.trial_ends_at.isoformat()},
        )


DbDep = Annotated[psycopg.Connection, Depends(db_for_user)]


def db_unscoped() -> Iterator[psycopg.Connection]:
    """For endpoints that run before a tenant exists: login, register, health."""
    with connection() as conn:
        yield conn


UnscopedDbDep = Annotated[psycopg.Connection, Depends(db_unscoped)]


def country_scope_sql(
    user: CurrentUser, column: str = "country_code", *, include_global: bool = False
) -> tuple[str, list]:
    """Un fragmento de SQL que recorta la consulta al alcance de esta persona.

    POR QUÉ EN SQL Y NO FILTRANDO LA LISTA DESPUÉS
    Porque hay paginación. Traer veinte filas y descartar doce deja páginas
    cortas y un `total` que miente: el socio de Guatemala vería "48 cargas" y
    ocho filas. El recorte tiene que ocurrir antes de contar.

    POR QUÉ LAS CONEXIONES GLOBALES DESAPARECEN PARA QUIEN TIENE ALCANCE LIMITADO
    Una conexión global no pertenece a ningún país: el archivo que carga declara
    el suyo, y mezcla varios. Enseñársela a alguien que solo puede leer Guatemala
    sería enseñarle la puerta por la que entran los datos de Colombia. Quien
    necesite verla necesita acceso a toda la sociedad, que es lo que significa
    `country_scope = NULL`. `include_global` existe para el caso contrario: una
    consulta donde la fila global es inofensiva porque el país va en otra parte.

    Devuelve ("", []) cuando la membresía no está limitada, así que el llamador
    concatena sin preguntar.
    """
    if user.countries is None:
        return "", []

    scope = list(user.countries)
    if include_global:
        return f" AND ({column} IS NULL OR upper({column}) = ANY(%s))", [scope]
    return f" AND upper({column}) = ANY(%s)", [scope]


def client_ip(request: Request) -> str:
    """Best-effort caller identity, for rate limiting. Always a string.

    `X-Forwarded-For` is a list the caller may start and every proxy APPENDS
    to. The FIRST entry is therefore whatever the caller typed - a script that
    rotates it gets a fresh rate-limit bucket per request and brute-forces
    `/auth/login` at will. The LAST entry is the one written by the proxy in
    front of this API (Render, a load balancer), which is the only hop that is
    not under the caller's control. With no proxy at all the header is absent
    and the socket peer is used, so local development is unaffected.
    """
    settings = get_settings()

    # The web app's own proxy, authenticated by a shared secret. Without this
    # every browser would share one bucket: the proxy's egress address.
    if settings.proxy_shared_secret:
        presented = request.headers.get("x-proxy-secret") or ""
        claimed = (request.headers.get("x-client-ip") or "").strip()
        if claimed and constant_time_equals(presented, settings.proxy_shared_secret):
            return claimed[:64]

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and settings.trust_proxy_headers:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1][:64]
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
