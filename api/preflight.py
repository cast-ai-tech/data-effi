"""One button: log in, check every permission, write down what happened.

WHY THIS IS ITS OWN MODULE AND NOT A ROUTE
------------------------------------------
Two callers need exactly this sequence and must not drift apart:

  the merchant   presses "Probar conexión" and waits for an answer
  the worker     hits a 403 mid-sync and needs to know WHICH permission died,
                 so the connection lands in `insufficient_permissions` with a
                 fixable message instead of `error` with an HTTP code

If each wrote its own version, the worker's would eventually stop matching the
screen's, and a merchant would read "todo bien" on a connection the worker had
already given up on.

WHAT IT PROMISES
----------------
- At most ONE login attempt per call. Never a retry after a rejection.
- Every outcome ends with `credential_status` written to the row, so no caller
  has to remember to do it and no failure leaves a stale `ok` on screen.
- Nothing it writes to the database contains a password, a session token, or a
  raw response body.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from api import credentials
from api.db import execute, fetch_all, fetch_one
from api.errors import ApiError
from api.schemas import ConnectionPermissionRow, ConnectionPreflightResponse
from pipeline.vault import CredentialUnreadable, VaultKeyMissing

logger = logging.getLogger(__name__)


def run_preflight_for_connection(
    conn,
    *,
    connection_id: UUID,
    tenant_id: UUID,
    platform_code: str,
    platform_name: str,
    consent_granted_at: datetime | None,
) -> ConnectionPreflightResponse:
    """Prove a connection works, and say precisely what is wrong when it does not."""
    from connectors.effi.auth import EffiAuthenticator
    from connectors.effi.permissions import run_preflight
    from connectors.effi.session_fetcher import EffiSessionFetcher

    if platform_code != "effi":
        raise ApiError(
            "test_not_supported",
            f"Todavía no se puede probar una conexión de {platform_name} desde aquí.",
        )

    # -- 0. things that must stop us BEFORE the password is used -----------
    if consent_granted_at is None:
        # Logging in with a merchant's password is exactly what consent is for.
        # Checked here, not only in the fetcher, because the fetcher is built
        # AFTER the login - too late to have asked.
        raise ApiError(
            "consent_required",
            "Esta conexión no tiene tu autorización registrada. Vuelve a guardar "
            "el usuario y la contraseña aceptando la autorización.",
        )

    current = fetch_one(
        conn,
        """
        SELECT c.credential_status, cc.last_login_error
          FROM core.connection c
          LEFT JOIN core.connection_credential cc ON cc.connection_id = c.id
         WHERE c.id = %s AND c.tenant_id = %s
        """,
        (connection_id, tenant_id),
    )
    if current and current["credential_status"] in _TERMINAL_CREDENTIAL_STATUSES:
        # "WRONG PASSWORD IS FINAL" (connectors/effi/auth.py) applies to this
        # button too. Pressing it again with the same stored password is one
        # more failed login on the merchant's real account; enough presses and
        # we are the ones who locked them out. Only re-entering the credential
        # (which resets the status to 'none') opens the door again.
        return ConnectionPreflightResponse(
            connection_id=connection_id,
            credential_status=current["credential_status"],
            is_usable=False,
            summary=_terminal_summary(
                current["credential_status"], current["last_login_error"]
            ),
            permissions=_permissions_from_view(conn, connection_id, tenant_id),
        )

    # -- 1+2. get a session and probe with it --------------------------------
    # At most two passes, and a second one only when the FIRST used a stored
    # session that Effi turned out to have dropped early. Then the password is
    # still good: log in once and look again, instead of telling the merchant
    # to retype a password that was never the problem. A session from a login
    # we just did is never retried - that would be a login loop.
    #
    # A session sent from the browser extension (migration 070) has no password
    # behind it: one pass, no login, and a rejection means "send it again".
    stored = credentials.load_session(
        conn, connection_id=connection_id, tenant_id=tenant_id
    )
    browser_session = stored.is_browser_session
    if browser_session:
        if not stored.token:
            return _browser_session_dead(
                conn, connection_id, tenant_id, BROWSER_SESSION_MISSING
            )
        fetcher = EffiSessionFetcher.from_session(
            stored, consent_granted_at=consent_granted_at
        )
        report = run_preflight(fetcher, base_url=fetcher.base_url)
    else:
        authenticator = EffiAuthenticator()
        force_login = False
        while True:
            outcome = _acquire_session(
                conn, connection_id, tenant_id, authenticator, force_login=force_login
            )
            if isinstance(outcome, ConnectionPreflightResponse):
                return outcome
            session, did_login = outcome

            fetcher = EffiSessionFetcher.from_session(
                session, consent_granted_at=consent_granted_at
            )
            report = run_preflight(fetcher, base_url=fetcher.base_url)
            if report.session_valid or did_login or force_login:
                break
            credentials.clear_credential(conn, connection_id=connection_id, tenant_id=tenant_id)
            force_login = True

    # -- 3. write down what was found, so the next screen shows it ----------
    _record_probes(conn, connection_id, tenant_id, report)

    credential_status = report.credential_status()
    if browser_session and credential_status == "expired":
        return _browser_session_dead(
            conn, connection_id, tenant_id, BROWSER_SESSION_REJECTED
        )
    execute(
        conn,
        "UPDATE core.connection SET credential_status = %s WHERE id = %s AND tenant_id = %s",
        (credential_status, connection_id, tenant_id),
    )
    if credential_status == "expired":
        credentials.clear_credential(
            conn, connection_id=connection_id, tenant_id=tenant_id
        )
    if report.is_usable:
        # The worker skips `error` rows forever. A connection the merchant has
        # just proven works (after fixing a permission, or after Effi came
        # back) must re-enter the schedule, or the green checklist on screen
        # would sit next to a sync that never runs again.
        execute(
            conn,
            """
            UPDATE core.connection
               SET status = CASE WHEN status = 'error' THEN 'active' ELSE status END,
                   last_error = NULL
             WHERE id = %s AND tenant_id = %s
            """,
            (connection_id, tenant_id),
        )

    logger.info(
        "preflight tenant=%s connection=%s status=%s usable=%s",
        tenant_id, connection_id, credential_status, report.is_usable,
    )

    return ConnectionPreflightResponse(
        connection_id=connection_id,
        credential_status=credential_status,
        is_usable=report.is_usable,
        summary=report.summary(),
        # Read back through the view rather than rebuilding the rows from the
        # probe. The probe knows the OUTCOME; the catalogue owns the contract -
        # the wording of `why`, which actions we ask for, whether Effi restricts
        # it to an administrator. Reconstructing those here would mean this
        # response and the next GET disagreed about the same permission.
        permissions=_permissions_from_view(conn, connection_id, tenant_id),
    )


_TERMINAL_CREDENTIAL_STATUSES = ("invalid", "locked")

BROWSER_SESSION_MISSING = (
    "No hay una sesión de Effi guardada para esta conexión. Entra a Effi en tu "
    "navegador y envíala desde la extensión «Conectar Effi con Data Effi»."
)
BROWSER_SESSION_REJECTED = (
    "Effi no aceptó la sesión enviada desde la extensión: venció o se cerró. "
    "Entra de nuevo a Effi en tu navegador y vuelve a enviarla desde la extensión."
)


def _browser_session_dead(
    conn, connection_id: UUID, tenant_id: UUID, message: str
) -> ConnectionPreflightResponse:
    """The extension's session is gone. No login to try: say how to send another."""
    credentials.mark_session_expired(
        conn, connection_id=connection_id, tenant_id=tenant_id, message=message
    )
    return ConnectionPreflightResponse(
        connection_id=connection_id,
        credential_status="session_expired",
        is_usable=False,
        summary=message,
        permissions=_permissions_from_view(conn, connection_id, tenant_id),
    )


