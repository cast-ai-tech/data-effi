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
from api.db import execute, fetch_all
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
    from connectors.effi.auth import (
        AccountLocked,
        EffiAuthenticator,
        InvalidCredentials,
        LoginContractUnverified,
        LoginUnavailable,
    )
    from connectors.effi.permissions import run_preflight
    from connectors.effi.session_fetcher import EffiSessionFetcher

    if platform_code != "effi":
        raise ApiError(
            "test_not_supported",
            f"Todavía no se puede probar una conexión de {platform_name} desde aquí.",
        )

    # -- 1. get a session, logging in only if we have to --------------------
    stored = credentials.load_session(
        conn, connection_id=connection_id, tenant_id=tenant_id
    )
    authenticator = EffiAuthenticator()

    try:
        with credentials.use_credential(
            conn, connection_id=connection_id, tenant_id=tenant_id
        ) as credential:
            session, did_login = authenticator.ensure_session(
                credential,
                existing_token=stored.token,
                existing_expires_at=stored.expires_at,
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

    # -- 2. probe each permission ------------------------------------------
    fetcher = EffiSessionFetcher.from_session(
        session, consent_granted_at=consent_granted_at
    )
    report = run_preflight(fetcher, base_url=fetcher.base_url)

    # -- 3. write down what was found, so the next screen shows it ----------
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

    credential_status = report.credential_status()
    execute(
        conn,
        "UPDATE core.connection SET credential_status = %s WHERE id = %s AND tenant_id = %s",
        (credential_status, connection_id, tenant_id),
    )
    if credential_status == "expired":
        credentials.clear_credential(
            conn, connection_id=connection_id, tenant_id=tenant_id
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
