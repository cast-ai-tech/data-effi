"""Workspace configuration: countries, platforms, connections, users.

Note what is NOT here: any endpoint that accepts a credential. Connections store
the NAME of an environment variable (`secret_ref`); the value is put on the
server by whoever administers it. That is the whole point - a compromised API
cannot hand out passwords it never had.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from api.db import execute, fetch_all, fetch_one, fetch_required
from api.deps import (
    CurrentUser,
    CurrentUserDep,
    DbDep,
    SettingsDep,
    UnscopedDbDep,
    country_scope_sql,
    require_cap,
    require_role,
)
from api.errors import ApiError, Conflict, NotFound
from api.schemas import (
    ActivateCountryRequest,
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionUpdateRequest,
    CountryResponse,
    FxRateRow,
    FxRateUpsertRequest,
    MemberRow,
    PlatformResponse,
    WebhookTokenResponse,
)
from api.security import create_webhook_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])

OwnerDep = Annotated[CurrentUser, Depends(require_role("owner"))]
AnalystDep = Annotated[CurrentUser, Depends(require_role("analyst"))]


@router.get("/countries", response_model=list[CountryResponse], summary="Países disponibles")
def list_countries(conn: DbDep, user: CurrentUserDep) -> list[CountryResponse]:
    """Every supported country, flagged with whether this workspace uses it."""
    rows = fetch_all(
        conn,
        """
        SELECT c.*,
               COALESCE(wc.is_active, false)      AS is_active,
               wc.maturation_days,
               wc.maturation_days_suggested
        FROM core.country c
        LEFT JOIN core.workspace_country wc
               ON wc.country_code = c.code AND wc.tenant_id = %s
        WHERE c.is_supported
        ORDER BY COALESCE(wc.is_active, false) DESC, c.name
        """,
        (user.tenant_id,),
    )
    return [CountryResponse(**row) for row in rows]


@router.put(
    "/countries",
    response_model=CountryResponse,
    summary="Activar o desactivar un país del workspace",
)
def activate_country(
    payload: ActivateCountryRequest, conn: DbDep, user: OwnerDep
) -> CountryResponse:
    country = fetch_one(
        conn, "SELECT code FROM core.country WHERE code = %s AND is_supported",
        (payload.country_code.upper(),),
    )
    if country is None:
        raise NotFound(f"El país '{payload.country_code}' no está soportado")

    fetch_one(
        conn,
        """
        INSERT INTO core.workspace_country (tenant_id, country_code, is_active, maturation_days)
        VALUES (%s, %s, %s, COALESCE(%s, 21))
        ON CONFLICT (tenant_id, country_code) DO UPDATE SET
            is_active       = EXCLUDED.is_active,
            maturation_days = COALESCE(%s, core.workspace_country.maturation_days)
        RETURNING country_code
        """,
        (
            user.tenant_id,
            payload.country_code.upper(),
            payload.is_active,
            payload.maturation_days,
            payload.maturation_days,
        ),
    )

    row = fetch_required(
        conn,
        """
        SELECT c.*, wc.is_active, wc.maturation_days, wc.maturation_days_suggested
        FROM core.country c
        JOIN core.workspace_country wc
          ON wc.country_code = c.code AND wc.tenant_id = %s
        WHERE c.code = %s
        """,
        (user.tenant_id, payload.country_code.upper()),
    )
    return CountryResponse(**row)


@router.get(
    "/platforms",
    response_model=list[PlatformResponse],
    summary="Plataformas disponibles por país",
)
def list_platforms(
    conn: DbDep,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> list[PlatformResponse]:
    """The catalogue, or the subset a given country can actually connect.

    Without a country this answers "what does Master Data integrate with?" and the
    honest answer includes the integrations that do not work yet - each one
    carrying its `availability` and its `setup_hint`, ordered by category so the
    settings screen can group them. With a country it answers the narrower
    "what can I connect for Colombia, and what have I connected already?".
    """
    if country:
        rows = fetch_all(
            conn,
            """
            SELECT * FROM mart.v_available_platforms
            WHERE country_code = %s
            ORDER BY category, tier, platform_name
            """,
            (country.upper(),),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT * FROM mart.v_platform_catalogue ORDER BY category, sort_order, platform_name",
        )

    return [PlatformResponse(**row) for row in rows]


@router.get(
    "/connections",
    response_model=list[ConnectionResponse],
    summary="Conexiones y su salud",
)
def list_connections(
    conn: DbDep,
    user: CurrentUserDep,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    scope: Annotated[Literal["country", "global"] | None, Query()] = None,
) -> list[ConnectionResponse]:
    """Every connection with its health.

    `country` filters to one country's connections; note that a global
    connection has no country and is therefore NOT returned by that filter -
    it belongs to the workspace, not to Colombia. `scope` is how the UI asks
    for one kind or the other.
    """
    clauses: list[str] = []
    params: list = []
    if country:
        clauses.append("country_code = %s")
        params.append(country.upper())
    if scope:
        clauses.append("scope = %s")
        params.append(scope)

    # El alcance por país recorta ANTES de devolver. Sin esto, un socio limitado
    # a Guatemala que no pase `country` recibía las conexiones de toda la
    # sociedad: `_guard_country` solo sabe rechazar un país pedido a propósito.
    scope_sql, scope_params = country_scope_sql(user)
    if scope_sql:
        clauses.append(scope_sql.replace(" AND ", "", 1))
        params.extend(scope_params)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_connection_health {where} "
        "ORDER BY country_code NULLS FIRST, platform_code",
        tuple(params),
    )
    return [ConnectionResponse(**row) for row in rows]


def _assert_connection_in_scope(conn, caller: CurrentUser, connection_id: UUID) -> dict:
    """La conexión existe, es de tu sociedad Y es de un país que puedes ver.

    Leer la lista ya está recortada por alcance, pero un id se puede escribir a
    mano. Sin esto, un socio limitado a Guatemala podía editar, regenerar el
    webhook o BORRAR la conexión de Colombia con sus datos - conociendo solo el
    id, que aparece en cualquier captura de pantalla.

    Devuelve 404 y no 403 a propósito: que exista una conexión en un país que no
    puedes ver no es algo que el código de error deba confirmarte.
    """
    row = fetch_one(
        conn,
        "SELECT id, country_code FROM core.connection WHERE id = %s AND tenant_id = %s",
        (connection_id, caller.tenant_id),
    )
    if row is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    if caller.countries is not None:
        pais = (row["country_code"] or "").upper()
        # Sin país = conexión global, que mezcla varios: fuera del alcance de
        # quien no los puede ver todos.
        if not pais or pais not in caller.countries:
            raise NotFound("Esa conexión no existe en tu workspace")
    return row


@router.post(
    "/connections",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una conexión",
    dependencies=[Depends(require_role("owner"))],
)
def create_connection(
    payload: ConnectionCreateRequest, conn: DbDep, user: CurrentUserDep
) -> ConnectionResponse:
    platform = fetch_one(
        conn,
        "SELECT code, name, tier, requires_consent, scope, availability, auth_type, setup_hint "
        "FROM core.platform WHERE code = %s AND is_active",
        (payload.platform_code,),
    )
    if platform is None:
        raise NotFound(f"La plataforma '{payload.platform_code}' no existe")

    # The database refuses a 'planned' platform too (migration 012). Checking it
    # here first is not redundant: this is the only place that can also name what
    # the integration will need, which is the useful half of the answer.
    if platform["availability"] == "planned":
        hint = platform["setup_hint"] or ""
        raise ApiError(
            "platform_planned",
            f"{platform['name']} todavía no está disponible. Aparece en el catálogo "
            f"para que sepas que viene. {hint}".strip(),
            detail={"setup_hint": platform["setup_hint"], "availability": "planned"},
        )

    country_code = _country_for_scope(payload, platform)

    if user.countries is not None:
        destino = (country_code or "").upper()
        if not destino:
            raise ApiError(
                "forbidden",
                "Una conexión global recibe datos de varios países, y tu usuario "
                f"está limitado a: {', '.join(user.countries)}.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if destino not in user.countries:
            raise ApiError(
                "forbidden",
                f"No puedes crear conexiones en {destino}. Tu usuario tiene acceso "
                f"a: {', '.join(user.countries)}.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    if country_code is not None:
        available = fetch_one(
            conn,
            "SELECT 1 FROM core.platform_country WHERE platform_code = %s AND country_code = %s",
            (payload.platform_code, country_code),
        )
        if available is None:
            raise ApiError(
                "platform_unavailable",
                f"{platform['name']} no opera en {country_code}.",
            )

        activated = fetch_one(
            conn,
            "SELECT 1 FROM core.workspace_country "
            "WHERE tenant_id = %s AND country_code = %s AND is_active",
            (user.tenant_id, country_code),
        )
        if activated is None:
            raise ApiError(
                "country_inactive",
                f"Primero activa {country_code} en tu workspace.",
            )
    else:
        # A global connection declares itself against every active country, so a
        # workspace with none has nowhere to put what arrives.
        any_country = fetch_one(
            conn,
            "SELECT 1 FROM core.workspace_country WHERE tenant_id = %s AND is_active LIMIT 1",
            (user.tenant_id,),
        )
        if any_country is None:
            raise ApiError(
                "country_inactive",
                "Activa al menos un país en tu workspace antes de crear una conexión global.",
            )

    # Tier 3 is the only place where a UI checkbox becomes a legal record.
    if platform["requires_consent"] and not payload.consent_granted:
        raise ApiError(
            "consent_required",
            f"{platform['name']} es una conexión Tier 3: necesita tu consentimiento "
            f"explícito antes de poder crearse. Lee docs/tier3-politica.md.",
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"docs": "docs/tier3-politica.md", "tier": platform["tier"]},
        )

    source_url = _validated_source_url(payload, platform)

    store_id = None
    if payload.store_name:
        if country_code is None:
            raise ApiError(
                "store_needs_country",
                f"{platform['name']} es una conexión global y una tienda pertenece a un "
                "país. Crea la tienda desde una conexión por país.",
            )
        store = fetch_required(
            conn,
            """
            INSERT INTO core.store (tenant_id, country_code, name) VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id, country_code, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (user.tenant_id, country_code, payload.store_name),
        )
        store_id = store["id"]

    consent_at = datetime.now(UTC) if payload.consent_granted else None

    try:
        created = fetch_required(
            conn,
            """
            INSERT INTO core.connection
                (tenant_id, country_code, platform_code, store_id, name, secret_ref,
                 source_url, default_kind, consent_granted_at, consent_granted_by,
                 sync_interval_minutes, status, source_mode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            RETURNING id
            """,
            (
                user.tenant_id, country_code, payload.platform_code, store_id, payload.name,
                payload.secret_ref, source_url, payload.default_kind, consent_at,
                user.id if consent_at else None, payload.sync_interval_minutes,
                # Migration 042: HOW the data arrives. Consent means the worker
                # will replay a session; a sheet URL means it reads a sheet;
                # otherwise someone uploads files.
                "session" if consent_at else ("sheet" if source_url else "file"),
            ),
        )
    except Exception as exc:
        raise _translate_connection_error(exc) from exc

    logger.info(
        "connection created tenant=%s country=%s platform=%s tier=%s scope=%s",
        user.tenant_id, country_code or "-", payload.platform_code,
        platform["tier"], platform["scope"],
    )
    return _connection_health(conn, created["id"])


