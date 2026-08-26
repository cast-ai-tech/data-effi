"""El buzón de capturas: que el contrato de login llegue solo.

QUÉ ES ESTO
-----------
La extensión de `tools/effi-capture` deduce cómo entra un navegador a Effi sin
leer una sola contraseña. Antes terminaba con "copia este texto y pégalo en el
chat"; ahora lo manda aquí, y aparece en la app fechado y ordenado.

POR QUÉ ESTE ENDPOINT PUEDE SER PÚBLICO
---------------------------------------
Igual que `/ingest/webhook/{token}`: el código de la ruta ES la credencial, y no
hay JWT porque quien captura no tiene cuenta en Master Data ni debe tenerla. Es una
persona haciéndonos un favor desde su casa.

Lo que viaja no es secreto: rutas y NOMBRES de campos, lo mismo que cualquiera ve
abriendo Effi con F12. Si esta tabla se filtrara entera, lo que se sabría es cómo
se llama el campo de usuario de Effi.

LAS TRES DEFENSAS, EN ORDEN
---------------------------
1. EL CÓDIGO. Caduca, se revoca, tiene usos contados, y solo se guarda su
   SHA-256. Un volcado de la base no entrega nada reutilizable.
2. EL LÍMITE POR IP. Un escáner que pruebe códigos al azar se queda sin turno
   antes de acertar, y no llena `raw.job_run` mientras lo intenta.
3. EL TRIGGER. `core.reject_capture_secrets` rechaza en el motor cualquier
   contrato que traiga algo con pinta de credencial. La extensión promete no
   mandar valores; el servidor lo comprueba, porque el cliente es un .zip que
   cualquiera puede editar antes de instalar.

UNA COSA QUE ESTE ARCHIVO NO HACE
---------------------------------
No aplica nada de lo que recibe. Una captura entra, se guarda y avisa a un
humano. Que un `POST` de fuera pudiera reconfigurar por sí solo cómo entramos a
la cuenta de un comerciante sería exactamente el agujero que este diseño evita.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, status

from api.db import check_rate_limit, connection, execute, fetch_all, fetch_one, fetch_required
from api.deps import (
    CurrentUserDep,
    DbDep,
    PlatformAdminDep,
    SettingsDep,
    client_ip,
    require_platform_admin,
)
from api.errors import ApiError, NotFound
from api.schemas import (
    CaptureInboxRow,
    CaptureReceivedResponse,
    CaptureSubmission,
    CaptureTokenResponse,
    PlatformOrgRow,
)
from api.security import create_webhook_token, hash_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/captures", tags=["captures"])

# Dos semanas: de sobra para coordinar con alguien por WhatsApp, poco para que
# un código olvidado siga vivo dentro de un .zip en la carpeta de descargas de
# medio mundo.
DEFAULT_TTL_DAYS = 14


# =============================================================================
# Lo que hace quien invita (dueño del espacio)
# =============================================================================


@router.post(
    "/tokens",
    response_model=CaptureTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un código para que alguien mande su captura",
    dependencies=[Depends(require_platform_admin)],
)
def create_capture_token(
    request: Request,
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    label: str,
    platform_code: str = "effi",
    max_uses: int = 10,
) -> CaptureTokenResponse:
    """Devuelve el código UNA vez. La base guarda solo su hash.

    `label` es para reconocer de quién es cuando lleguen tres el mismo día:
    "Juan, de Distrilatam". No se le muestra a quien captura.
    """
    etiqueta = (label or "").strip()
    if not etiqueta:
        raise ApiError(
            "label_required",
            "Ponle un nombre al código para reconocer de quién es la captura "
            "cuando llegue (por ejemplo: «Juan, de Distrilatam»).",
        )

    if not 1 <= max_uses <= 50:
        raise ApiError("invalid_max_uses", "Los usos deben estar entre 1 y 50.")

    plataforma = fetch_one(
        conn, "SELECT code, name FROM core.platform WHERE code = %s", (platform_code,)
    )
    if plataforma is None:
        raise NotFound("Esa plataforma no existe en el catálogo")

    token, token_hash = create_webhook_token()
    expires_at = datetime.now(UTC) + timedelta(days=DEFAULT_TTL_DAYS)

    row = fetch_required(
        conn,
        """
        INSERT INTO core.capture_token
            (tenant_id, token_hash, label, platform_code, max_uses, expires_at, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
        """,
        (user.tenant_id, token_hash, etiqueta, platform_code, max_uses, expires_at, user.id),
    )

    # El código NO se registra. Solo el hecho de que existe uno.
    logger.info(
        "capture token issued tenant=%s platform=%s label=%s",
        user.tenant_id, platform_code, etiqueta,
    )

    return CaptureTokenResponse(
        token_id=row["id"],
        token=token,
        label=etiqueta,
        platform_code=platform_code,
        platform_name=plataforma["name"],
        submit_url=settings.public_url_for(
            f"/captures/{token}", fallback_base=str(request.base_url)
        ),
        max_uses=max_uses,
        expires_at=expires_at,
        created_at=row["created_at"],
        message=(
            "Este código se muestra una sola vez. Genera el paquete con "
            "«python -m scripts.empaquetar_captura --codigo <código>» y envíalo. "
            f"Caduca en {DEFAULT_TTL_DAYS} días."
        ),
    )


@router.get(
    "",
    response_model=list[CaptureInboxRow],
    summary="Las capturas que han llegado",
    dependencies=[Depends(require_platform_admin)],
)
def list_captures(
    conn: DbDep, _user: PlatformAdminDep, only_new: bool = False
) -> list[CaptureInboxRow]:
    """Lo recibido, lo más útil primero: con login antes que sin login.

    SIN FILTRO POR EMPRESA, y ese es el arreglo (migración 053). Una captura
    describe cómo funciona Effi, no los datos de un comerciante: es la misma
    para todos. Filtrarla por el tenant de quien pregunta la encerraba en la
    empresa del cliente que la pidió, donde no la ve quien tiene que cablear
    el conector.
    """
    rows = fetch_all(
        conn,
        """
        SELECT id, platform_code, platform_name, invited_label, source,
               found_login, export_count, contract, base_url, login_path,
               created_at, reviewed_at, is_new
        FROM mart.v_capture_inbox
        WHERE (NOT %s OR reviewed_at IS NULL)
        ORDER BY found_login DESC, created_at DESC
        LIMIT 100
        """,
        (only_new,),
    )
    return [CaptureInboxRow(**row) for row in rows]


@router.post(
    "/{capture_id}/reviewed",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Marcar una captura como revisada",
    dependencies=[Depends(require_platform_admin)],
)
def mark_reviewed(capture_id: int, conn: DbDep, _user: PlatformAdminDep) -> None:
    execute(
        conn, "UPDATE raw.capture SET reviewed_at = now() WHERE id = %s", (capture_id,)
    )


# =============================================================================
# Lo que hace quien captura — SIN cuenta, SIN JWT
#
# El código de la ruta es toda la credencial. Mismo trato que
# /ingest/webhook/{token}, y por el mismo motivo: del otro lado hay alguien que
# no tiene usuario en Master Data y no debería necesitarlo para hacernos un favor.
# =============================================================================


@router.post(
    "/{token}",
    response_model=CaptureReceivedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recibir una captura desde la extensión",
)
def submit_capture(
    token: str, payload: CaptureSubmission, request: Request, settings: SettingsDep
) -> CaptureReceivedResponse:
    """Guarda el contrato y avisa. No aplica nada: eso lo decide una persona."""
    ip = client_ip(request)

    # El límite por IP va ANTES de mirar el código, para que probar códigos al
    # azar se quede sin turno en vez de sin ideas.
    with connection(service=True) as conn:
        permitido = check_rate_limit(
            conn, scope="capture", subject=ip,
            limit=settings.rate_limit_ingest_per_minute,
        )
    if not permitido:
        raise ApiError(
            "rate_limited",
            "Demasiados envíos seguidos. Espera un minuto y vuelve a intentarlo.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    invitacion = _valid_token(token)
    if invitacion is None:
        # Nunca se distingue entre "no existe", "caducó" y "se agotó": esa
        # diferencia es justo lo que mide un script que prueba códigos.
        raise NotFound("Ese código no sirve. Pídele uno nuevo a quien te lo envió.")

    contrato = payload.contract_for_storage()
    encontro_login = bool(contrato.get("ruta"))
    exportaciones = len(contrato.get("exportaciones") or [])

    with connection(service=True) as conn:
        try:
            execute(
                conn,
                """
                INSERT INTO raw.capture
                    (tenant_id, token_id, platform_code, contract, source,
                     found_login, export_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    invitacion["tenant_id"], invitacion["id"],
                    invitacion["platform_code"], _as_json(contrato),
                    payload.source, encontro_login, exportaciones,
                ),
            )
        except Exception as exc:
            # El trigger del motor rechazó algo. Es la última defensa y que
            # salte significa que el cliente mandó de más: se registra fuerte.
            logger.error(
                "captura rechazada por el motor tenant=%s: %s",
                invitacion["tenant_id"], type(exc).__name__,
            )
            raise ApiError(
                "capture_refused",
                "El servidor rechazó esa captura porque traía datos que no debe "
                "llevar. Avísale a quien te envió el código.",
            ) from None

        execute(
            conn,
            "UPDATE core.capture_token SET uses = uses + 1 WHERE id = %s",
            (invitacion["id"],),
        )
        conn.commit()
        _notify(conn, invitacion, encontro_login, exportaciones)

    logger.info(
        "captura recibida tenant=%s login=%s exports=%d",
        invitacion["tenant_id"], encontro_login, exportaciones,
    )

    # El mensaje de vuelta es lo ÚNICO que ve quien captura. Tiene que decirle
    # si sirvió, porque todavía tiene Effi abierto y puede repetir ahora mismo.
    if not encontro_login:
        mensaje = (
            "Recibido, pero la captura no incluye el momento de entrar. Cierra "
            "sesión en Effi y graba otra vez desde la pantalla de entrar."
        )
    elif exportaciones == 0:
        mensaje = (
            "¡Recibido! Se capturó la entrada. Si puedes, graba una segunda vez "
            "exportando Guías, Novedades y Trazabilidad de dinero."
        )
    else:
        mensaje = (
            f"¡Recibido y completo! Se capturó la entrada y {exportaciones} "
            f"{'descarga' if exportaciones == 1 else 'descargas'}. Ya puedes cerrar."
        )

    return CaptureReceivedResponse(
        received=True,
        found_login=encontro_login,
        export_count=exportaciones,
        usable=encontro_login,
        message=mensaje,
    )


