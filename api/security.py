"""Password hashing and token handling.

Passwords: argon2id, never reversible, never logged.
Access tokens: short-lived JWTs carrying the tenant and role.
Refresh tokens: long-lived random strings; only their SHA-256 is stored, so a
database leak yields nothing a thief can present as a token.
"""

from __future__ import annotations

import hashlib
import secrets
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

MIN_PASSWORD_LENGTH = 10


class TokenError(Exception):
    """The presented token is missing, malformed, expired or not ours."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
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
    tenant_id: UUID,
    email: str,
    role: str,
) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "email": email,
        "role": role,
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


def constant_time_equals(left: str, right: str) -> bool:
    """For comparing shared secrets (worker trigger), never for passwords."""
    return secrets.compare_digest(left, right)