def _country_for_scope(payload: ConnectionCreateRequest, platform: dict) -> str | None:
    """Reject a country/scope mismatch before the trigger has to.

    Migration 012's `core.enforce_connection_scope` is the real guard - it fires
    however the row arrives, including from psql. This is the same rule stated
    once more, in the one place that can also tell the operator what to do.
    """
    given = payload.country_code.upper() if payload.country_code else None

    if platform["scope"] == "global":
        if given is not None:
            raise ApiError(
                "country_not_allowed",
                f"{platform['name']} es una conexión global: no se ata a un país. "
                "El propio archivo dice de qué país es cada carga.",
                detail={"scope": "global"},
            )
        return None

    if given is None:
        raise ApiError(
            "country_required",
            f"{platform['name']} necesita un país: cada cuenta pertenece a una sola "
            "operación.",
            detail={"scope": "country"},
        )
    return given


def _validated_source_url(payload: ConnectionCreateRequest, platform: dict) -> str | None:
    """A published-sheet URL, checked by the same code the connector uses."""
    if payload.source_url is None:
        return None

    from connectors.sheets.published_csv import InvalidSheetUrlError, validate_published_url

    if platform["code"] != "google_sheets":
        raise ApiError(
            "source_url_not_supported",
            f"{platform['name']} no se configura con una URL. Usa secret_ref con el "
            "NOMBRE de la variable de entorno que guarda la credencial.",
        )
    try:
        return validate_published_url(payload.source_url)
    except InvalidSheetUrlError as exc:
        raise ApiError("invalid_source_url", str(exc)) from exc


