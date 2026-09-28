"""Conectar Effi con la extensión: el comerciante entra, nosotros solo usamos la sesión.

POR QUÉ EXISTE
--------------
El login de Effi lleva un reCAPTCHA v2 invisible, así que el servidor no puede
entrar con usuario y contraseña (connectors/effi/auth.py). El comerciante sí:
entra a Effi en su navegador, resuelve el captcha, y la extensión
`extension/effi-connector` nos entrega la cookie de sesión. Esto es el puente.

EL FLUJO
--------
1. El dueño pide un código en Configuración → Conexiones
   (`POST /config/effi/connections/{id}/pairing`). Diez minutos, un solo uso,
   atado a UNA conexión. La base guarda solo su SHA-256.
2. Lo pega en la extensión, que llama `POST /config/effi/pairing/redeem` con el
   código, la cookie y el User-Agent del navegador. SIN JWT: el código es toda
   la credencial, como en `/captures/{token}`.
3. Aquí: límite por IP, se valida la cookie, se RECLAMA el código de forma
   atómica (dos canjes simultáneos: gana uno), se guarda la sesión cifrada en
   la bóveda exactamente donde el worker la busca, y se corre el mismo
   «Probar conexión» de siempre. La extensión recibe cómo quedó.
4. La pantalla que generó el código consulta
   `GET /config/effi/connections/{id}/pairing/{pairing_id}` hasta verlo.

LO QUE ESTE ARCHIVO GARANTIZA
-----------------------------
- El canje NO trae id de conexión. La conexión y la empresa salen de la fila
  del código, así que un código de la empresa A no puede escribir en la B, y
  cambiar un id en el JSON no sirve de nada.
- La cookie no se registra, no se devuelve y no se guarda en claro. Ni en el
  log, ni en la respuesta, ni en un error de validación (api/errors.py no
  devuelve el `input`).
- Un código se gasta aunque lo que siga falle. Reintentar con el mismo código
  sería justo la ventana que el "un solo uso" existe para cerrar.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from api import credentials
from api.db import check_rate_limit, connection, execute, fetch_one
from api.deps import CurrentUserDep, DbDep, SettingsDep, client_ip, client_ip_inet, require_role
from api.errors import ApiError, NotFound
from api.preflight import run_preflight_for_connection
from api.routers.config import _connection_for_credential
from api.schemas import (
    EffiCookie,
    EffiPairingCreateRequest,
    EffiPairingRedeemRequest,
    EffiPairingRedeemResponse,
    EffiPairingResponse,
    EffiPairingStatusResponse,
)
from api.security import hash_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config/effi", tags=["effi"])

REDEEM_PATH = "/config/effi/pairing/redeem"

PAIRING_TTL = timedelta(minutes=10)

# Sin 0/O, 1/I/L ni U: se lee en una pantalla y se teclea o se pega. Doce
# caracteres de 30 posibles son ~59 bits; con diez intentos por minuto por IP
# y diez minutos de vida, adivinarlo no es un plan.
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_CODE_LENGTH = 12

# Una cookie de Effi no vive más que esto en nuestra bóveda, diga lo que diga
# su fecha. Si Effi la mantiene viva más tiempo, se vuelve a enviar y ya.
MAX_SESSION_LIFETIME = timedelta(days=30)

NOT_USABLE_CODE = (
    "Ese código no sirve: ya se usó, caducó o no existe. Genera uno nuevo en "
    "Data Effi (Configuración → Conexiones → Effi → Conectar con la extensión)."
)


# =============================================================================
# El dueño pide un código
# =============================================================================


@router.post(
    "/connections/{connection_id}/pairing",
    response_model=EffiPairingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generar un código para conectar Effi con la extensión",
    dependencies=[Depends(require_role("owner"))],
)
def create_pairing(
    connection_id: UUID,
    payload: EffiPairingCreateRequest,
    request: Request,
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
) -> EffiPairingResponse:
    """El código sale UNA vez, aquí. Pedir otro deja muerto el anterior."""
    # 404 si no es de tu empresa o de un país que no ves (mismo guardia que la
    # credencial con contraseña): un id escrito a mano no abre nada.
    row = _connection_for_credential(conn, user, connection_id)

    if row["platform_code"] != "effi":
        raise ApiError(
            "pairing_not_supported",
            f"{row['platform_name']} no se conecta con la extensión de Effi.",
        )
    if not credentials.vault_available():
        raise ApiError(
            "vault_unavailable",
            "Este servidor todavía no tiene bóveda de credenciales configurada, "
            "así que no puede guardar la sesión. Mientras tanto puedes seguir "
            "subiendo el reporte a mano.",
        )
    if not payload.consent_granted:
        raise ApiError(
            "consent_required",
            "Necesitamos tu autorización explícita para usar tu sesión de Effi y "
            "descargar tus propios reportes. Lee qué implica antes de aceptar.",
        )

    # Un código vivo por conexión. El anterior, si alguien lo copió a un chat,
    # deja de servir en este instante.
    execute(
        conn,
        """
        UPDATE core.connection_pairing
           SET revoked_at = now()
         WHERE connection_id = %s AND tenant_id = %s
           AND redeemed_at IS NULL AND revoked_at IS NULL
        """,
        (connection_id, user.tenant_id),
    )

    code = generate_code()
    expires_at = datetime.now(UTC) + PAIRING_TTL
    created = fetch_one(
        conn,
        """
        INSERT INTO core.connection_pairing
            (tenant_id, connection_id, code_hash, created_by, expires_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (user.tenant_id, connection_id, hash_code(code), user.id, expires_at),
    )
    if created is None:  # pragma: no cover - INSERT ... RETURNING
        raise RuntimeError("No se pudo crear el código de emparejamiento")

    # El hecho, no el código.
    logger.info(
        "effi pairing issued tenant=%s connection=%s by=%s pairing=%s",
        user.tenant_id, connection_id, user.id, created["id"],
    )

    return EffiPairingResponse(
        pairing_id=created["id"],
        connection_id=connection_id,
        code=code,
        expires_at=expires_at,
        ttl_seconds=int(PAIRING_TTL.total_seconds()),
        api_url=settings.public_api_url or str(request.base_url).rstrip("/"),
        message=(
            "Pega este código en la extensión «Conectar Effi con Data Effi» con "
            "Effi abierto y la sesión iniciada. Sirve una sola vez y caduca en "
            f"{int(PAIRING_TTL.total_seconds() // 60)} minutos."
        ),
    )


