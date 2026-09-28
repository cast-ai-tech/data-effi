"""Password hashing and token handling.

Passwords: argon2id, never reversible, never logged.
Access tokens: short-lived JWTs carrying the tenant and role.
Refresh tokens: long-lived random strings; only their SHA-256 is stored, so a
database leak yields nothing a thief can present as a token.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from api.settings import Settings

# Defaults follow the argon2-cffi recommendation. Raising these is safe;
# lowering them below the library defaults is not.
_hasher = PasswordHasher()

# Cada hash o verificación de argon2 reserva `memory_cost` (64 MB con los
# valores por defecto) mientras dura. Sin tope, una ráfaga de logins - la gente
# entrando a la vez a primera hora, o alguien probando contraseñas desde muchas
# IPs - corre tantas como hilos tenga el servidor: en la prueba de carga,
# cuarenta logins simultáneos llevaron el proceso de 140 MB a 740 MB, en un
# servidor de 512 MB. El semáforo las pone en fila; cada una dura ~50-100 ms,
# así que la fila se vacía rápido. El resto del API no espera por ella.
_hash_slots: threading.BoundedSemaphore | None = None
_hash_slots_lock = threading.Lock()


def _slots() -> threading.BoundedSemaphore:
    global _hash_slots
    if _hash_slots is None:
        with _hash_slots_lock:
            if _hash_slots is None:
                from api.settings import get_settings

                try:
                    size = max(int(get_settings().password_hash_concurrency), 1)
                except (Exception, SystemExit):  # sin configuración (herramientas)
                    size = 2
                _hash_slots = threading.BoundedSemaphore(size)
    return _hash_slots

MIN_PASSWORD_LENGTH = 10


class TokenError(Exception):
    """The presented token is missing, malformed, expired or not ours."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    with _slots():
        return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        with _slots():
            return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than today's default."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def create_access_token(
    settings: Settings,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    email: str,
    role: str,
    org_id: UUID | None = None,
    is_org_admin: bool = False,
    org_role: str | None = None,
    countries: list[str] | None = None,
) -> tuple[str, int]:
    """Return (token, expires_in_seconds).

    The token names ONE company (`tid`) - the one the caller is currently
    standing in - plus the grant that applies there: the role in that company
    and, when the membership is limited, the countries it may read (`cty`).
    Someone who belongs to four companies holds four different tokens over a
    session, one per switch, so a stolen token never widens beyond the company
    it was minted for.

    `tid` is None only for an org admin holding no membership anywhere: they can
    still read the consolidated roll-up, which asks each company in its own
    scoped transaction rather than reading through this token.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id) if tenant_id else None,
        "email": email,
        "role": role,
        "oid": str(org_id) if org_id else None,
        # `adm` is what the previously deployed API reads, and it keeps being
        # emitted for as long as core.app_user.is_org_admin exists (migration
        # 036 explains why it outlives this change). `orl` is the real answer:
        # admin, analyst or viewer over the holding.
        "adm": is_org_admin or org_role == "admin",
        "orl": org_role,
        "cty": countries,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, settings.jwt_access_ttl_minutes * 60


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("El token expiró") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token inválido") from exc

    if payload.get("typ") != "access":
        raise TokenError("Se esperaba un token de acceso")
    return payload


def create_refresh_token() -> tuple[str, str]:
    """Return (token, sha256). Only the hash is ever persisted."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)


def generate_invitation_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def create_webhook_token() -> tuple[str, str]:
    """Return (token, sha256) for an ingestion webhook.

    Same shape as a refresh token and for the same reason: this string is the
    only credential the caller presents, so the database keeps nothing that
    could be replayed against `/ingest/webhook/{token}`.
    """
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def constant_time_equals(left: str, right: str) -> bool:
    """For comparing shared secrets (worker trigger), never for passwords.

    Compared as bytes: `compare_digest` on two `str` raises TypeError as soon as
    one carries a non-ASCII character, and these values come from request
    headers. A header with an `é` in it turned a 401 into a 500.
    """
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
