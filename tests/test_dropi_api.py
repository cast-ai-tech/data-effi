"""`/config/dropi`: connect a Dropi account by token, test it, disconnect it.

Against a real PostgreSQL (skipped without one), with Dropi itself replaced by a
fake client. What is defended:

  - the token goes in and never comes back out, in any response;
  - the country scope holds for every endpoint - a partner limited to
    Guatemala cannot read, rotate, test or create a Colombian Dropi link;
  - a second API connection for the same country is refused, pointing at the
    existing one, because two connections would duplicate every guide;
  - a rejected token is recorded as `invalid` and answered with 200.
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "dropi.api@masterdata.app"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "socio.gt.dropi@masterdata.app"
PARTNER_PASSWORD = "clave-del-socio-1234"
TOKEN = "tok-dropi-ficticio-abcdef0123456789"
OTHER_TOKEN = "tok-dropi-nuevo-9876543210fedcba"


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

    from pipeline import vault

    os.environ["DATABASE_URL"] = api_dsn
    os.environ["DATABASE_URL_READONLY"] = ""
    os.environ["AI_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = "uploads/test"
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)
    os.environ[vault.ENV_KEY] = vault.generate_key()
    vault._fernet.cache_clear()

    from api.settings import get_settings

    get_settings.cache_clear()

    from api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
    vault._fernet.cache_clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def owner(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña Dropi",
            "tenant_name": "Operación Dropi",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    for code in ("CO", "GT"):
        activated = client.put(
            "/config/countries",
            headers=auth(token),
            json={"country_code": code, "is_active": True},
        )
        assert activated.status_code == 200, activated.text
    return token


@pytest.fixture(scope="module")
def partner(client, owner) -> str:
    """Owner role, Guatemala only: only the country scope can stop them."""
    invite = client.post(
        "/auth/invite",
        headers=auth(owner),
        json={"email": PARTNER_EMAIL, "role": "owner", "country_scope": ["GT"]},
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
def co_connection(client, owner) -> str:
    response = client.post(
        "/config/dropi/connections",
        headers=auth(owner),
        json={"country_code": "CO", "token": TOKEN},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_mode"] == "api"
    assert body["has_token"] is True
    assert body["credential_status"] == "none"
    assert TOKEN not in response.text
    return body["connection_id"]


class _Page:
    def __init__(self, orders):
        self.orders = orders


def _fake_factory(behaviour):
    from connectors.dropi.client import DropiAuthError

    def factory(*, token, country_code):
        class Fake:
            def test_connection(self):
                if behaviour == "reject":
                    raise DropiAuthError("Dropi rechazó el token de integración (HTTP 401).")
                return _Page([{"id": 1}])
        return Fake()

    return factory


def test_the_token_never_comes_back(client, owner, co_connection):
    response = client.get(f"/config/dropi/connections/{co_connection}", headers=auth(owner))
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    assert "token" not in response.json()


def test_a_second_api_connection_for_the_same_country_points_at_the_first(
    client, owner, co_connection
):
    response = client.post(
        "/config/dropi/connections",
        headers=auth(owner),
        json={"country_code": "CO", "token": OTHER_TOKEN},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["detail"]["connection_id"] == co_connection


def test_a_good_token_tests_ok(client, owner, co_connection, monkeypatch):
    from api.routers import dropi

    monkeypatch.setattr(dropi, "client_factory", _fake_factory("accept"))
    response = client.post(
        f"/config/dropi/connections/{co_connection}/test", headers=auth(owner)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["credential_status"] == "ok"
    assert body["orders_visible"] is True


def test_a_rejected_token_is_recorded_and_answered_with_200(
    client, owner, co_connection, monkeypatch
):
    from api.routers import dropi

    monkeypatch.setattr(dropi, "client_factory", _fake_factory("reject"))
    response = client.post(
        f"/config/dropi/connections/{co_connection}/test", headers=auth(owner)
    )
    assert response.status_code == 200, response.text
    assert response.json()["credential_status"] == "invalid"

    status = client.get(f"/config/dropi/connections/{co_connection}", headers=auth(owner))
    assert status.json()["credential_status"] == "invalid"


def test_a_new_token_resets_the_status_to_unproven(client, owner, co_connection):
    response = client.put(
        f"/config/dropi/connections/{co_connection}/token",
        headers=auth(owner),
        json={"token": OTHER_TOKEN},
    )
    assert response.status_code == 200, response.text
    assert response.json()["credential_status"] == "none"
    assert OTHER_TOKEN not in response.text


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("get", "", None),
        ("put", "/token", {"token": OTHER_TOKEN}),
        ("post", "/test", None),
        ("delete", "/token", None),
    ],
)
def test_another_countrys_link_does_not_exist_for_a_scoped_partner(
    client, partner, co_connection, method, suffix, body
):
    url = f"/config/dropi/connections/{co_connection}{suffix}"
    kwargs = {"headers": auth(partner)}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(url, **kwargs)
    assert response.status_code == 404, response.text


def test_a_scoped_partner_cannot_create_one_outside_their_country(client, partner):
    response = client.post(
        "/config/dropi/connections",
        headers=auth(partner),
        json={"country_code": "CO", "token": TOKEN},
    )
    # The 409 for "already exists" must not leak across the scope either.
    assert response.status_code == 403, response.text


def test_a_file_connection_becomes_an_api_one_by_pasting_the_token(client, partner):
    created = client.post(
        "/config/connections",
        headers=auth(partner),
        json={"country_code": "GT", "platform_code": "dropi", "name": "Dropi GT"},
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connection_id"]

    response = client.put(
        f"/config/dropi/connections/{connection_id}/token",
        headers=auth(partner),
        json={"token": TOKEN},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_mode"] == "api"


def test_the_token_endpoints_refuse_a_connection_that_is_not_dropi(client, owner):
    created = client.post(
        "/config/connections",
        headers=auth(owner),
        json={"country_code": "CO", "platform_code": "ads_manual", "name": "Pauta CO"},
    )
    assert created.status_code == 201, created.text
    response = client.put(
        f"/config/dropi/connections/{created.json()['connection_id']}/token",
        headers=auth(owner),
        json={"token": TOKEN},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "not_a_dropi_connection"


def test_disconnecting_keeps_the_connection_and_goes_back_to_files(
    client, owner, co_connection
):
    response = client.delete(
        f"/config/dropi/connections/{co_connection}/token", headers=auth(owner)
    )
    assert response.status_code == 204, response.text

    status = client.get(f"/config/dropi/connections/{co_connection}", headers=auth(owner))
    assert status.status_code == 200
    assert status.json()["has_token"] is False
    assert status.json()["source_mode"] == "file"