# =============================================================================
# Internos
# =============================================================================


def _valid_token(token: str) -> dict[str, Any] | None:
    """La invitación, si el código sirve. Servicio: aún no se sabe el tenant."""
    if not token or len(token) > 200:
        return None

    with connection(service=True) as conn:
        return fetch_one(
            conn,
            """
            SELECT id, tenant_id, platform_code, label, max_uses, uses
              FROM core.capture_token
             WHERE token_hash = %s
               AND revoked_at IS NULL
               AND expires_at > now()
               AND uses < max_uses
            """,
            (hash_token(token),),
        )


def _as_json(value: dict[str, Any]):
    from psycopg.types.json import Json

    return Json(value)


def _notify(conn, invitacion: dict[str, Any], encontro_login: bool, exportaciones: int) -> None:
    """Avisar en la campana. Una captura que nadie mira no sirvió de nada.

    Nunca tumba el envío: quien capturó ya hizo su parte y no tiene por qué ver
    un error porque a nosotros nos falló una notificación.
    """
    from ai.alerts import persist_findings

    etiqueta = invitacion.get("label") or "alguien"
    if encontro_login:
        titulo = f"Llegó la captura de Effi de {etiqueta}"
        hallazgo = (
            f"Se capturó cómo entra el navegador a Effi, con {exportaciones} "
            f"{'descarga' if exportaciones == 1 else 'descargas'} de reportes."
            if exportaciones
            else "Se capturó cómo entra el navegador a Effi, sin descargas de reportes."
        )
        accion = (
            "Revísala en Conexiones → Capturas. Si está completa, pasa las líneas "
            "EFFI_* al .env del servidor."
        )
    else:
        titulo = f"Captura incompleta de {etiqueta}"
        hallazgo = (
            "Llegó una captura pero no incluye el momento de entrar a Effi, que "
            "es la parte que hace falta."
        )
        accion = "Pídele que cierre sesión en Effi y grabe otra vez desde el principio."

    try:
        persist_findings(
            conn,
            invitacion["tenant_id"],
            None,
            [{
                "code": "capture_received",
                "severity": "info" if encontro_login else "warning",
                "title": titulo,
                "finding": hallazgo,
                "action": accion,
                "deep_link": "/plataforma",
            }],
            # Sin ventana de deduplicación: dos capturas seguidas son dos
            # noticias distintas, no la misma repetida.
            dedup_days=0,
        )
        conn.commit()
    except Exception:
        logger.exception("no se pudo notificar la captura recibida")


