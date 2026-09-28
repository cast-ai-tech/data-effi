"""Roles across the holding, as opposed to roles inside a company.

What these tests are really asserting:
  - an org `viewer` sees the consolidated totals of companies they do not belong to
  - and still cannot open a single number underneath them, or create a company
  - the last administrator cannot be demoted, because nobody could undo it
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "operador@masterdata.app"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "socio.holding@masterdata.app"
PARTNER_PASSWORD = "clave-del-socio-1234"


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
def owner(client, api_dsn) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Operador",
            "tenant_name": "Sociedad Uno",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    # The free month allows one company; the holding needs two, so the org
    # goes on a paid plan the way an advisor would put it there.
    from api.billing import activate_plan

    me = client.get("/auth/me", headers=auth(body["access_token"])).json()
    with psycopg.connect(api_dsn) as conn:
        activate_plan(conn, me["org_id"], "master_elite", months=None)
        conn.commit()
    return body


@pytest.fixture(scope="module")
def second_company(client, owner) -> str:
    response = client.post(
        "/org/tenants",
        headers=auth(owner["access_token"]),
        json={"name": "Sociedad Dos", "countries": ["GT"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["tenant_id"]


@pytest.fixture(scope="module")
def partner_token(client, owner) -> str:
    """Invited into company one only - never into company two."""
    invite = client.post(
        "/auth/invite",
        headers=auth(owner["access_token"]),
        json={"email": PARTNER_EMAIL, "role": "viewer"},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": PARTNER_PASSWORD,
            "full_name": "Socio del holding",
        },
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["access_token"]


# =============================================================================
# The first owner runs the holding
# =============================================================================


def test_the_first_owner_is_an_org_admin(client, owner):
    body = client.get("/auth/me", headers=auth(owner["access_token"])).json()
    assert body["org_role"] == "admin"
    assert body["is_org_admin"] is True


def test_a_plain_member_has_no_org_role(client, partner_token):
    body = client.get("/auth/me", headers=auth(partner_token)).json()
    assert body["org_role"] is None
    assert body["is_org_admin"] is False


def test_only_an_admin_grants_org_roles(client, partner_token):
    response = client.post(
        "/org/members",
        headers=auth(partner_token),
        json={"email": OWNER_EMAIL, "role": "viewer"},
    )
    assert response.status_code == 403, response.text


def test_an_unknown_email_cannot_be_promoted(client, owner):
    response = client.post(
        "/org/members",
        headers=auth(owner["access_token"]),
        json={"email": "nadie@masterdata.app", "role": "viewer"},
    )
    assert response.status_code == 404, response.text


# =============================================================================
# Seeing the whole group without touching it
# =============================================================================


def test_an_org_viewer_consolidates_companies_they_do_not_belong_to(
    client, owner, partner_token, second_company
):
    """Before the promotion the roll-up has one company. After it, both."""
    before = client.get("/org/summary", headers=auth(partner_token))
    assert before.status_code == 200, before.text
    assert len(before.json()["by_tenant"]) == 1

    granted = client.post(
        "/org/members",
        headers=auth(owner["access_token"]),
        json={"email": PARTNER_EMAIL, "role": "viewer"},
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["role"] == "viewer"

    # The grant reaches the session on the next token, exactly like a membership.
    again = client.post(
        "/auth/login", json={"email": PARTNER_EMAIL, "password": PARTNER_PASSWORD}
    )
    assert again.status_code == 200, again.text
    assert again.json()["org_role"] == "viewer"

    after = client.get("/org/summary", headers=auth(again.json()["access_token"]))
    assert after.status_code == 200, after.text
    assert len(after.json()["by_tenant"]) == 2


def test_an_org_viewer_still_cannot_create_a_company(client):
    session = client.post(
        "/auth/login", json={"email": PARTNER_EMAIL, "password": PARTNER_PASSWORD}
    )
    response = client.post(
        "/org/tenants",
        headers=auth(session.json()["access_token"]),
        json={"name": "Sociedad Tres", "countries": ["CO"]},
    )
    assert response.status_code == 403, response.text


def test_an_org_viewer_does_not_gain_entry_to_those_companies(client, second_company):
    """The totals are visible; the company behind them is not theirs to open."""
    session = client.post(
        "/auth/login", json={"email": PARTNER_EMAIL, "password": PARTNER_PASSWORD}
    )
    response = client.post(
        "/auth/switch",
        headers=auth(session.json()["access_token"]),
        json={"tenant_id": second_company},
    )
    assert response.status_code == 403, response.text


# =============================================================================
# The last administrator
# =============================================================================


def test_the_last_org_admin_cannot_be_demoted(client, owner):
    members = client.get("/org/members", headers=auth(owner["access_token"])).json()
    admin = next(m for m in members if m["role"] == "admin")

    response = client.patch(
        f"/org/members/{admin['user_id']}",
        headers=auth(owner["access_token"]),
        json={"role": "viewer"},
    )
    assert response.status_code == 409, response.text


def test_the_last_org_admin_cannot_be_removed(client, owner):
    members = client.get("/org/members", headers=auth(owner["access_token"])).json()
    admin = next(m for m in members if m["role"] == "admin")

    response = client.delete(
        f"/org/members/{admin['user_id']}", headers=auth(owner["access_token"])
    )
    assert response.status_code == 409, response.text


def test_revoking_an_org_role_leaves_the_companies_alone(client, owner):
    members = client.get("/org/members", headers=auth(owner["access_token"])).json()
    partner = next(m for m in members if m["email"] == PARTNER_EMAIL)

    revoked = client.delete(
        f"/org/members/{partner['user_id']}", headers=auth(owner["access_token"])
    )
    assert revoked.status_code == 204, revoked.text

    session = client.post(
        "/auth/login", json={"email": PARTNER_EMAIL, "password": PARTNER_PASSWORD}
    )
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["org_role"] is None
    # Still inside their own company: losing the holding role is not an eviction.
    assert len(body["workspaces"]) == 1


def _partner_session_with_org_role(client, owner, role: str) -> tuple[str, dict]:
    granted = client.post(
        "/org/members", headers=auth(owner["access_token"]),
        json={"email": PARTNER_EMAIL, "role": role},
    )
    assert granted.status_code == 201, granted.text
    session = client.post(
        "/auth/login", json={"email": PARTNER_EMAIL, "password": PARTNER_PASSWORD}
    )
    assert session.status_code == 200, session.text
    assert session.json()["org_role"] == role
    return granted.json()["user_id"], session.json()


def test_removing_an_org_role_revokes_that_persons_refresh_tokens(
    client, owner, partner_token
):
    """Otherwise the old role outlives the removal: refresh would re-mint it."""
    partner_id, session = _partner_session_with_org_role(client, owner, "viewer")

    revoked = client.delete(f"/org/members/{partner_id}", headers=auth(owner["access_token"]))
    assert revoked.status_code == 204, revoked.text

    refreshed = client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refreshed.status_code == 401, refreshed.text
    # The admin who did it keeps their own session.
    mine = client.post("/auth/refresh", json={"refresh_token": owner["refresh_token"]})
    assert mine.status_code == 200, mine.text
    owner["refresh_token"] = mine.json()["refresh_token"]
    owner["access_token"] = mine.json()["access_token"]


def test_changing_an_org_role_revokes_that_persons_refresh_tokens(
    client, owner, partner_token
):
    partner_id, session = _partner_session_with_org_role(client, owner, "analyst")

    changed = client.patch(
        f"/org/members/{partner_id}", headers=auth(owner["access_token"]),
        json={"role": "viewer"},
    )
    assert changed.status_code == 200, changed.text

    refreshed = client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refreshed.status_code == 401, refreshed.text

    client.delete(f"/org/members/{partner_id}", headers=auth(owner["access_token"]))


# =============================================================================
# The organisation itself
# =============================================================================


def test_an_admin_renames_the_organisation(client, owner):
    response = client.patch(
        "/org/", headers=auth(owner["access_token"]), json={"name": "Grupo Renombrado"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Grupo Renombrado"
