"""The vault, from the API's side: store, read, and never hand back a password.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
----------------------------------------
Credentials go IN through this module and never come back OUT to an HTTP client.
There is no endpoint, no response model and no debug flag that returns a stored
password - not to the merchant who typed it, not to an owner, not to support.

That is stricter than it sounds necessary, and it is deliberate. "Let the owner
see their own password" is how a credential ends up in a browser cache, a
screenshot in a WhatsApp group, a support transcript, and a bug report. A
merchant who forgot their Effi password recovers it from Effi, which is the only
system that should ever have been able to tell them.

What DOES come back is the username and a status word. That is enough to answer
the only questions a settings screen has: which account is connected, and is it
working.

WHERE THE PLAINTEXT LIVES
-------------------------
Inside `use_credential()`, for the duration of one `with` block, and nowhere
else. The password is decrypted on entry and the reference is dropped on exit.
It is never assigned to a field on a long-lived object, never returned, never
placed in a dict that something else might serialise.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from api.db import execute, fetch_one
from pipeline.vault import (
    Credential,
    CredentialUnreadable,
    VaultKeyMissing,
    decrypt_secret,
    encrypt_secret,
    vault_available,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CredentialSummary",
    "StoredSession",
    "clear_credential",
    "delete_credential",
    "load_session",
    "read_summary",
    "record_login_failure",
    "save_session",
    "store_credential",
    "use_credential",
    "vault_available",
]


@dataclass(slots=True)
class CredentialSummary:
    """What a screen is allowed to know about a stored credential."""

    username: str
    credential_status: str
    last_login_at: datetime | None
    last_login_error: str | None
    session_expires_at: datetime | None
    rotated_at: datetime


@dataclass(slots=True)
class StoredSession:
    """A session read back out of the vault, if there is one."""

    token: str | None
    expires_at: datetime | None


# -- writing ----------------------------------------------------------------
def store_credential(
    conn,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    username: str,
    password: str,
) -> CredentialSummary:
    """Encrypt and store one merchant's platform login.

    Storing a NEW password always discards the stored session. The old session
    may still be technically valid, but keeping it would mean a merchant who
    changed their password because it leaked would find the leaked session still
    working here - which is the opposite of what they just did.
    """
    if not vault_available():
        raise VaultKeyMissing(
            "Este servidor no tiene bóveda de credenciales configurada "
            "(CONNECTION_VAULT_KEY). Mientras tanto se puede seguir subiendo el "
            "reporte a mano."
        )

    # `Credential` validates before anything is written: a blank username or
    # password must fail here, not become a row that fails on every sync.
    credential = Credential(username=username, password=password)
    secret = encrypt_secret(credential.password)

    execute(
        conn,
        """
        INSERT INTO core.connection_credential
            (connection_id, tenant_id, username, secret_enc, rotated_at, updated_at)
        VALUES (%s, %s, %s, %s, now(), now())
        ON CONFLICT (connection_id) DO UPDATE SET
            username           = EXCLUDED.username,
            secret_enc         = EXCLUDED.secret_enc,
            -- A new password invalidates the old session, on purpose.
            session_enc        = NULL,
            session_expires_at = NULL,
            last_login_error   = NULL,
            rotated_at         = now(),
            updated_at         = now()
        """,
        (connection_id, tenant_id, credential.username, secret),
    )

    # Back to 'none': the credential is stored but unproven. It becomes 'ok'
    # when a login actually succeeds, never because someone typed something.
    execute(
        conn,
        """
        UPDATE core.connection
           SET credential_status = 'none', last_error = NULL
         WHERE id = %s AND tenant_id = %s
        """,
        (connection_id, tenant_id),
    )

    # The username is logged; it is not a secret and it is what makes an audit
    # trail useful. The password is not, and never appears in this module's logs.
    logger.info(
        "credential stored tenant=%s connection=%s user=%s",
        tenant_id, connection_id, credential.username,
    )

    summary = read_summary(conn, connection_id=connection_id, tenant_id=tenant_id)
    if summary is None:  # pragma: no cover - we just wrote it
        raise RuntimeError("La credencial se guardó pero no se pudo leer de vuelta")
    return summary


def save_session(
    conn,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    token: str,
    expires_at: datetime,
) -> None:
    """Persist a session so the next sync does not have to log in again.

    This is the whole reason logins stay rare. A merchant syncing twice a day
    against a twelve-hour session logs in about once a day, not four times.
    """
    execute(
        conn,
        """
        UPDATE core.connection_credential
           SET session_enc        = %s,
               session_expires_at = %s,
               last_login_at      = now(),
               last_login_error   = NULL,
               updated_at         = now()
         WHERE connection_id = %s AND tenant_id = %s
        """,
        (encrypt_secret(token), expires_at, connection_id, tenant_id),
    )
    execute(
        conn,
        "UPDATE core.connection SET credential_status = 'ok' WHERE id = %s AND tenant_id = %s",
        (connection_id, tenant_id),
    )


def record_login_failure(
    conn,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    credential_status: str,
    message: str,
) -> None:
    """Write down why the last login failed, in words the merchant can act on.

    `message` must be something a person can read and fix. Never a raw response
    body: those carry cookies and tokens, and this column is not encrypted.
    """
    execute(
        conn,
        """
        UPDATE core.connection_credential
           SET last_login_error = %s,
               -- A failed login means the stored session, if any, is worthless.
               session_enc        = NULL,
               session_expires_at = NULL,
               updated_at         = now()
         WHERE connection_id = %s AND tenant_id = %s
        """,
        (message[:500], connection_id, tenant_id),
    )
    execute(
        conn,
        "UPDATE core.connection SET credential_status = %s WHERE id = %s AND tenant_id = %s",
        (credential_status, connection_id, tenant_id),
    )
    logger.warning(
        "login failed tenant=%s connection=%s status=%s",
        tenant_id, connection_id, credential_status,
    )


def clear_credential(conn, *, connection_id: UUID, tenant_id: UUID) -> None:
    """Forget the stored session but keep the username and password.

    Used when a session is rejected: the credential may still be perfectly good,
    and throwing it away would make the merchant retype a password that was
    never the problem.
    """
    execute(
        conn,
        """
        UPDATE core.connection_credential
           SET session_enc = NULL, session_expires_at = NULL, updated_at = now()
         WHERE connection_id = %s AND tenant_id = %s
        """,
        (connection_id, tenant_id),
    )


def delete_credential(conn, *, connection_id: UUID, tenant_id: UUID) -> None:
    """Remove the credential entirely - the merchant disconnected the account."""
    execute(
        conn,
        "DELETE FROM core.connection_credential WHERE connection_id = %s AND tenant_id = %s",
        (connection_id, tenant_id),
    )
    execute(
        conn,
        "UPDATE core.connection SET credential_status = 'none' WHERE id = %s AND tenant_id = %s",
        (connection_id, tenant_id),
    )
    logger.info("credential deleted tenant=%s connection=%s", tenant_id, connection_id)


# -- reading ----------------------------------------------------------------
def read_summary(conn, *, connection_id: UUID, tenant_id: UUID) -> CredentialSummary | None:
    """What the settings screen may see. Notice `secret_enc` is not selected.

    Not selecting it is not decoration: a column that is never fetched cannot be
    accidentally logged by a middleware that dumps a row, cannot appear in a
    Sentry breadcrumb, and cannot be widened into a response model by someone
    adding a field in six months.
    """
    row = fetch_one(
        conn,
        """
        SELECT cc.username, cc.last_login_at, cc.last_login_error,
               cc.session_expires_at, cc.rotated_at, c.credential_status
          FROM core.connection_credential cc
          JOIN core.connection c ON c.id = cc.connection_id
         WHERE cc.connection_id = %s AND cc.tenant_id = %s
        """,
        (connection_id, tenant_id),
    )
    if row is None:
        return None
    return CredentialSummary(
        username=row["username"],
        credential_status=row["credential_status"],
        last_login_at=row["last_login_at"],
        last_login_error=row["last_login_error"],
        session_expires_at=row["session_expires_at"],
        rotated_at=row["rotated_at"],
    )


def load_session(conn, *, connection_id: UUID, tenant_id: UUID) -> StoredSession:
    """Read the stored session, if it is there and still readable.

    An unreadable session - a rotated key - is treated as no session at all
    rather than an error. The credential itself may still decrypt, in which case
    the connector simply logs in again and nobody notices. Only when the
    PASSWORD is unreadable does the merchant have to do something, and that is
    raised by `use_credential`, not here.
    """
    row = fetch_one(
        conn,
        """
        SELECT session_enc, session_expires_at
          FROM core.connection_credential
         WHERE connection_id = %s AND tenant_id = %s
        """,
        (connection_id, tenant_id),
    )
    if row is None or row["session_enc"] is None:
        return StoredSession(token=None, expires_at=None)

    try:
        token = decrypt_secret(row["session_enc"])
    except CredentialUnreadable:
        logger.info(
            "stored session unreadable (rotated key); will log in again "
            "tenant=%s connection=%s", tenant_id, connection_id,
        )
        return StoredSession(token=None, expires_at=None)

    return StoredSession(token=token, expires_at=row["session_expires_at"])


@contextmanager
def use_credential(conn, *, connection_id: UUID, tenant_id: UUID) -> Iterator[Credential]:
    """Hand the plaintext credential to a connector, for one block, and drop it.

    A context manager rather than a getter so the plaintext has a scope somebody
    can see. `get_credential()` would return a password with no defined lifetime,
    and a password with no defined lifetime ends up on an object, then in a log
    line, then in a bug report.

        with use_credential(conn, connection_id=cid, tenant_id=tid) as cred:
            session = authenticator.login(cred)
        # `cred` is out of scope here, on purpose

    Raises LookupError when nothing is stored, and CredentialUnreadable when the
    key has been rotated - two different messages for the merchant, because the
    first means "conecta tu cuenta" and the second means "vuelve a ingresarla".
    """
    row = fetch_one(
        conn,
        """
        SELECT username, secret_enc
          FROM core.connection_credential
         WHERE connection_id = %s AND tenant_id = %s
        """,
        (connection_id, tenant_id),
    )
    if row is None:
        raise LookupError(
            "Esta conexión no tiene una cuenta conectada. Ingresa tu usuario y "
            "contraseña de la plataforma en Configuración → Conexiones."
        )

    password = decrypt_secret(row["secret_enc"])
    if not password:  # pragma: no cover - NOT NULL plus a non-empty CHECK upstream
        raise CredentialUnreadable(
            "La credencial guardada está vacía. Vuelve a ingresar tu usuario y "
            "contraseña."
        )

    credential = Credential(username=row["username"], password=password)
    try:
        yield credential
    finally:
        # Blank the field before the object becomes garbage. This does not
        # scrub CPython's string memory - nothing in Python can - but it drops
        # the last reachable reference immediately instead of leaving one alive
        # in a frame that a traceback or a debugger could still walk.
        credential.password = ""
        del password