@router.get(
    "/connections/{connection_id}/pairing/{pairing_id}",
    response_model=EffiPairingStatusResponse,
    summary="Cómo va un código de emparejamiento",
    dependencies=[Depends(require_role("owner"))],
)
def pairing_status(
    connection_id: UUID, pairing_id: UUID, conn: DbDep, user: CurrentUserDep
) -> EffiPairingStatusResponse:
    """Lo que consulta la pantalla mientras espera. Nunca incluye la cookie."""
    _connection_for_credential(conn, user, connection_id)
    row = fetch_one(
        conn,
        """
        SELECT p.id, p.connection_id, p.expires_at, p.revoked_at, p.redeemed_at,
               p.outcome, p.outcome_detail, c.credential_status
          FROM core.connection_pairing p
          JOIN core.connection c ON c.id = p.connection_id
         WHERE p.id = %s AND p.connection_id = %s AND p.tenant_id = %s
        """,
        (pairing_id, connection_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Ese código no existe en esta conexión")

    return EffiPairingStatusResponse(
        pairing_id=row["id"],
        connection_id=row["connection_id"],
        state=pairing_state(row),
        credential_status=row["credential_status"],
        summary=row["outcome_detail"],
        expires_at=row["expires_at"],
        redeemed_at=row["redeemed_at"],
    )


# =============================================================================
# La extensión canjea el código — SIN JWT
# =============================================================================


@router.post(
    "/pairing/redeem",
    response_model=EffiPairingRedeemResponse,
    summary="Recibir la sesión de Effi desde la extensión",
)
def redeem_pairing(
    payload: EffiPairingRedeemRequest, request: Request, settings: SettingsDep
) -> EffiPairingRedeemResponse:
    """Guarda la sesión cifrada, la prueba y dice cómo quedó."""
    ip = client_ip(request)

    # 1. El límite va ANTES de mirar el código: probar códigos al azar se queda
    #    sin turno en vez de sin ideas.
    with connection(service=True) as conn:
        allowed = check_rate_limit(
            conn, scope="effi_pairing", subject=ip,
            limit=settings.rate_limit_effi_pairing_per_minute,
        )
    if not allowed:
        raise ApiError(
            "rate_limited",
            "Demasiados intentos seguidos. Espera un minuto y vuelve a intentarlo.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    code = normalize_code(payload.code)
    if code is None:
        raise NotFound(NOT_USABLE_CODE)

    # 2. La cookie se valida ANTES de gastar el código: si faltó la sesión, la
    #    persona entra a Effi y reintenta con el mismo código.
    token, session_expires_at = session_from_cookies(payload.cookies)

    # 3. Reclamar. Atómico: dos canjes simultáneos del mismo código, gana uno.
    #    Se confirma de inmediato - un código usado queda usado aunque lo que
    #    sigue falle.
    code_hash = hash_code(code)
    user_agent = (payload.user_agent or "").strip()[:512] or None
    with connection(service=True) as conn:
        claimed = fetch_one(
            conn,
            """
            UPDATE core.connection_pairing
               SET redeemed_at = now(), redeemed_ip = %s, redeemed_user_agent = %s
             WHERE code_hash = %s
               AND redeemed_at IS NULL
               AND revoked_at IS NULL
               AND expires_at > now()
            RETURNING id, tenant_id, connection_id, created_by, created_at, code_hash
            """,
            (client_ip_inet(request), user_agent, code_hash),
        )
    # La búsqueda es por índice sobre el hash; la comparación en tiempo
    # constante es el cinturón además de los tirantes.
    if claimed is None or not hmac.compare_digest(str(claimed["code_hash"]), code_hash):
        # Nunca se distingue "no existe", "caducó" y "ya se usó": esa diferencia
        # es justo lo que mide un script que prueba códigos.
        logger.info("effi pairing redeem refused ip=%s", ip)
        raise NotFound(NOT_USABLE_CODE)

    tenant_id: UUID = claimed["tenant_id"]
    connection_id: UUID = claimed["connection_id"]

    # 4. Guardar y probar, con la empresa y la conexión DEL CÓDIGO.
    with connection(service=True) as conn:
        target = fetch_one(
            conn,
            """
            SELECT c.id, c.name, c.platform_code, p.name AS platform_name
              FROM core.connection c
              JOIN core.platform p ON p.code = c.platform_code
             WHERE c.id = %s AND c.tenant_id = %s
            """,
            (connection_id, tenant_id),
        )
        if target is None or target["platform_code"] != "effi":  # pragma: no cover - FK + create guard
            raise NotFound(NOT_USABLE_CODE)

        try:
            credentials.store_browser_session(
                conn,
                connection_id=connection_id,
                tenant_id=tenant_id,
                token=token,
                expires_at=session_expires_at,
                user_agent=user_agent,
            )
        except credentials.VaultKeyMissing:
            _record_outcome(conn, claimed["id"], "unverified", "Bóveda no configurada")
            conn.commit()
            raise ApiError(
                "vault_unavailable",
                "Data Effi no pudo guardar la sesión: el servidor no tiene bóveda "
                "configurada. Avísale a soporte.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from None

        # La conexión pasa a sesión, con la autorización que dio el dueño al
        # generar el código, y vuelve al horario si una sesión muerta la sacó.
        execute(
            conn,
            """
            UPDATE core.connection
               SET source_mode        = 'session',
                   consent_granted_at = %s,
                   consent_granted_by = %s,
                   status             = CASE WHEN status IN ('error', 'disabled')
                                             THEN 'active' ELSE status END,
                   last_error         = NULL
             WHERE id = %s AND tenant_id = %s
            """,
            (claimed["created_at"], claimed["created_by"], connection_id, tenant_id),
        )
        conn.commit()

        outcome, credential_status, summary = _probe(
            conn, connection_id, tenant_id, target["platform_name"], claimed["created_at"]
        )
        _record_outcome(conn, claimed["id"], outcome, summary)
        conn.commit()

    logger.info(
        "effi pairing redeemed tenant=%s connection=%s pairing=%s outcome=%s ip=%s",
        tenant_id, connection_id, claimed["id"], outcome, ip,
    )

    return EffiPairingRedeemResponse(
        connected=outcome == "connected",
        credential_status=credential_status,
        connection_name=target["name"],
        summary=summary,
    )


# =============================================================================
# Internos (públicos para los tests)
# =============================================================================


def generate_code() -> str:
    """XXXX-XXXX-XXXX con `secrets`, nunca con `random`."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, _CODE_LENGTH, 4))


def normalize_code(value: str) -> str | None:
    """Mayúsculas, sin guiones ni espacios. None si no puede ser un código nuestro."""
    cleaned = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    if len(cleaned) != _CODE_LENGTH or any(ch not in _ALPHABET for ch in cleaned):
        return None
    return cleaned


def hash_code(code: str) -> str:
    """El hash del código NORMALIZADO: con o sin guiones, el mismo código."""
    normalized = normalize_code(code)
    if normalized is None:
        raise ValueError("Código con formato inválido")
    return hash_token(normalized)


def session_cookie_names() -> tuple[str, ...]:
    """Las cookies que forman la sesión de Effi. Todo lo demás se descarta.

    `EFFI_SESSION_COOKIE` es la misma variable que ya usa el login
    (connectors/effi/auth.py); `EFFI_EXTRA_SESSION_COOKIES` existe por si Effi
    resulta necesitar otra, sin tener que publicar una extensión nueva.
    """
    main = os.environ.get("EFFI_SESSION_COOKIE", "").strip() or "ci_session"
    extra = [
        name.strip()
        for name in os.environ.get("EFFI_EXTRA_SESSION_COOKIES", "").split(",")
        if name.strip()
    ]
    return (main, *extra)


def session_from_cookies(cookies: list[EffiCookie]) -> tuple[str, datetime | None]:
    """El encabezado Cookie que el fetcher va a reenviar, y cuándo vence.

    Mismo formato que produce el login (`nombre=valor`), para que desde aquí
    una sesión de la extensión y una de login sean indistinguibles.
    """
    names = session_cookie_names()
    kept: dict[str, EffiCookie] = {}
    for cookie in cookies:
        if cookie.name in names and cookie.name not in kept:
            kept[cookie.name] = cookie

    if names[0] not in kept:
        raise ApiError(
            "session_cookie_missing",
            "No llegó la sesión de Effi. Entra a Effi en este navegador, "
            "comprueba que ves tu panel y vuelve a enviar con el mismo código.",
        )

    token = "; ".join(f"{name}={kept[name].value}" for name in names if name in kept)

    now = datetime.now(UTC)
    expiries = [
        datetime.fromtimestamp(c.expires_at, UTC)
        for c in kept.values()
        if c.expires_at is not None
    ]
    if not expiries:
        # Cookie de sesión del navegador: no dice cuándo muere. NULL es la
        # respuesta honesta; el worker se entera cuando Effi la rechace.
        return token, None
    expires_at = min(min(expiries), now + MAX_SESSION_LIFETIME)
    if expires_at <= now:
        raise ApiError(
            "session_cookie_expired",
            "La sesión de Effi de este navegador ya venció. Entra de nuevo a Effi "
            "y vuelve a enviar con el mismo código.",
        )
    return token, expires_at


def pairing_state(row: dict[str, Any]) -> str:
    if row["redeemed_at"] is not None:
        return row["outcome"] or "unverified"
    if row["revoked_at"] is not None:
        return "revoked"
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return "expired"
    return "pending"


_OUTCOME_BY_STATUS = {
    "ok": "connected",
    "insufficient_permissions": "insufficient_permissions",
    "session_expired": "session_rejected",
}


def _probe(
    conn, connection_id: UUID, tenant_id: UUID, platform_name: str, consent_at: datetime
) -> tuple[str, str, str]:
    """El mismo «Probar conexión» del botón. Si no se puede, se dice sin romper.

    La sesión ya quedó guardada: aunque Effi no conteste ahora, el worker la
    usará en la próxima sincronización. Por eso un fallo aquí es `unverified`,
    no un error para quien usa la extensión.
    """
    try:
        result = run_preflight_for_connection(
            conn,
            connection_id=connection_id,
            tenant_id=tenant_id,
            platform_code="effi",
            platform_name=platform_name,
            consent_granted_at=consent_at,
        )
    except ApiError as exc:
        conn.rollback()
        return "unverified", "none", (
            "La sesión quedó guardada, pero no se pudo comprobar ahora: "
            f"{exc.message}"
        )
    except Exception as exc:
        conn.rollback()
        # Solo el tipo: el mensaje de una excepción de red puede arrastrar
        # encabezados, y ahí va la cookie.
        logger.warning("effi pairing probe failed: %s", type(exc).__name__)
        return "unverified", "none", (
            "La sesión quedó guardada, pero Effi no respondió a la comprobación. "
            "Data Effi la usará en la próxima sincronización."
        )

    outcome = _OUTCOME_BY_STATUS.get(result.credential_status, "unverified")
    return outcome, result.credential_status, result.summary


def _record_outcome(conn, pairing_id: UUID, outcome: str, detail: str) -> None:
    execute(
        conn,
        "UPDATE core.connection_pairing SET outcome = %s, outcome_detail = %s WHERE id = %s",
        (outcome, (detail or "")[:1000], pairing_id),
    )
