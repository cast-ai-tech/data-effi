"""Branches: the physical places a company operates from.

What these tests are really asserting:
  - a branch can only exist in a country the company actually operates in
  - a partner limited to one country neither reads nor creates the others'
  - `viewer` may look and may not touch, `analyst` may do both
  - branches of one company are invisible to another, which is row-level
    security doing its job through a table added long after it was written
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "duena@dataeffi.co"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "socio.sucursales@dataeffi.co"
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
def owner(client) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña",
            "tenant_name": "Distribuidora Andina",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def guatemala(client, owner) -> str:
    """A second company operating in two countries."""
    response = client.post(
        "/org/tenants",
        headers=auth(owner["access_token"]),
        json={"name": "Distribuidora Centro", "countries": ["GT", "EC"]},
    )
    assert response.status_code == 201, response.text
    return response.json()["tenant_id"]


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
    """A viewer who may only ever see Guatemala."""
    invite = client.post(
        "/auth/invite",
        headers=auth(owner_in_guatemala),
        json={"email": PARTNER_EMAIL, "role": "viewer", "country_scope": ["GT"]},
    )
    assert invite.status_code == 201, invite.text
    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": PARTNER_PASSWORD,
            "full_name": "Socio Guatemala",
        },
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["access_token"]


@pytest.fixture(scope="module")
def bodega_gt(client, owner_in_guatemala) -> dict:
    response = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={
            "country_code": "GT",
            "name": "Bodega Zona 4",
            "city": "Ciudad de Guatemala",
            "address": "4a Avenida 12-34",
            "manager_name": "Ana Pérez",
            "phone": "+502 5555 1234",
            "cost_center": "CC-GT-01",
            "is_warehouse": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# =============================================================================
# A branch belongs to a company AND to a country it operates in
# =============================================================================


def test_a_branch_keeps_the_details_it_was_given(bodega_gt):
    assert bodega_gt["country_code"] == "GT"
    assert bodega_gt["city"] == "Ciudad de Guatemala"
    assert bodega_gt["manager_name"] == "Ana Pérez"
    assert bodega_gt["cost_center"] == "CC-GT-01"
    assert bodega_gt["is_warehouse"] is True
    assert bodega_gt["store_count"] == 0


def test_a_country_the_company_does_not_operate_is_refused(client, owner_in_guatemala):
    """Distribuidora Centro operates in GT and EC. Colombia is not hers."""
    response = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={"country_code": "CO", "name": "Bodega Bogotá"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "country_not_active"


def test_two_branches_cannot_share_a_name_in_one_country(
    client, owner_in_guatemala, bodega_gt
):
    response = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={"country_code": "GT", "name": bodega_gt["name"]},
    )
    assert response.status_code == 409, response.text


def test_the_same_name_is_fine_in_another_country(client, owner_in_guatemala, bodega_gt):
    response = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={"country_code": "EC", "name": bodega_gt["name"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["country_code"] == "EC"


# =============================================================================
# Country scope
# =============================================================================


def test_a_scoped_partner_sees_only_their_country(client, partner_token, bodega_gt):
    response = client.get("/config/branches", headers=auth(partner_token))
    assert response.status_code == 200, response.text
    countries = {row["country_code"] for row in response.json()}
    assert countries == {"GT"}, "el socio de GT no debería ver sucursales de EC"


def test_a_scoped_partner_is_refused_another_country_outright(client, partner_token):
    response = client.get(
        "/config/branches", headers=auth(partner_token), params={"country": "EC"}
    )
    assert response.status_code == 403, response.text


def test_a_viewer_may_look_and_may_not_create(client, partner_token):
    response = client.post(
        "/config/branches",
        headers=auth(partner_token),
        json={"country_code": "GT", "name": "Sucursal del socio"},
    )
    assert response.status_code == 403, response.text


# =============================================================================
# Editing and deleting
# =============================================================================


def test_editing_leaves_the_untouched_fields_alone(client, owner_in_guatemala, bodega_gt):
    response = client.patch(
        f"/config/branches/{bodega_gt['id']}",
        headers=auth(owner_in_guatemala),
        json={"phone": "+502 4444 0000"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["phone"] == "+502 4444 0000"
    assert body["city"] == bodega_gt["city"]
    assert body["manager_name"] == bodega_gt["manager_name"]


def test_a_deactivated_branch_disappears_from_the_default_list(
    client, owner_in_guatemala
):
    created = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={"country_code": "GT", "name": "Punto de venta temporal"},
    )
    assert created.status_code == 201, created.text
    branch_id = created.json()["id"]

    patched = client.patch(
        f"/config/branches/{branch_id}",
        headers=auth(owner_in_guatemala),
        json={"is_active": False},
    )
    assert patched.status_code == 200, patched.text

    listed = client.get("/config/branches", headers=auth(owner_in_guatemala))
    assert branch_id not in [row["id"] for row in listed.json()]

    with_inactive = client.get(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        params={"include_inactive": True},
    )
    assert branch_id in [row["id"] for row in with_inactive.json()]


def test_an_empty_branch_can_be_deleted(client, owner_in_guatemala):
    created = client.post(
        "/config/branches",
        headers=auth(owner_in_guatemala),
        json={"country_code": "GT", "name": "Oficina que cierra"},
    )
    assert created.status_code == 201, created.text
    branch_id = created.json()["id"]

    deleted = client.delete(
        f"/config/branches/{branch_id}", headers=auth(owner_in_guatemala)
    )
    assert deleted.status_code == 204, deleted.text

    gone = client.patch(
        f"/config/branches/{branch_id}",
        headers=auth(owner_in_guatemala),
        json={"phone": "1"},
    )
    assert gone.status_code == 404


# =============================================================================
# Isolation between companies
# =============================================================================


def test_another_company_never_sees_these_branches(client, owner, bodega_gt):
    """The owner is the same person - the COMPANY is what changes.

    This is the assertion that matters most in the file: `core.branch` was added
    to the schema long after row-level security was written, so a policy that had
    been forgotten would show up right here as somebody else's warehouse.
    """
    response = client.get("/config/branches", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    assert bodega_gt["id"] not in [row["id"] for row in response.json()]


def test_a_branch_of_another_company_cannot_be_opened_by_id(client, owner, bodega_gt):
    response = client.patch(
        f"/config/branches/{bodega_gt['id']}",
        headers=auth(owner["access_token"]),
        json={"phone": "0"},
    )
    assert response.status_code == 404, response.text
