"""The "Probar conexión" button, end to end, without Effi and without a database.

Three promises the button used to break:

  1. A stored password Effi already rejected is not tried again by pressing the
     button - that is the lockout loop the worker already refuses.
  2. A stored session that died early costs one fresh login, not a false
     "vuelve a ingresar tu contraseña".
  3. A connection proven usable re-enters the worker's schedule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api import credentials, preflight
from api.errors import ApiError
from connectors.effi import auth, permissions
from connectors.effi import session_fetcher as sf


class _Report:
    def __init__(self, session_valid: bool, usable: bool = True):
        self.session_valid = session_valid
        self.is_usable = session_valid and usable
        self.results = []

    def credential_status(self):
        return "ok" if self.is_usable else ("expired" if not self.session_valid else "insufficient_permissions")

    def summary(self):
        return "resumen"


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(
        credential_status="ok", last_login_error=None, sql=[], logins=0,
        cleared=0, reports=[], stored_token="ci_session=guardada",
    )

    monkeypatch.setattr(
        preflight, "fetch_one",
        lambda conn, sql, params: {
            "credential_status": state.credential_status,
            "last_login_error": state.last_login_error,
        },
    )
    monkeypatch.setattr(preflight, "execute", lambda conn, sql, params=None: state.sql.append(" ".join(sql.split())))
    monkeypatch.setattr(preflight, "_permissions_from_view", lambda *a: [])

    def load_session(conn, **k):
        if state.stored_token:
            return credentials.StoredSession(state.stored_token, datetime.now(UTC) + timedelta(hours=6))
        return credentials.StoredSession(None, None)

    def clear(conn, **k):
        state.cleared += 1
        state.stored_token = None

    class _Cred:
        def __enter__(self):
            from pipeline.vault import Credential

            return Credential(username="u@t.co", password="p")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(credentials, "load_session", load_session)
    monkeypatch.setattr(credentials, "clear_credential", clear)
    monkeypatch.setattr(credentials, "save_session", lambda conn, **k: None)
    monkeypatch.setattr(credentials, "use_credential", lambda conn, **k: _Cred())
    monkeypatch.setattr(credentials, "record_login_failure", lambda conn, **k: None)

    class _Auth:
        def ensure_session(self, credential, *, existing_token, existing_expires_at):
            if existing_token:
                return auth.EffiSession(existing_token, existing_expires_at, datetime.now(UTC)), False
            state.logins += 1
            return auth.EffiSession("ci_session=nueva", datetime.now(UTC) + timedelta(hours=12), datetime.now(UTC)), True

    monkeypatch.setattr(auth, "EffiAuthenticator", _Auth)
    monkeypatch.setattr(sf.EffiSessionFetcher, "from_session", classmethod(
        lambda cls, session, **k: SimpleNamespace(base_url="https://effi.test", token=session.token)
    ))
    monkeypatch.setattr(permissions, "run_preflight", lambda fetcher, **k: state.reports.pop(0))
    return state


def _press(consent=True):
    return preflight.run_preflight_for_connection(
        object(), connection_id=uuid4(), tenant_id=uuid4(),
        platform_code="effi", platform_name="Effi",
        consent_granted_at=datetime.now(UTC) if consent else None,
    )


@pytest.mark.parametrize("status", ["invalid", "locked"])
def test_a_rejected_password_is_not_tried_again_by_the_button(world, status):
    world.credential_status = status
    world.stored_token = None

    response = _press()

    assert world.logins == 0, "el botón volvió a probar una contraseña que Effi ya rechazó"
    assert response.credential_status == status
    assert response.is_usable is False


def test_a_stored_session_that_died_early_costs_one_login_not_a_new_password(world):
    world.reports = [_Report(session_valid=False), _Report(session_valid=True)]

    response = _press()

    assert world.logins == 1
    assert response.credential_status == "ok"


def test_a_session_from_a_fresh_login_is_not_retried(world):
    world.stored_token = None
    world.reports = [_Report(session_valid=False)]

    response = _press()

    assert world.logins == 1
    assert response.credential_status == "expired"


def test_a_usable_connection_rejoins_the_schedule(world):
    world.reports = [_Report(session_valid=True)]

    _press()

    revive = [s for s in world.sql if "WHEN status = 'error' THEN 'active'" in s]
    assert revive and "last_error = NULL" in revive[0]


def test_an_unusable_connection_is_not_revived(world):
    world.reports = [_Report(session_valid=True, usable=False)]

    _press()

    assert not [s for s in world.sql if "THEN 'active'" in s]


def test_no_consent_means_no_login(world):
    with pytest.raises(ApiError):
        _press(consent=False)

    assert world.logins == 0