def _translate_connection_error(exc: Exception) -> Exception:
    """Turn a constraint or trigger failure into the standard Spanish envelope.

    The trigger raises with ERRCODE check_violation and a message already written
    for a person (migration 012). Letting that reach the generic handler would
    replace a useful sentence with "algo falló de nuestro lado".
    """
    import psycopg

    if isinstance(exc, psycopg.errors.UniqueViolation):
        return Conflict("Ya existe una conexión con ese nombre para esa plataforma")

    if isinstance(exc, psycopg.errors.CheckViolation):
        message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
        if "source_url" in message:
            return ApiError(
                "invalid_source_url",
                "La URL debe ser una hoja de Google publicada en la web "
                "(https://docs.google.com/...).",
            )
        return ApiError("connection_refused", message.strip())

    # Anything unanticipated keeps its original behaviour: the generic handler.
    return exc


@router.patch(
    "/connections/{connection_id}",
    response_model=ConnectionResponse,
    summary="Editar una conexión",
    dependencies=[Depends(require_role("owner"))],
)
def update_connection(
    connection_id: UUID, payload: ConnectionUpdateRequest, conn: DbDep, user: CurrentUserDep
) -> ConnectionResponse:
    _assert_connection_in_scope(conn, user, connection_id)
    existing = fetch_one(
        conn,
        "SELECT id FROM core.connection WHERE id = %s AND tenant_id = %s",
        (connection_id, user.tenant_id),
    )
    if existing is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    source_url = payload.source_url
    if source_url is not None:
        from connectors.sheets.published_csv import InvalidSheetUrlError, validate_published_url

        try:
            source_url = validate_published_url(source_url)
        except InvalidSheetUrlError as exc:
            raise ApiError("invalid_source_url", str(exc)) from exc

    try:
        fetch_one(
            conn,
            """
            UPDATE core.connection SET
                name                  = COALESCE(%s, name),
                secret_ref            = COALESCE(%s, secret_ref),
                source_url            = COALESCE(%s, source_url),
                default_kind          = COALESCE(%s, default_kind),
                status                = COALESCE(%s, status),
                sync_interval_minutes = COALESCE(%s, sync_interval_minutes)
            WHERE id = %s AND tenant_id = %s
            RETURNING id
            """,
            (
                payload.name, payload.secret_ref, source_url, payload.default_kind,
                payload.status, payload.sync_interval_minutes, connection_id, user.tenant_id,
            ),
        )
    except Exception as exc:
        raise _translate_connection_error(exc) from exc

    return _connection_health(conn, connection_id)