def _terminal_summary(credential_status: str, last_error: str | None) -> str:
    if credential_status == "locked":
        advice = ("Effi bloqueó la cuenta. Desbloquéala en Effi y vuelve a guardar el "
                  "usuario y la contraseña aquí; no se volverá a intentar antes.")
    else:
        advice = ("Effi rechazó el usuario o la contraseña guardados. Vuelve a "
                  "escribirlos; no se volverá a intentar con los mismos para no "
                  "bloquear tu cuenta.")
    return f"{last_error} {advice}" if last_error else advice


def _record_probes(conn, connection_id: UUID, tenant_id: UUID, report) -> None:
    for result in report.results:
        execute(
            conn,
            """
            INSERT INTO core.connection_permission_probe
                (connection_id, tenant_id, permission_code, status, detail, checked_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, permission_code) DO UPDATE SET
                status     = EXCLUDED.status,
                detail     = EXCLUDED.detail,
                checked_at = EXCLUDED.checked_at
            """,
            (
                connection_id, tenant_id, result.code, result.status,
                result.detail, result.checked_at,
            ),
        )


def _acquire_session(conn, connection_id: UUID, tenant_id: UUID, authenticator, *, force_login: bool):
    """`(session, did_login)`, or the failure response to hand straight back."""
    from connectors.effi.auth import (
        AccountLocked,
        InvalidCredentials,
        LoginContractUnverified,
        LoginUnavailable,
    )

    stored = credentials.load_session(conn, connection_id=connection_id, tenant_id=tenant_id)
    try:
        with credentials.use_credential(
            conn, connection_id=connection_id, tenant_id=tenant_id
        ) as credential:
            session, did_login = authenticator.ensure_session(
                credential,
                existing_token=None if force_login else stored.token,
                existing_expires_at=None if force_login else stored.expires_at,
            )
    except LookupError as exc:
        return _failed(
            conn, connection_id, tenant_id, "none", str(exc),
            write_credential_error=False,
        )
    except CredentialUnreadable as exc:
        return _failed(
            conn, connection_id, tenant_id, "expired", str(exc),
            write_credential_error=False,
        )
    except VaultKeyMissing as exc:
        # A server misconfiguration, not the merchant's problem. Do not stamp
        # their connection as broken over it.
        raise ApiError("vault_unavailable", str(exc)) from None
    except InvalidCredentials as exc:
        return _failed(conn, connection_id, tenant_id, "invalid", str(exc))
    except AccountLocked as exc:
        return _failed(conn, connection_id, tenant_id, "locked", str(exc))
    except LoginContractUnverified as exc:
        # The honest answer while the login shape is still unconfirmed. The
        # connection is left untouched: nothing failed, nothing was proven.
        raise ApiError("login_not_ready", str(exc)) from None
    except LoginUnavailable as exc:
        raise ApiError("platform_unreachable", str(exc)) from None

    if did_login:
        credentials.save_session(
            conn,
            connection_id=connection_id,
            tenant_id=tenant_id,
            token=session.token,
            expires_at=session.expires_at,
        )

    return session, did_login