# =============================================================================
# La otra mitad de operar la plataforma: quién existe y cómo está
#
# Vive en este router y no en uno propio porque es la misma pantalla y el mismo
# guardia. Si algún día crece - facturación, métricas de uso, soporte - merecerá
# su archivo; hoy sería una carpeta con un endpoint dentro.
# =============================================================================


@router.get(
    "/orgs",
    response_model=list[PlatformOrgRow],
    summary="Las organizaciones de la plataforma, con su plan y su salud",
    dependencies=[Depends(require_platform_admin)],
)
def list_platform_orgs(conn: DbDep, _user: PlatformAdminDep) -> list[PlatformOrgRow]:
    """Quién existe, en qué plan está y si algo se le rompió.

    DELIBERADAMENTE SIN CIFRAS DE NEGOCIO. Ni ventas, ni guías, ni plata. Esta
    pantalla existe para saber a quién cobrarle y a quién se le cayó una
    conexión; para todo lo demás está el tablero de cada empresa, que es de su
    dueño y no de quien opera la plataforma.

    La vista `mart.v_platform_orgs` es lo que impone ese límite: no selecciona
    una sola columna de `core.shipment` ni de `core.movement`.
    """
    rows = fetch_all(
        conn,
        """
        SELECT org_id, org_name, slug, created_at, subscription_status,
               plan_code, plan_name, trial_ends_at, current_period_end,
               tenant_count, user_count, connection_count, connection_errors
        FROM mart.v_platform_orgs
        ORDER BY created_at DESC
        LIMIT 500
        """,
    )
    return [PlatformOrgRow(**row) for row in rows]
