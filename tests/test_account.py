"""The account panel: what a person may change about themselves.

What these tests are really asserting:
  - you edit your own account and nobody else's - there is no id to tamper with
  - the current password is required even though the caller is authenticated
  - changing it ends every session, because the reason people change a password
    is that they believe someone else has it
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

EMAIL = "cuenta@dataeffi.co"
PASSWORD = "una-clave-larga-de-prueba"
NEW_PASSWORD = "otra-clave-mas-larga-todavia"


@pytest.fixture(scope="module")
def api_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture(scope="module")
def client(api_dsn):
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = api_dsn
    os.environ["DATABASE_URL_READONLY"] = ""
    os.environ["AI_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = "uploads/test"
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)

    from api.settings import get_settings

    get_settings.cache_clear()

    from api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def account(client) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "full_name": "Nombre Inicial",
            "tenant_name": "Sociedad de la cuenta",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# =============================================================================
# The profile
# =============================================================================


def test_a_person_edits_their_own_name(client, account):
    response = client.patch(
        "/auth/me", headers=auth(account["access_token"]), json={"full_name": "Nombre Nuevo"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Nombre Nuevo"

    again = client.get("/auth/me", headers=auth(account["access_token"]))
    assert again.json()["full_name"] == "Nombre Nuevo"


def test_an_empty_patch_changes_nothing(client, account):
    response = client.patch("/auth/me", headers=auth(account["access_token"]), json={})
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Nombre Nuevo"


def test_the_profile_needs_a_session(client):
    assert client.get("/auth/me").status_code == 401
    assert client.patch("/auth/me", json={"full_name": "x"}).status_code == 401


def test_the_profile_carries_the_workspaces_and_capabilities(client, account):
    body = client.get("/auth/me", headers=auth(account["access_token"])).json()
    assert body["email"] == EMAIL
    assert body["role"] == "owner"
    assert "read" in body["capabilities"]
    assert [ws["name"] for ws in body["workspaces"]] == ["Sociedad de la cuenta"]


# =============================================================================
# Sessions
# =============================================================================


def test_a_fresh_login_shows_up_as_a_session(client, account):
    response = client.get("/auth/me/sessions", headers=auth(account["access_token"]))
    assert response.status_code == 200, response.text
    assert len(response.json()) >= 1
    assert response.json()[0]["tenant_name"] == "Sociedad de la cuenta"


def test_closing_a_session_stops_its_refresh_token(client, account):
    second = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert second.status_code == 200, second.text
    refresh = second.json()["refresh_token"]

    sessions = client.get(
        "/auth/me/sessions", headers=auth(second.json()["access_token"])
    ).json()
    assert len(sessions) >= 2
    newest = sessions[0]["id"]

    closed = client.delete(
        f"/auth/me/sessions/{newest}", headers=auth(account["access_token"])
    )
    assert closed.status_code == 204, closed.text

    refreshed = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 401


# =============================================================================
# The password
# =============================================================================


def test_the_current_password_is_required_to_be_right(client, account):
    response = client.post(
        "/auth/me/password",
        headers=auth(account["access_token"]),
        json={"current_password": "no-es-esta-clave", "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 401, response.text


def test_a_short_password_is_refused_with_a_reason(client, account):
    response = client.post(
        "/auth/me/password",
        headers=auth(account["access_token"]),
        json={"current_password": PASSWORD, "new_password": "corta"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "weak_password"


def test_repeating_the_same_password_is_refused(client, account):
    response = client.post(
        "/auth/me/password",
        headers=auth(account["access_token"]),
        json={"current_password": PASSWORD, "new_password": PASSWORD},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "same_password"


def test_changing_the_password_ends_every_session(client, account):
    """The old refresh token dies, and the new password is the one that works."""
    session = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert session.status_code == 200, session.text
    old_refresh = session.json()["refresh_token"]

    changed = client.post(
        "/auth/me/password",
        headers=auth(session.json()["access_token"]),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 204, changed.text

    assert client.post("/auth/refresh", json={"refresh_token": old_refresh}).status_code == 401
    assert client.post(
        "/auth/login", json={"email": EMAIL, "password": PASSWORD}
    ).status_code == 401
    assert client.post(
        "/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD}
    ).status_code == 200


def test_logging_out_revokes_the_refresh_token_server_side(client, account):
    """The button in the sidebar must end the session everywhere, not just here.

    Regression: the frontend used to POST an empty refresh_token, so the real
    one stayed valid for fourteen days after "Cerrar sesión".
    """
    fresh = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert fresh.status_code == 200, fresh.text
    refresh = fresh.json()["refresh_token"]

    out = client.post("/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 204, out.text

    refreshed = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 401


def test_an_unknown_email_takes_as_long_as_a_wrong_password(client, account):
    """Both answers must look the same; the timing must not tell them apart.

    argon2 verification dominates the request, so a login that skips it for an
    unknown email answers an order of magnitude faster. The bound is loose on
    purpose: it catches "skipped entirely", not scheduler jitter.
    """
    import time

    def elapsed(email: str) -> float:
        started = time.perf_counter()
        response = client.post("/auth/login", json={"email": email, "password": "wrong-pw-1"})
        assert response.status_code == 401
        return time.perf_counter() - started

    # Warm up once so the first-call cost of either path does not skew it.
    elapsed(EMAIL)
    elapsed("nadie@dataeffi.co")
    known = min(elapsed(EMAIL) for _ in range(3))
    unknown = min(elapsed("nadie@dataeffi.co") for _ in range(3))
    assert unknown > known * 0.25, f"unknown={unknown:.4f}s known={known:.4f}s"
