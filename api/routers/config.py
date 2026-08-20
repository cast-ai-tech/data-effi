"""Workspace configuration: countries, platforms, connections, users.

Note what is NOT here: any endpoint that accepts a credential. Connections store
the NAME of an environment variable (`secret_ref`); the value is put on the
server by whoever administers it. That is the whole point - a compromised API
cannot hand out passwords it never had.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from api.db import fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, require_role
from api.errors import ApiError, Conflict, NotFound
from api.schemas import (
    ActivateCountryRequest,
    ConnectionCreateRequest,
    ConnectionResponse,
    ConnectionUpdateRequest,
    CountryResponse,
    PlatformResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])

OwnerDep = Annotated[object, Depends(require_role("owner"))]
AnalystDep = Annotated[object, Depends(require_role("analyst"))]


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

    row = fetch_one(
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
    query = "SELECT * FROM mart.v_available_platforms"
    params: tuple = ()
    if country:
        query += " WHERE country_code = %s"
        params = (country.upper(),)
    query += " ORDER BY tier, platform_name"

    return [PlatformResponse(**row) for row in fetch_all(conn, query, params)]


@router.get(
    "/connections",
    response_model=list[ConnectionResponse],
    summary="Conexiones y su salud",
)
def list_connections(
    conn: DbDep,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> list[ConnectionResponse]:
    query = "SELECT * FROM mart.v_connection_health"
    params: tuple = ()
    if country:
        query += " WHERE country_code = %s"
        params = (country.upper(),)
    query += " ORDER BY country_code, platform_code"

    return [ConnectionResponse(**row) for row in fetch_all(conn, query, params)]


@router.post(
    "/connections",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una conexión",
)
def create_connection(
    payload: ConnectionCreateRequest, conn: DbDep, user: OwnerDep
) -> ConnectionResponse:
    platform = fetch_one(
        conn,
        "SELECT code, name, tier, requires_consent FROM core.platform "
        "WHERE code = %s AND is_active",
        (payload.platform_code,),
    )
    if platform is None:
        raise NotFound(f"La plataforma '{payload.platform_code}' no existe")

    country_code = payload.country_code.upper()
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

    # Tier 3 is the only place where a UI checkbox becomes a legal record.
    if platform["requires_consent"] and not payload.consent_granted:
        raise ApiError(
            "consent_required",
            f"{platform['name']} es una conexión Tier 3: necesita tu consentimiento "
            f"explícito antes de poder crearse. Lee docs/tier3-politica.md.",
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"docs": "docs/tier3-politica.md", "tier": platform["tier"]},
        )

    store_id = None
    if payload.store_name:
        store = fetch_one(
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
        created = fetch_one(
            conn,
            """
            INSERT INTO core.connection
                (tenant_id, country_code, platform_code, store_id, name, secret_ref,
                 consent_granted_at, consent_granted_by, sync_interval_minutes, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            RETURNING id
            """,
            (
                user.tenant_id, country_code, payload.platform_code, store_id, payload.name,
                payload.secret_ref, consent_at, user.id if consent_at else None,
                payload.sync_interval_minutes,
            ),
        )
    except Exception as exc:
        if "connection_tenant_id_country_code_platform_code_name_key" in str(exc):
            raise Conflict("Ya existe una conexión con ese nombre para ese país y plataforma") from exc
        raise

    logger.info(
        "connection created tenant=%s country=%s platform=%s tier=%s",
        user.tenant_id, country_code, payload.platform_code, platform["tier"],
    )
    return _connection_health(conn, created["id"])


@router.patch(
    "/connections/{connection_id}",
    response_model=ConnectionResponse,
    summary="Editar una conexión",
)
def update_connection(
    connection_id: UUID, payload: ConnectionUpdateRequest, conn: DbDep, user: OwnerDep
) -> ConnectionResponse:
    existing = fetch_one(
        conn,
        "SELECT id FROM core.connection WHERE id = %s AND tenant_id = %s",
        (connection_id, user.tenant_id),
    )
    if existing is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    fetch_one(
        conn,
        """
        UPDATE core.connection SET
            name                  = COALESCE(%s, name),
            secret_ref            = COALESCE(%s, secret_ref),
            status                = COALESCE(%s, status),
            sync_interval_minutes = COALESCE(%s, sync_interval_minutes)
        WHERE id = %s AND tenant_id = %s
        RETURNING id
        """,
        (
            payload.name, payload.secret_ref, payload.status,
            payload.sync_interval_minutes, connection_id, user.tenant_id,
        ),
    )
    return _connection_health(conn, connection_id)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Eliminar una conexión y sus datos",
)
def delete_connection(connection_id: UUID, conn: DbDep, user: OwnerDep) -> None:
    row = fetch_one(
        conn,
        "DELETE FROM core.connection WHERE id = %s AND tenant_id = %s RETURNING id",
        (connection_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Esa conexión no existe en tu workspace")
    logger.info("connection deleted: %s", connection_id)


@router.get("/users", response_model=list[UserResponse], summary="Usuarios del workspace")
def list_users(conn: DbDep, user: CurrentUserDep) -> list[UserResponse]:
    rows = fetch_all(
        conn,
        """
        SELECT u.id, u.email, u.full_name, u.role, u.tenant_id, u.created_at,
               t.name AS tenant_name
        FROM core.app_user u
        JOIN core.tenant t ON t.id = u.tenant_id
        WHERE u.tenant_id = %s AND u.is_active
        ORDER BY u.created_at
        """,
        (user.tenant_id,),
    )
    return [UserResponse(**row) for row in rows]


def _connection_health(conn, connection_id: UUID) -> ConnectionResponse:
    row = fetch_one(
        conn, "SELECT * FROM mart.v_connection_health WHERE connection_id = %s", (connection_id,)
    )
    if row is None:      # pragma: no cover - only if the tenant GUC is unset
        raise NotFound("No se pudo leer la salud de la conexión")
    return ConnectionResponse(**row)