# =============================================================================
# Webhook credentials
#
# The token IS the credential for POST /ingest/webhook/{token}: whoever holds it
# can push data into this connection and nothing else. So it is generated here,
# shown once, and stored only as a SHA-256 - exactly like a refresh token. A
# database dump yields hashes, and a hash cannot be POSTed.
# =============================================================================


@router.post(
    "/connections/{connection_id}/webhook",
    response_model=WebhookTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generar (o regenerar) el token del webhook de una conexión",
    dependencies=[Depends(require_role("owner"))],
)
def create_webhook(
    connection_id: UUID,
    request: Request,
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    default_kind: Annotated[
        Literal["shipments", "movements", "ads", "cs"] | None, Query()
    ] = None,
) -> WebhookTokenResponse:
    """Hand back the token once. Regenerating invalidates the previous one."""
    _assert_connection_in_scope(conn, user, connection_id)
    existing = fetch_one(
        conn,
        """
        SELECT c.id, c.default_kind, p.name AS platform_name, p.auth_type
        FROM core.connection c
        JOIN core.platform p ON p.code = c.platform_code
        WHERE c.id = %s AND c.tenant_id = %s
        """,
        (connection_id, user.tenant_id),
    )
    if existing is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    if existing["auth_type"] != "webhook":
        raise ApiError(
            "webhook_not_supported",
            f"{existing['platform_name']} no recibe datos por webhook. Crea una "
            "conexión de tipo Webhook (n8n · Make · Zapier).",
        )

    token, token_hash = create_webhook_token()
    row = fetch_required(
        conn,
        """
        UPDATE core.connection SET
            webhook_token_hash = %s,
            webhook_created_at = now(),
            default_kind       = COALESCE(%s, default_kind)
        WHERE id = %s AND tenant_id = %s
        RETURNING webhook_created_at, default_kind
        """,
        (token_hash, default_kind, connection_id, user.tenant_id),
    )

    # PUBLIC_API_URL, not the request's host. Behind a proxy the two differ, and
    # this URL is shown once - an operator cannot come back for a corrected one.
    url = settings.public_url_for(
        f"/ingest/webhook/{token}", fallback_base=str(request.base_url)
    )
    if settings.public_api_url is None:
        logger.warning(
            "PUBLIC_API_URL is not set; the webhook URL was built from the request "
            "host (%s). Behind a proxy that is the internal address and the "
            "automation will not reach it.",
            request.base_url,
        )
    # The token is deliberately absent from this line. Only the fact is logged.
    logger.info(
        "webhook token issued tenant=%s connection=%s", user.tenant_id, connection_id
    )
    return WebhookTokenResponse(
        connection_id=connection_id,
        token=token,
        url=url,
        default_kind=row["default_kind"],
        created_at=row["webhook_created_at"],
        message=(
            "Copia esta URL en tu automatización (n8n, Make, Zapier). El token se "
            "muestra una sola vez: si lo pierdes, genera uno nuevo y el anterior "
            "deja de funcionar."
        ),
    )


