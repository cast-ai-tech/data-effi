"""Scope rules for connections: a country, no country, or not yet at all.

Migration 012 split the catalogue in two. A country-scoped platform is one
account in one country - a Meta ad account, an Effi account, a Shopify store. A
global one belongs to the whole workspace because the file it receives says
which country it is about. Getting that wrong is not a validation nicety: a
manual upload pinned to Colombia would quietly refuse an Ecuadorian report.

The database enforces it with a trigger, so every path is covered. The API says
the same thing first, because only the API can also say what to do instead.
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import admin_test_dsn, recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "scope@masterdata.app"
OWNER_PASSWORD = "una-clave-larga-de-prueba"


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


@pytest.fixture(scope="module")
def owner_token(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña de Prueba",
            "tenant_name": "Operación Alcance",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]

    activated = client.put(
        "/config/countries",
        json={"country_code": "CO", "is_active": True},
        headers=auth(token),
    )
    assert activated.status_code == 200, activated.text
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# The catalogue tells you which rule applies
# =============================================================================


def test_the_catalogue_lists_everything_including_what_does_not_work_yet(client, owner_token):
    response = client.get("/config/platforms", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    platforms = {p["platform_code"]: p for p in response.json()}

    assert platforms["manual_xlsx"]["scope"] == "global"
    assert platforms["effi"]["scope"] == "country"
    assert platforms["webhook_generic"]["availability"] == "available"
    assert platforms["google_sheets"]["availability"] == "beta"

    planned = platforms["shopify"]
    assert planned["availability"] == "planned"
    assert planned["setup_hint"], "una integración planeada tiene que decir qué necesitará"
    assert planned["category"] == "tienda"


# =============================================================================
# A global platform refuses a country
# =============================================================================


def test_a_global_platform_refuses_a_country(client, owner_token):
    response = client.post(
        "/config/connections",
        json={
            "country_code": "CO",
            "platform_code": "manual_xlsx",
            "name": "Carga manual con país",
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 400, response.text
    body = response.json()["error"]
    assert body["code"] == "country_not_allowed"
    assert "global" in body["message"]


def test_a_global_platform_is_created_without_a_country(client, owner_token):
    response = client.post(
        "/config/connections",
        json={"platform_code": "manual_xlsx", "name": "Carga manual"},
        headers=auth(owner_token),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["country_code"] is None
    assert created["scope"] == "global"
    assert created["has_webhook"] is False


def test_the_scope_filter_separates_the_two_kinds(client, owner_token):
    globals_only = client.get("/config/connections?scope=global", headers=auth(owner_token))
    assert globals_only.status_code == 200
    codes = {c["platform_code"] for c in globals_only.json()}
    assert "manual_xlsx" in codes
    assert all(c["country_code"] is None for c in globals_only.json())

    # A global connection belongs to the workspace, not to Colombia, so the
    # country filter must not return it.
    by_country = client.get("/config/connections?country=CO", headers=auth(owner_token))
    assert by_country.status_code == 200
    assert "manual_xlsx" not in {c["platform_code"] for c in by_country.json()}


# =============================================================================
# A country platform refuses a NULL country
# =============================================================================


def test_a_country_platform_refuses_a_null_country(client, owner_token):
    response = client.post(
        "/config/connections",
        json={"platform_code": "dropi", "name": "Dropi sin país"},
        headers=auth(owner_token),
    )
    assert response.status_code == 400, response.text
    body = response.json()["error"]
    assert body["code"] == "country_required"
    assert "país" in body["message"]


def test_a_country_platform_is_created_with_its_country(client, owner_token):
    response = client.post(
        "/config/connections",
        json={"country_code": "CO", "platform_code": "dropi", "name": "Dropi Colombia"},
        headers=auth(owner_token),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["country_code"] == "CO"
    assert created["scope"] == "country"


# =============================================================================
# A planned platform refuses creation, and says what it will need
# =============================================================================


def test_a_planned_platform_refuses_creation_naming_what_it_needs(client, owner_token):
    response = client.post(
        "/config/connections",
        json={"country_code": "CO", "platform_code": "shopify", "name": "Mi tienda"},
        headers=auth(owner_token),
    )
    assert response.status_code == 400, response.text
    body = response.json()["error"]
    assert body["code"] == "platform_planned"
    assert "todavía no está disponible" in body["message"]
    # The useful half of the answer: what it will take.
    assert "token de app privada" in body["message"]
    assert body["detail"]["setup_hint"]


# =============================================================================
# The same rules hold below the API, where the trigger lives
# =============================================================================


def _tenant_id(dsn: str):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM core.tenant ORDER BY created_at LIMIT 1")
        return cur.fetchone()[0]


def test_the_trigger_refuses_the_same_three_things(client, owner_token):
    """Straight into the table, as psql would. The rule is not the API's alone."""
    dsn = admin_test_dsn()
    if not dsn:
        pytest.skip("POSTGRES_ADMIN_URL is not available")

    tenant_id = _tenant_id(dsn)
    attempts = [
        ("manual_xlsx", "CO", "global"),       # global platform, country given
        ("dropi", None, "necesita un país"),   # country platform, no country
        ("shopify", "CO", "no está disponible"),  # planned platform
    ]

    for index, (platform_code, country_code, expected) in enumerate(attempts):
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
                cur.execute(
                    """
                    INSERT INTO core.connection
                        (tenant_id, country_code, platform_code, name, status)
                    VALUES (%s, %s, %s, %s, 'active')
                    """,
                    (tenant_id, country_code, platform_code, f"directo-{index}"),
                )
            assert expected in str(excinfo.value)
