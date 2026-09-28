"""Dropi por API: conectar una cuenta con su token de integración.

Dropi sigue siendo una plataforma de archivo en el catálogo (migración 061 dice
por qué). Estos endpoints añaden la segunda vía a una conexión Dropi de un país:
el token de integración de la cuenta, cifrado en la misma bóveda que la
contraseña de Effi (api/credentials.py, pipeline/vault.py), y `source_mode =
'api'` para que el job `sync_dropi` la lea.

LAS MISMAS REGLAS QUE EL RESTO DE /config
  - Solo un dueño escribe. Leer el estado basta con poder ver la conexión.
  - Alcance por país: una conexión de un país que no puedes ver no existe para
    ti (404, no 403), sea que la pidas por id o que intentes crearla.
  - El token entra y no vuelve a salir: ninguna respuesta lo incluye.
  - Guardar no prueba. Probar es un botón aparte, y hace UNA consulta de una
    orden. Un token rechazado no se reintenta solo (el job lo salta).

CONVERTIR UNA CONEXIÓN DE ARCHIVO EN VEZ DE CREAR OTRA. Si ya subes el export de
Dropi a una conexión, pegar el token EN ESA conexión es lo correcto: la clave de
una guía es (conexión, número de guía), así que lo que llega por API y lo que
llegó por archivo se fusionan en las mismas guías. Una conexión nueva
duplicaría cada guía que ya estaba cargada.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from api import credentials
from api.db import execute, fetch_one, fetch_required
from api.deps import CurrentUser, CurrentUserDep, DbDep, require_role, tenant_of
from api.errors import ApiError, NotFound
from api.routers.config import _assert_connection_in_scope, create_connection
from api.schemas import (
    ConnectionCreateRequest,
    DropiConnectionCreateRequest,
    DropiConnectionStatus,
    DropiTestResponse,
    DropiTokenRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config/dropi", tags=["config", "dropi"])

OwnerDep = Annotated[CurrentUser, Depends(require_role("owner"))]

PLATFORM_CODE = "dropi"
# The vault stores a username in the clear so a screen can say which account
# is connected. A token has no username; this label says what kind of secret it
# is and nothing about the secret itself.
TOKEN_LABEL = "token de integración"  # noqa: S105 - una etiqueta, no un secreto


def _client_factory(**kwargs: Any) -> Any:
    """Indirection so tests can replace Dropi without patching httpx."""
    from connectors.dropi.client import DropiClient

    return DropiClient(**kwargs)


client_factory: Callable[..., Any] = _client_factory


# -- helpers -------------------------------------------------------------------
def _dropi_connection(conn, user: CurrentUser, connection_id: UUID) -> dict:
    """The connection, in scope, and a Dropi one. Anything else is a 404/400."""
    _assert_connection_in_scope(conn, user, connection_id)
    row = fetch_one(
        conn,
        """
        SELECT id, country_code, platform_code, source_mode, status, credential_status,
               last_sync_at, last_error, name
          FROM core.connection
         WHERE id = %s AND tenant_id = %s
        """,
        (connection_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Esa conexión no existe en tu workspace")
    if row["platform_code"] != PLATFORM_CODE:
        raise ApiError(
            "not_a_dropi_connection",
            "Esta conexión no es de Dropi: el token de integración solo aplica a Dropi.",
        )
    return row


def _require_supported_country(country_code: str | None) -> None:
    from connectors.dropi.client import UnsupportedCountryError, base_url_for

    try:
        base_url_for(country_code)
    except UnsupportedCountryError as exc:
        raise ApiError("dropi_country_unsupported", str(exc)) from None


def _require_vault() -> None:
    if not credentials.vault_available():
        raise ApiError(
            "vault_unavailable",
            "Este servidor todavía no tiene bóveda de credenciales configurada. "
            "Mientras tanto puedes seguir subiendo el reporte de órdenes de Dropi: "
            "produce exactamente el mismo tablero.",
        )


def _store_token(conn, user: CurrentUser, connection_id: UUID, token: str) -> None:
    try:
        credentials.store_credential(
            conn,
            connection_id=connection_id,
            tenant_id=tenant_of(user),
            username=TOKEN_LABEL,
            password=token,
        )
    except ValueError as exc:
        raise ApiError("credential_invalid", str(exc)) from None

    # The connection now receives data by API. Revived from error/disabled for
    # the same reason `put_connection_credential` does it: a merchant who just
    # fixed the token must not be left with a connection that never syncs.
    execute(
        conn,
        """
        UPDATE core.connection
           SET source_mode = 'api',
               status      = CASE WHEN status IN ('error', 'disabled')
                                  THEN 'active' ELSE status END,
               last_error  = NULL
         WHERE id = %s AND tenant_id = %s
        """,
        (connection_id, user.tenant_id),
    )
    # The token itself is never logged; only the fact.
    logger.info("dropi token stored tenant=%s connection=%s", user.tenant_id, connection_id)


def _status(conn, user: CurrentUser, connection_id: UUID, message: str | None = None
            ) -> DropiConnectionStatus:
    row = fetch_required(
        conn,
        """
        SELECT c.id, c.country_code, c.source_mode, c.status, c.credential_status,
               c.last_sync_at, c.last_error,
               cc.connection_id IS NOT NULL AS has_token,
               cc.last_login_at, cc.last_login_error,
               s.synced_through, s.last_orders_seen, s.last_warning
          FROM core.connection c
          LEFT JOIN core.connection_credential cc ON cc.connection_id = c.id
          LEFT JOIN core.connection_sync_state s  ON s.connection_id = c.id
         WHERE c.id = %s AND c.tenant_id = %s
        """,
        (connection_id, user.tenant_id),
    )
    return DropiConnectionStatus(
        connection_id=row["id"],
        country_code=row["country_code"],
        source_mode=row["source_mode"],
        status=row["status"],
        has_token=row["has_token"],
        credential_status=row["credential_status"],
        last_login_at=row["last_login_at"],
        last_login_error=row["last_login_error"],
        last_sync_at=row["last_sync_at"],
        last_error=row["last_error"],
        synced_through=row["synced_through"],
        last_orders_seen=row["last_orders_seen"],
        last_warning=row["last_warning"],
        message=message,
    )


# -- endpoints -----------------------------------------------------------------
@router.post(
    "/connections",
    response_model=DropiConnectionStatus,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una conexión Dropi por API (token de integración)",
)
def create_dropi_connection(
    payload: DropiConnectionCreateRequest, conn: DbDep, user: OwnerDep
) -> DropiConnectionStatus:
    """Same checks as `POST /config/connections` - platform, country in the
    workspace, country in YOUR scope - plus a known API host and a vault.

    If this country already has a Dropi connection fed by files, the answer is
    409 pointing at it: paste the token there instead (see the module header).
    """
    country = payload.country_code.upper()
    _require_supported_country(country)
    _require_vault()

    existing = fetch_one(
        conn,
        """
        SELECT id, name FROM core.connection
         WHERE tenant_id = %s AND platform_code = %s AND country_code = %s
           AND status <> 'disabled'
         ORDER BY created_at LIMIT 1
        """,
        (user.tenant_id, PLATFORM_CODE, country),
    )
    if existing is not None and (
        user.countries is None or country in user.countries
    ):
        raise ApiError(
            "dropi_connection_exists",
            f"Ya tienes la conexión «{existing['name']}» de Dropi en {country}. Pega "
            "el token en esa conexión (Gestionar): así lo que llegue por API se une "
            "a las guías que ya cargaste en vez de duplicarlas.",
            status_code=status.HTTP_409_CONFLICT,
            detail={"connection_id": str(existing["id"])},
        )

    # The generic creator carries every rule about platforms, countries and
    # scope; reusing it keeps one copy of them.
    created = create_connection(
        ConnectionCreateRequest(
            country_code=country,
            platform_code=PLATFORM_CODE,
            name=payload.name or f"Dropi {country} · API",
            store_name=payload.store_name,
        ),
        conn,
        user,
    )
    _store_token(conn, user, created.connection_id, payload.token)
    return _status(
        conn, user, created.connection_id,
        message="Token guardado y cifrado. Pulsa «Probar conexión» para comprobarlo.",
    )


@router.get(
    "/connections/{connection_id}",
    response_model=DropiConnectionStatus,
    summary="Estado de la conexión Dropi por API",
)
def get_dropi_connection(
    connection_id: UUID, conn: DbDep, user: CurrentUserDep
) -> DropiConnectionStatus:
    _dropi_connection(conn, user, connection_id)
    return _status(conn, user, connection_id)


@router.put(
    "/connections/{connection_id}/token",
    response_model=DropiConnectionStatus,
    summary="Guardar o cambiar el token de integración de Dropi",
)
def put_dropi_token(
    connection_id: UUID, payload: DropiTokenRequest, conn: DbDep, user: OwnerDep
) -> DropiConnectionStatus:
    row = _dropi_connection(conn, user, connection_id)
    _require_supported_country(row["country_code"])
    _require_vault()
    _store_token(conn, user, connection_id, payload.token)
    return _status(
        conn, user, connection_id,
        message="Token guardado y cifrado. Pulsa «Probar conexión» para comprobarlo.",
    )


@router.post(
    "/connections/{connection_id}/test",
    response_model=DropiTestResponse,
    summary="Probar el token contra Dropi (una consulta de una orden)",
)
def test_dropi_connection(
    connection_id: UUID, conn: DbDep, user: OwnerDep
) -> DropiTestResponse:
    """One request, one order. Writes down the outcome and never retries a 401.

    The answer is 200 either way: a rejected token is a result the screen shows,
    not a server error, and the status it wrote has to be committed.
    """
    from connectors.dropi.client import DropiAuthError, DropiError
    from pipeline.vault import CredentialUnreadable, VaultKeyMissing

    row = _dropi_connection(conn, user, connection_id)

    try:
        with credentials.use_credential(
            conn, connection_id=connection_id, tenant_id=tenant_of(user)
        ) as credential:
            client = client_factory(token=credential.password, country_code=row["country_code"])
            page = client.test_connection()
            del client
    except LookupError:
        raise ApiError(
            "dropi_token_missing",
            "Esta conexión todavía no tiene token. Pégalo primero en Gestionar.",
        ) from None
    except (CredentialUnreadable, VaultKeyMissing) as exc:
        raise ApiError("vault_unavailable", str(exc)) from None
    except DropiAuthError as exc:
        credentials.record_login_failure(
            conn, connection_id=connection_id, tenant_id=tenant_of(user),
            credential_status="invalid", message=str(exc),
        )
        return DropiTestResponse(
            connection_id=connection_id, ok=False, credential_status="invalid",
            orders_visible=False, message=str(exc),
        )
    except DropiError as exc:
        # Dropi down, rate-limited or answering something unexpected: nothing
        # is known about the token, so its status is left alone.
        return DropiTestResponse(
            connection_id=connection_id, ok=False,
            credential_status=row["credential_status"], orders_visible=False,
            message=str(exc),
        )

    credentials.record_login_ok(conn, connection_id=connection_id, tenant_id=tenant_of(user))
    visible = bool(page.orders)
    return DropiTestResponse(
        connection_id=connection_id,
        ok=True,
        credential_status="ok",
        orders_visible=visible,
        message=(
            "Dropi aceptó el token. La próxima sincronización trae tus órdenes."
            if visible else
            "Dropi aceptó el token, pero la cuenta no devolvió órdenes. Si tienes "
            "órdenes, revisa que el token sea de la cuenta de este país."
        ),
    )


@router.delete(
    "/connections/{connection_id}/token",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Desconectar la API de Dropi (los datos se conservan)",
)
def delete_dropi_token(connection_id: UUID, conn: DbDep, user: OwnerDep) -> Response:
    """Forget the token and go back to receiving files. Guides already loaded stay.

    Deleting the whole connection AND its data is `DELETE /config/connections/{id}`.
    Revoking the token for good is done in Dropi: forgetting it here only stops
    Master Data from using it.
    """
    _dropi_connection(conn, user, connection_id)
    credentials.delete_credential(conn, connection_id=connection_id, tenant_id=tenant_of(user))
    execute(
        conn,
        """
        UPDATE core.connection SET source_mode = 'file', last_error = NULL
         WHERE id = %s AND tenant_id = %s
        """,
        (connection_id, user.tenant_id),
    )
    execute(
        conn,
        "DELETE FROM core.connection_sync_state WHERE connection_id = %s AND tenant_id = %s",
        (connection_id, user.tenant_id),
    )
    logger.info("dropi token removed tenant=%s connection=%s", user.tenant_id, connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