@router.delete(
    "/connections/{connection_id}/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Revocar el webhook de una conexión",
    dependencies=[Depends(require_role("owner"))],
)
def revoke_webhook(
    connection_id: UUID, conn: DbDep, user: CurrentUserDep
) -> None:
    _assert_connection_in_scope(conn, user, connection_id)
    row = fetch_one(
        conn,
        """
        UPDATE core.connection
           SET webhook_token_hash = NULL, webhook_created_at = NULL
         WHERE id = %s AND tenant_id = %s
        RETURNING id
        """,
        (connection_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Esa conexión no existe en tu workspace")
    logger.info("webhook token revoked tenant=%s connection=%s", user.tenant_id, connection_id)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Eliminar una conexión y sus datos",
    dependencies=[Depends(require_role("owner"))],
)
def delete_connection(
    connection_id: UUID, conn: DbDep, user: CurrentUserDep
) -> None:
    _assert_connection_in_scope(conn, user, connection_id)
    row = fetch_one(
        conn,
        "DELETE FROM core.connection WHERE id = %s AND tenant_id = %s RETURNING id",
        (connection_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Esa conexión no existe en tu workspace")
    logger.info("connection deleted: %s", connection_id)


# =============================================================================
# Currency conversion.
#
# WHY THE RATES ARE NOT PER COMPANY
# `core.fx_rate` has no tenant_id and sits outside row-level security on purpose:
# what a quetzal is worth today is not one operator's private fact. Every company
# reads the same table, and the consolidated roll-up converts with it.
#
# WHAT "TRM" MEANS HERE
# Colombia's official TRM is a specific number published by the Superintendencia
# Financiera. This endpoint reports whatever the configured provider returned,
# stamped with its date and its source, and lets an operator overwrite it by
# hand. It does not claim to be the TRM: it says where each number came from
# (`api`, `manual` or `carried_forward`) and how old it is.
# =============================================================================


@router.get(
    "/fx",
    response_model=list[FxRateRow],
    summary="Tasas de cambio contra el dólar y el peso colombiano",
)
def list_fx_rates(conn: DbDep, user: CurrentUserDep) -> list[FxRateRow]:
    rows = fetch_all(conn, "SELECT * FROM mart.v_fx_rates")
    return [FxRateRow(**row) for row in rows]


@router.put(
    "/fx",
    response_model=FxRateRow,
    dependencies=[Depends(require_cap("config"))],
    summary="Fijar una tasa a mano",
)
def upsert_fx_rate(
    payload: FxRateUpsertRequest, conn: DbDep, user: CurrentUserDep
) -> FxRateRow:
    """Writes the rate a person dictates, marked `manual` so it is never mistaken
    for the provider's.

    The request speaks in the direction people quote - how many bolívares to the
    dollar - and this converts to the direction the database stores, which is the
    inverse. Asking an operator to type 0.00127 is asking for a typo nobody would
    catch.
    """
    known = fetch_one(
        conn,
        "SELECT 1 AS ok FROM core.country WHERE currency_code = %s AND is_supported LIMIT 1",
        (payload.currency_code,),
    )
    if known is None:
        raise NotFound(
            f"Ningún país soportado usa la moneda {payload.currency_code}. "
            "Agrega primero el país."
        )

    execute(
        conn,
        """
        INSERT INTO core.fx_rate (rate_date, base_currency, quote_currency, rate, source)
        VALUES (COALESCE(%s, CURRENT_DATE), %s, 'USD', %s, 'manual')
        ON CONFLICT (rate_date, base_currency, quote_currency)
        DO UPDATE SET rate = EXCLUDED.rate, source = 'manual', fetched_at = now()
        """,
        (payload.rate_date, payload.currency_code, 1.0 / payload.per_usd),
    )

    row = fetch_one(
        conn, "SELECT * FROM mart.v_fx_rates WHERE currency_code = %s", (payload.currency_code,)
    )
    if row is None:
        raise NotFound(f"No pude releer la tasa de {payload.currency_code}")
    return FxRateRow(**row)


@router.get("/users", response_model=list[MemberRow], summary="Usuarios de la sociedad")
def list_users(
    conn: UnscopedDbDep,
    user: Annotated[CurrentUser, Depends(require_cap("manage"))],
) -> list[MemberRow]:
    """Who may enter THIS company, with the grant each one holds.

    Reads memberships rather than `core.app_user.tenant_id`: since migration 032
    a person can belong to several companies, and the old query would have shown
    only those whose default company happens to be this one - which, for anyone
    invited into a second partnership, is nobody.

    Unscoped connection on purpose: core.membership and core.app_user sit outside
    row-level security (login has to read them before a company is known), and
    the WHERE below is what scopes this to the caller's company.
    """
    rows = fetch_all(
        conn,
        """
        SELECT u.id AS user_id, u.email, u.full_name, m.role, m.country_scope,
               m.share_pct, m.business_model, m.tenant_id, t.name AS tenant_name,
               m.is_active, u.last_login_at
        FROM core.membership m
        JOIN core.app_user u ON u.id = m.user_id
        JOIN core.tenant t ON t.id = m.tenant_id
        WHERE m.tenant_id = %s AND m.is_active
        ORDER BY u.email
        """,
        (user.tenant_id,),
    )
    return [
        MemberRow(
            user_id=row["user_id"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            country_scope=list(row["country_scope"]) if row["country_scope"] else None,
            share_pct=float(row["share_pct"]) if row["share_pct"] is not None else None,
            business_model=row["business_model"],
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            is_active=row["is_active"],
            last_login_at=row["last_login_at"],
        )
        for row in rows
    ]


def _connection_health(conn, connection_id: UUID) -> ConnectionResponse:
    row = fetch_one(
        conn, "SELECT * FROM mart.v_connection_health WHERE connection_id = %s", (connection_id,)
    )
    if row is None:      # pragma: no cover - only if the tenant GUC is unset
        raise NotFound("No se pudo leer la salud de la conexión")
    return ConnectionResponse(**row)