def _permissions_from_view(
    conn, connection_id: UUID, tenant_id: UUID
) -> list[ConnectionPermissionRow]:
    """The permission contract joined to whatever the last probe found."""
    rows = fetch_all(
        conn,
        """
        SELECT permission_code, permission_name, actions, why, requirement,
               admin_only, status, detail, checked_at
          FROM mart.v_connection_permissions
         WHERE connection_id = %s AND tenant_id = %s
         ORDER BY sort_order
        """,
        (connection_id, tenant_id),
    )
    return [ConnectionPermissionRow(**row) for row in rows]


def _failed(
    conn,
    connection_id: UUID,
    tenant_id: UUID,
    credential_status: str,
    message: str,
    *,
    write_credential_error: bool = True,
) -> ConnectionPreflightResponse:
    """Record a terminal failure and answer with something the merchant can act on.

    Returns a 200 rather than raising, on purpose: "tu contraseña de Effi está
    mal" is a valid, expected answer to "prueba la conexión", not an API error.
    The merchant needs to read it on the same screen, next to the permission
    list, not in a red toast that disappears.
    """
    if write_credential_error:
        credentials.record_login_failure(
            conn,
            connection_id=connection_id,
            tenant_id=tenant_id,
            credential_status=credential_status,
            message=message,
        )
    else:
        execute(
            conn,
            "UPDATE core.connection SET credential_status = %s WHERE id = %s AND tenant_id = %s",
            (credential_status, connection_id, tenant_id),
        )

    # The checklist still travels with a failure. A merchant whose password was
    # wrong is about to fix it and press the button again; taking the permission
    # list off the screen at that exact moment would be perverse.
    return ConnectionPreflightResponse(
        connection_id=connection_id,
        credential_status=credential_status,
        is_usable=False,
        summary=message,
        permissions=_permissions_from_view(conn, connection_id, tenant_id),
    )
