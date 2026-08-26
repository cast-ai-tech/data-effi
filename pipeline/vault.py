"""The credential vault: a merchant's platform password, encrypted at rest.

WHY A SECOND KEY AND NOT `PII_ENCRYPTION_KEY`
---------------------------------------------
`pipeline/crypto.py` protects customer contact data. This protects the keys to a
merchant's fulfillment account. They have different blast radii and different
rotation schedules, and a single key would tie them together in the worst way:

  - Rotating the PII key because a laptop was lost would silently invalidate
    every stored Effi password, breaking every sync at once for a reason that
    had nothing to do with them.
  - A read path that can decrypt a phone number would also be a read path that
    can decrypt a password. Two keys means an `ai/` module that touches customer
    data cannot reach a credential even by accident.

So: `PII_ENCRYPTION_KEY` for people, `CONNECTION_VAULT_KEY` for credentials.

WHAT THIS BUYS AND WHAT IT DOES NOT
-----------------------------------
Be honest about the threat model, because a vault that is oversold is worse than
no vault.

  IT DEFENDS AGAINST   a stolen database dump, a leaked backup, a `SELECT *` by
                       a compromised read-only role, a credential leaking
                       through `pg_stat_statements` or a query plan (encryption
                       happens in Python; Postgres only ever sees bytes).

  IT DOES NOT DEFEND   an attacker who owns the application server. They have
  AGAINST              the key and the database at once. Nothing storable can
                       survive that, which is why `secret_ref` - the env-var
                       path from migration 001 - is still supported and still
                       preferred wherever an operator is willing to maintain it.

THE RULES
---------
1. A plaintext credential exists only as a local variable, only for as long as a
   login takes. It is never returned by an API, never logged, never put in an
   exception message, never written to a column.
2. Decryption failure is NOT a crash. A key rotation leaves rows that cannot be
   read; the correct behaviour is "this connection needs re-authorising", not a
   500 on the connections page.
3. `Credential` refuses to render itself. `repr()` and `str()` show asterisks,
   so a traceback or a `logger.info("%s", cred)` cannot leak the password.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

ENV_KEY = "CONNECTION_VAULT_KEY"

# A session is treated as stale a little before the platform would expire it.
# Refreshing early costs one cheap login; refreshing late costs a failed sync
# and a merchant looking at an error they did nothing to cause.
SESSION_REFRESH_MARGIN = timedelta(minutes=10)


class VaultKeyMissing(RuntimeError):
    """No vault key configured. Storing or reading a credential must fail loudly."""


class CredentialUnreadable(RuntimeError):
    """The stored ciphertext will not decrypt - almost always a rotated key.

    Distinct from VaultKeyMissing on purpose: one means "the server is
    misconfigured", the other means "this merchant must re-enter their password".
    They lead to different screens.
    """


@lru_cache(maxsize=1)
def _fernet():
    """Build the cipher once; key derivation is not free and this runs per row."""
    from cryptography.fernet import Fernet

    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise VaultKeyMissing(
            f"Falta la variable de entorno {ENV_KEY}. Sin ella nadie puede "
            "conectar su cuenta de una plataforma. Genera una con: "
            "python -m scripts.generate_vault_key"
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:  # wrong length, not base64, truncated by a copy-paste
        raise VaultKeyMissing(
            f"{ENV_KEY} no es una llave válida de Fernet (32 bytes en base64 "
            "urlsafe). Genera una con: python -m scripts.generate_vault_key"
        ) from exc


def vault_available() -> bool:
    """True when credentials can be stored and read.

    Lets the API answer "la bóveda no está configurada en este servidor" instead
    of throwing a 500 at a merchant who is only trying to open a settings page.
    """
    try:
        _fernet()
    except VaultKeyMissing:
        return False
    return True


# -- the secret itself ------------------------------------------------------
@dataclass(slots=True)
class Credential:
    """A username and its password, in memory, for the length of one login.

    Never store this. Never return it from a route. Never put it in a dict that
    something might serialise. It exists so a connector can call `.password` at
    the exact moment it builds a login request, and for nothing else.
    """

    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        self.username = self.username.strip()
        if not self.username:
            raise ValueError("El usuario no puede estar vacío")
        if not self.password:
            raise ValueError("La contraseña no puede estar vacía")

    # Both are overridden: `repr` covers tracebacks and `%r`, `str` covers `%s`
    # and f-strings. Leaving either one alone leaves a way for the password to
    # reach a log file.
    def __repr__(self) -> str:
        return f"<Credential username={self.username!r} password=***>"

    def __str__(self) -> str:
        return self.__repr__()


def encrypt_secret(value: str) -> bytes:
    """Encrypt a password or a session token for storage.

    Blank is rejected rather than encrypted: an empty secret in the vault would
    read as "connected" on every screen and fail on every sync.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("No se cifra un secreto vacío")
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt_secret(blob: bytes | memoryview | None) -> str | None:
    """Decrypt a stored secret, or None when there is nothing readable.

    Raises CredentialUnreadable rather than returning None on a bad token: a
    password that silently reads as absent would look identical to a connection
    nobody ever configured, and the merchant would be told to "conecta tu
    cuenta" when what actually happened is that the server rotated its key.
    """
    if blob is None:
        return None
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken as exc:
        logger.warning("credencial ilegible: llave rotada o ciphertext alterado")
        raise CredentialUnreadable(
            "La credencial guardada no se puede leer con la llave actual del "
            "servidor. El comerciante debe volver a ingresar su usuario y "
            "contraseña."
        ) from exc


def session_is_fresh(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    """True when a stored session can still be used without logging in again.

    A session with no known expiry is treated as stale. Guessing "it is probably
    still good" trades one cheap login for a failed sync, and the sync is the
    thing the merchant sees.
    """
    if expires_at is None:
        return False
    moment = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at - SESSION_REFRESH_MARGIN > moment


def generate_key() -> str:
    """A fresh vault key, for `scripts.generate_vault_key` and for tests."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()
