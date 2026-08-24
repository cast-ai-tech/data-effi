"""Organizations, memberships, country scope and the uploader role.

The scenario is the operator's real one, compressed: a holding with two
companies, a partner who may only see one country of one of them, and someone
who loads the files and must never see a number.

What these tests are really asserting:
  - a person can belong to two companies with a DIFFERENT role in each
  - the token names one company at a time, and switching re-reads the grant
  - `uploader` is refused by every reading surface
  - a country scope is enforced on the parameter AND on the multi-country view
  - the roll-up shows a partner only their own companies, and the operator all
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "jefe@dataeffi.co"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "socio.gt@dataeffi.co"
PARTNER_PASSWORD = "clave-del-socio-1234"
LOADER_EMAIL = "carga@dataeffi.co"
LOADER_PASSWORD = "clave-de-la-carga-99"


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
def owner(client) -> dict:
    """The operator: registers the deployment, so they run the holding."""
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Jefe",
            "tenant_name": "Sociedad Colombia",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_org_admin"] is True
    assert body["role"] == "owner"
    return body


@pytest.fixture(scope="module")
def colombia(owner) -> str:
    return owner["tenant_id"]


@pytest.fixture(scope="module")
def guatemala(client, owner) -> str:
    """A second company, so "several partnerships" is a real state in the tests."""
    response = client.post(
        "/org/tenants",
        headers=auth(owner["access_token"]),
        json={"name": "Sociedad Guatemala", "countries": ["GT", "EC"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert sorted(body["countries"]) == ["EC", "GT"]
    return body["tenant_id"]


@pytest.fixture(scope="module")
def owner_in_guatemala(client, owner, guatemala) -> str:
    response = client.post(
        "/auth/switch",
        headers=auth(owner["access_token"]),
        json={"tenant_id": guatemala},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def partner_token(client, owner_in_guatemala) -> str:
    """A partner limited to Guatemala inside the Guatemala company."""
    invite = client.post(
        "/auth/invite",
        headers=auth(owner_in_guatemala),
        json={
            "email": PARTNER_EMAIL,
            "role": "viewer",
            "country_scope": ["GT"],
            "share_pct": 40,
        },
    )
    assert invite.status_code == 201, invite.text
    body = invite.json()
    assert body["already_registered"] is False
    assert body["country_scope"] == ["GT"]

    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": body["invitation_token"],
            "password": PARTNER_PASSWORD,
            "full_name": "Socio Guatemala",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["countries"] == ["GT"]
    return accepted.json()["access_token"]


@pytest.fixture(scope="module")
def loader_token(client, owner_in_guatemala) -> str:
    invite = client.post(
        "/auth/invite",
        headers=auth(owner_in_guatemala),
        json={"email": LOADER_EMAIL, "role": "uploader"},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": LOADER_PASSWORD,
            "full_name": "Quien Carga",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "uploader"
    return accepted.json()["access_token"]


# =============================================================================
# The holding
# =============================================================================


def test_registering_creates_an_org_with_the_first_company(client, owner):
    response = client.get("/auth/me", headers=auth(owner["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["org_name"]
    assert body["is_org_admin"] is True
    assert sorted(body["capabilities"]) == ["config", "ingest", "manage", "read"]
    assert len(body["workspaces"]) == 1


def test_creating_a_company_adds_it_to_the_same_org(client, owner, guatemala):
    response = client.get("/org/tenants", headers=auth(owner["access_token"]))
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert names == {"Sociedad Colombia", "Sociedad Guatemala"}


def test_only_the_org_admin_may_create_companies(client, partner_token):
    response = client.post(
        "/org/tenants", headers=auth(partner_token), json={"name": "Sociedad Pirata"}
    )
    assert response.status_code == 403


# =============================================================================
# Invitations carry the grant
# =============================================================================


def test_a_country_scope_must_name_countries_the_company_operates(client, owner_in_guatemala):
    response = client.post(
        "/auth/invite",
        headers=auth(owner_in_guatemala),
        json={"email": "nadie@dataeffi.co", "role": "viewer", "country_scope": ["CO"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_country"


def test_inviting_a_known_email_grants_access_without_a_second_password(
    client, owner, colombia, partner_token
):
    """The same human, a second partnership: no new account, no new password."""
    response = client.post(
        "/auth/invite",
        headers=auth(owner["access_token"]),
        json={"email": PARTNER_EMAIL, "role": "analyst"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["already_registered"] is True
    assert body["invitation_token"] is None

    me = client.get("/auth/me", headers=auth(partner_token))
    roles = {ws["name"]: ws["role"] for ws in me.json()["workspaces"]}
    assert roles == {"Sociedad Colombia": "analyst", "Sociedad Guatemala": "viewer"}


def test_switching_company_re_reads_the_role(client, partner_token, colombia):
    """Viewer in one, analyst in the other - the token has to follow."""
    response = client.post(
        "/auth/switch", headers=auth(partner_token), json={"tenant_id": colombia}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_name"] == "Sociedad Colombia"
    assert body["role"] == "analyst"
    assert body["countries"] is None      # no scope in that company


def test_switching_to_a_company_you_do_not_belong_to_is_refused(client, loader_token, colombia):
    response = client.post(
        "/auth/switch", headers=auth(loader_token), json={"tenant_id": colombia}
    )
    assert response.status_code == 403


# =============================================================================
# The uploader sees nothing
# =============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/kpis/global",
        "/kpis/geo?country=GT",
        "/orders?country=GT",
        "/customers?country=GT",
        "/ai/alerts?country=GT",
        "/config/users",
    ],
)
def test_uploader_is_refused_every_reading_surface(client, loader_token, path):
    response = client.get(path, headers=auth(loader_token))
    assert response.status_code == 403, path


def test_uploader_can_still_do_its_job(client, loader_token):
    """Upload screen: the country list, the connections, and its own loads."""
    for path in ("/config/countries", "/config/connections", "/ingest/batches?country=GT"):
        response = client.get(path, headers=auth(loader_token))
        assert response.status_code == 200, path


# =============================================================================
# Country scope
# =============================================================================


def test_scoped_partner_is_refused_a_country_outside_the_scope(client, partner_token, guatemala):
    scoped = client.post(
        "/auth/switch", headers=auth(partner_token), json={"tenant_id": guatemala}
    ).json()["access_token"]

    allowed = client.get("/kpis/geo?country=GT", headers=auth(scoped))
    assert allowed.status_code == 200

    refused = client.get("/kpis/geo?country=EC", headers=auth(scoped))
    assert refused.status_code == 403
    assert "GT" in refused.json()["error"]["message"]


def test_scoped_partner_sees_only_their_country_in_the_multi_country_view(
    client, partner_token, guatemala
):
    """`/kpis/global` has no country parameter to refuse, so rows are filtered."""
    scoped = client.post(
        "/auth/switch", headers=auth(partner_token), json={"tenant_id": guatemala}
    ).json()["access_token"]

    response = client.get("/kpis/global", headers=auth(scoped))
    assert response.status_code == 200
    assert all(row["country_code"] == "GT" for row in response.json()["rows"])


def test_a_viewer_cannot_invite_or_manage(client, partner_token, guatemala):
    scoped = client.post(
        "/auth/switch", headers=auth(partner_token), json={"tenant_id": guatemala}
    ).json()["access_token"]

    invited = client.post(
        "/auth/invite", headers=auth(scoped), json={"email": "x@dataeffi.co", "role": "viewer"}
    )
    assert invited.status_code == 403

    listed = client.get(f"/org/tenants/{guatemala}/members", headers=auth(scoped))
    assert listed.status_code == 403


# =============================================================================
# The consolidated roll-up
# =============================================================================


def test_the_operator_sees_every_company_consolidated(client, owner, guatemala):
    response = client.get("/org/summary", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_currency"] == "USD"
    names = {row["name"] for row in body["by_tenant"]}
    assert names == {"Sociedad Colombia", "Sociedad Guatemala"}
    assert body["unavailable"] == []


def test_a_partner_only_consolidates_their_own_companies(client, loader_token):
    """The loader belongs to one company, so their roll-up has exactly one."""
    response = client.get("/org/summary", headers=auth(loader_token))
    assert response.status_code == 200
    names = {row["name"] for row in response.json()["by_tenant"]}
    assert names == {"Sociedad Guatemala"}


# =============================================================================
# Administration
# =============================================================================


def test_owner_lists_members_with_their_grants(client, owner, guatemala, partner_token, loader_token):
    response = client.get(
        f"/org/tenants/{guatemala}/members", headers=auth(owner["access_token"])
    )
    assert response.status_code == 200, response.text
    by_email = {row["email"]: row for row in response.json()}
    assert by_email[PARTNER_EMAIL]["country_scope"] == ["GT"]
    assert by_email[PARTNER_EMAIL]["share_pct"] == 40
    assert by_email[LOADER_EMAIL]["role"] == "uploader"


def test_widening_a_scope_takes_effect_on_the_next_token(client, owner, guatemala, partner_token):
    partner_id = next(
        row["user_id"]
        for row in client.get(
            f"/org/tenants/{guatemala}/members", headers=auth(owner["access_token"])
        ).json()
        if row["email"] == PARTNER_EMAIL
    )

    patched = client.patch(
        f"/org/tenants/{guatemala}/members/{partner_id}",
        headers=auth(owner["access_token"]),
        json={"clear_country_scope": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["country_scope"] is None

    refreshed = client.post(
        "/auth/switch", headers=auth(partner_token), json={"tenant_id": guatemala}
    ).json()
    assert refreshed["countries"] is None
    assert (
        client.get("/kpis/geo?country=EC", headers=auth(refreshed["access_token"])).status_code
        == 200
    )


def test_the_last_owner_cannot_be_removed(client, owner, guatemala):
    owner_id = next(
        row["user_id"]
        for row in client.get(
            f"/org/tenants/{guatemala}/members", headers=auth(owner["access_token"])
        ).json()
        if row["email"] == OWNER_EMAIL
    )
    response = client.delete(
        f"/org/tenants/{guatemala}/members/{owner_id}", headers=auth(owner["access_token"])
    )
    assert response.status_code == 409


def test_revoking_access_removes_the_company_from_that_person(
    client, owner, guatemala, loader_token
):
    loader_id = next(
        row["user_id"]
        for row in client.get(
            f"/org/tenants/{guatemala}/members", headers=auth(owner["access_token"])
        ).json()
        if row["email"] == LOADER_EMAIL
    )
    response = client.delete(
        f"/org/tenants/{guatemala}/members/{loader_id}", headers=auth(owner["access_token"])
    )
    assert response.status_code == 204

    # Their session is dead: logging in again finds no company at all.
    login = client.post("/auth/login", json={"email": LOADER_EMAIL, "password": LOADER_PASSWORD})
    assert login.status_code == 200
    assert login.json()["workspaces"] == []


def test_uploader_can_actually_upload(client, owner_in_guatemala, loader_token, guias_dia1):
    """The one thing an uploader exists for. Refused with 403 until this test.

    `require_cap("ingest")` on the route lets the role in; a second seniority
    check inside the handler (`at_least("analyst")`) was throwing it back out.
    """
    connection = client.post(
        "/config/connections",
        headers=auth(owner_in_guatemala),
        json={"platform_code": "manual_xlsx", "name": "Carga Guatemala"},
    )
    assert connection.status_code == 201, connection.text

    response = client.post(
        "/ingest/upload",
        data={"connection_id": connection.json()["connection_id"], "kind": "shipments"},
        files={"files": ("guias.csv", guias_dia1, "text/csv")},
        headers=auth(loader_token),
    )
    assert response.status_code == 202, response.text
    assert len(response.json()["jobs"]) == 1

    detected = client.post(
        "/ingest/detect",
        files={"file": ("guias.csv", guias_dia1, "text/csv")},
        headers=auth(loader_token),
    )
    assert detected.status_code == 200, detected.text


# -----------------------------------------------------------------------------
# The org chart: org -> company -> countries. Branches are gone (migration 043).
# -----------------------------------------------------------------------------


def test_branches_no_longer_exist(api_dsn):
    """Migration 043 removed the fourth level. Neither the table nor the link survives."""
    with psycopg.connect(api_dsn) as conn:
        table = conn.execute("SELECT to_regclass('core.branch')").fetchone()
        assert table is not None and table[0] is None
        column = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'core' AND table_name = 'store' AND column_name = 'branch_id'"
        ).fetchone()
        assert column is None


def test_the_org_chart_offers_every_supported_country(client, owner):
    response = client.get("/org/countries", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    codes = {row["code"] for row in response.json()}
    assert {"CO", "EC", "GT", "HN"} <= codes
    assert all(row["name"] and row["currency_code"] for row in response.json())


def test_editing_a_company_sets_its_countries_as_a_whole(client, owner, guatemala):
    headers = auth(owner["access_token"])
    response = client.patch(
        f"/org/tenants/{guatemala}",
        headers=headers,
        json={"name": "Sociedad Guatemala y Honduras", "countries": ["gt", "HN"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Sociedad Guatemala y Honduras"
    assert body["slug"] == "sociedad-guatemala"  # the identifier never moves
    assert body["countries"] == ["GT", "HN"]  # EC was left out, so it is off

    # The chart agrees with the edit.
    listed = client.get("/org/tenants", headers=headers).json()
    assert {t["tenant_id"]: t["countries"] for t in listed}[guatemala] == ["GT", "HN"]

    # Deactivated, not deleted: EC comes back exactly as it was, and HN goes quiet.
    response = client.patch(
        f"/org/tenants/{guatemala}", headers=headers, json={"countries": ["GT", "EC"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["countries"] == ["EC", "GT"]
    assert response.json()["name"] == "Sociedad Guatemala y Honduras"

    # Left as the other tests in this module expect it.
    response = client.patch(
        f"/org/tenants/{guatemala}", headers=headers, json={"name": "Sociedad Guatemala"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["countries"] == ["EC", "GT"]


def test_a_company_cannot_operate_in_an_unsupported_country(client, owner, guatemala):
    response = client.patch(
        f"/org/tenants/{guatemala}",
        headers=auth(owner["access_token"]),
        json={"countries": ["GT", "ZZ"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unknown_country"


def test_only_the_org_admin_may_edit_a_company(client, partner_token, guatemala):
    response = client.patch(
        f"/org/tenants/{guatemala}", headers=auth(partner_token), json={"name": "Otra"}
    )
    assert response.status_code == 403


def test_a_company_outside_the_org_cannot_be_edited(client, owner):
    response = client.patch(
        "/org/tenants/00000000-0000-0000-0000-000000000000",
        headers=auth(owner["access_token"]),
        json={"name": "Fantasma"},
    )
    assert response.status_code == 404
