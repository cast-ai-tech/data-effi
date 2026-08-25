"""El modelo de negocio de cada acceso (046) y el aislamiento por país en los
endpoints que NO reciben `?country=`.

`_guard_country` (api/deps.py) rechaza `?country=CR` a quien solo puede ver
Guatemala y Honduras. Lo que estos tests afirman es lo que el guardia de la
puerta no alcanza a ver:

  - el consolidado /kpis/global viene recortado a [GT, HN]
  - una guía de CR pedida por id responde 404, no la guía
  - el cliente de CR pedido por hash responde 404
  - el catálogo de productos no lista lo que solo se vendió en CR
  - el copiloto exige nombrar un país del alcance
  - business_model viaja por la invitación, el PATCH y la lista, y cada fila
    de acceso dice a qué empresa pertenece
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "admin.aislamiento@dataeffi.co"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "operadora.gt.hn@dataeffi.co"
PARTNER_PASSWORD = "clave-de-la-operadora-1"
COUNTRIES = ("GT", "HN", "CR")
# Customer hashes are 64 hex characters; one fixed one per country.
HASHES = {"GT": "a1" * 32, "HN": "b2" * 32, "CR": "c3" * 32}
SCOPE = ["GT", "HN"]


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
    """Una sociedad que opera en Guatemala, Honduras y Costa Rica."""
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Administrador",
            "tenant_name": "Distribuidora Centroamérica",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    for code in COUNTRIES:
        activated = client.put(
            "/config/countries",
            headers=auth(body["access_token"]),
            json={"country_code": code, "is_active": True},
        )
        assert activated.status_code == 200, activated.text
    return body


@pytest.fixture(scope="module")
def connections(client, owner) -> dict[str, str]:
    created = {}
    for code in COUNTRIES:
        response = client.post(
            "/config/connections",
            headers=auth(owner["access_token"]),
            json={"platform_code": "ads_manual", "country_code": code, "name": f"Conn {code}"},
        )
        assert response.status_code == 201, response.text
        created[code] = response.json()["connection_id"]
    return created


@pytest.fixture(scope="module")
def guides(client, api_dsn, owner, connections) -> dict[str, str]:
    """Una guía entregada por país, cada una con su cliente y su producto."""
    tenant_id = owner["tenant_id"]
    ids: dict[str, str] = {}
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        for code in COUNTRIES:
            cur.execute(
                """
                INSERT INTO core.product (tenant_id, name, name_norm)
                VALUES (%s, %s, core.normalize_text(%s)) RETURNING id
                """,
                (tenant_id, f"Producto solo {code}", f"Producto solo {code}"),
            )
            product_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO core.shipment
                    (tenant_id, connection_id, country_code, tracking_number, customer_hash,
                     product_id, quantity, status_code, created_date, dispatched_batch_at,
                     delivered_at, currency_code, declared_value, cod_collected,
                     freight_cost, product_cost)
                VALUES (%s, %s, %s, %s, %s, %s, 1, 'delivered', %s, %s, %s, 'USD',
                        50, 50, 6, 12)
                RETURNING id
                """,
                (
                    tenant_id, connections[code], code, f"{code}-0001",
                    HASHES[code], product_id,
                    date(2026, 8, 1), date(2026, 8, 1), date(2026, 8, 4),
                ),
            )
            ids[code] = str(cur.fetchone()[0])
        conn.commit()
    return ids


@pytest.fixture(scope="module")
def partner(client, owner) -> dict:
    """La operadora de Guatemala y Honduras: owner, pero solo en esos dos."""
    invite = client.post(
        "/auth/invite",
        headers=auth(owner["access_token"]),
        json={
            "email": PARTNER_EMAIL,
            "role": "owner",
            "country_scope": SCOPE,
            "business_model": "ecommerce",
        },
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["business_model"] == "ecommerce"
    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": PARTNER_PASSWORD,
            "full_name": "Operadora GT/HN",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert sorted(accepted.json()["countries"]) == SCOPE
    return accepted.json()


# =============================================================================
# El administrador ve todo
# =============================================================================


def test_the_admin_sees_every_country(client, owner, guides):
    response = client.get("/kpis/global", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    assert {row["country_code"] for row in response.json()["rows"]} == set(COUNTRIES)


# =============================================================================
# La operadora de GT/HN nunca ve CR
# =============================================================================


KPI_PATHS = [
    "/kpis/carriers", "/kpis/geo", "/kpis/products", "/kpis/daily-status",
    "/kpis/platforms", "/kpis/contribution-split", "/kpis/aging", "/kpis/layout",
]


@pytest.mark.parametrize("path", KPI_PATHS)
def test_asking_for_costa_rica_is_refused_by_name(client, partner, guides, path):
    response = client.get(path, params={"country": "CR"}, headers=auth(partner["access_token"]))
    assert response.status_code == 403, response.text
    assert "GT" in response.text and "HN" in response.text


@pytest.mark.parametrize("path", KPI_PATHS)
def test_her_own_countries_still_answer(client, partner, guides, path):
    for code in SCOPE:
        response = client.get(path, params={"country": code}, headers=auth(partner["access_token"]))
        assert response.status_code == 200, (path, code, response.text)


def test_the_global_rollup_comes_back_trimmed(client, partner, guides):
    response = client.get("/kpis/global", headers=auth(partner["access_token"]))
    assert response.status_code == 200, response.text
    assert {row["country_code"] for row in response.json()["rows"]} == set(SCOPE)


def test_a_guide_from_costa_rica_is_not_found_by_id(client, partner, guides):
    """404 y no 403: que la guía exista en CR no es algo que deba averiguar."""
    response = client.get(f"/orders/{guides['CR']}", headers=auth(partner["access_token"]))
    assert response.status_code == 404, response.text
    mine = client.get(f"/orders/{guides['GT']}", headers=auth(partner["access_token"]))
    assert mine.status_code == 200, mine.text


def test_orders_and_customers_of_costa_rica_are_refused(client, partner, guides):
    for path in ("/orders", "/customers"):
        response = client.get(path, params={"country": "CR"}, headers=auth(partner["access_token"]))
        assert response.status_code == 403, (path, response.text)


def test_a_customer_from_costa_rica_is_not_found_by_hash(client, partner, guides):
    cr_hash = HASHES["CR"]
    response = client.get(f"/customers/{cr_hash}", headers=auth(partner["access_token"]))
    assert response.status_code == 404, response.text
    gt_hash = HASHES["GT"]
    mine = client.get(f"/customers/{gt_hash}", headers=auth(partner["access_token"]))
    assert mine.status_code == 200, mine.text


def test_the_product_catalogue_hides_what_only_costa_rica_sold(client, partner, owner, guides):
    everything = client.get("/products", headers=auth(owner["access_token"]))
    assert everything.status_code == 200, everything.text
    assert {row["product_name"] for row in everything.json()} >= {
        "Producto solo GT", "Producto solo HN", "Producto solo CR",
    }

    trimmed = client.get("/products", headers=auth(partner["access_token"]))
    assert trimmed.status_code == 200, trimmed.text
    names = {row["product_name"] for row in trimmed.json()}
    assert "Producto solo CR" not in names
    assert {"Producto solo GT", "Producto solo HN"} <= names


def test_alerts_and_recommendations_without_a_country_are_trimmed(client, partner, owner, guides):
    """`country` is optional here, so the door guard sees nothing: the rows
    themselves must be cut to the scope."""
    for path in ("/ai/alerts", "/ai/recommendations"):
        response = client.get(path, headers=auth(partner["access_token"]))
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        items = body.get("alerts") if "alerts" in body else body.get("recommendations", [])
        for item in items:
            assert item["country_code"] in SCOPE, (path, item)


def test_a_product_only_sold_in_costa_rica_is_not_found_by_id(client, partner, owner, guides):
    everything = client.get("/products", headers=auth(owner["access_token"])).json()
    by_name = {row["product_name"]: row["product_id"] for row in everything}
    foreign = client.get(f"/products/{by_name['Producto solo CR']}", headers=auth(partner["access_token"]))
    assert foreign.status_code == 404, foreign.text
    mine = client.get(f"/products/{by_name['Producto solo GT']}", headers=auth(partner["access_token"]))
    assert mine.status_code == 200, mine.text


def test_the_copilot_demands_one_of_her_countries(client, partner, guides):
    headers = auth(partner["access_token"])
    without = client.post("/ai/ask", json={"question": "¿Cuántas guías entregué?"}, headers=headers)
    assert without.status_code == 403, without.text
    foreign = client.post(
        "/ai/ask", json={"question": "¿Cuántas guías entregué?", "country_code": "CR"}, headers=headers
    )
    assert foreign.status_code == 403, foreign.text


# =============================================================================
# Modelo de negocio y empresa en cada acceso
# =============================================================================


def test_every_access_says_its_company_and_its_business_model(client, owner, partner):
    response = client.get("/config/users", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    rows = {row["email"]: row for row in response.json()}
    assert rows[PARTNER_EMAIL]["business_model"] == "ecommerce"
    assert rows[PARTNER_EMAIL]["tenant_name"] == "Distribuidora Centroamérica"
    assert rows[PARTNER_EMAIL]["tenant_id"] == owner["tenant_id"]
    assert sorted(rows[PARTNER_EMAIL]["country_scope"]) == SCOPE
    # The admin never said which side they are on: null, not a guess.
    assert rows[OWNER_EMAIL]["business_model"] is None
    assert rows[OWNER_EMAIL]["tenant_name"] == "Distribuidora Centroamérica"


def test_the_business_model_and_the_countries_can_be_changed(client, owner, partner):
    tenant_id = owner["tenant_id"]
    user_id = partner["user_id"] if "user_id" in partner else None
    if user_id is None:
        me = client.get("/auth/me", headers=auth(partner["access_token"]))
        user_id = me.json()["id"]

    changed = client.patch(
        f"/org/tenants/{tenant_id}/members/{user_id}",
        headers=auth(owner["access_token"]),
        json={"business_model": "proveeduria", "country_scope": ["GT", "HN", "CR"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["business_model"] == "proveeduria"
    assert sorted(changed.json()["country_scope"]) == ["CR", "GT", "HN"]
    assert changed.json()["tenant_name"] == "Distribuidora Centroamérica"

    # Back to two countries: the share is never touched by any of this.
    back = client.patch(
        f"/org/tenants/{tenant_id}/members/{user_id}",
        headers=auth(owner["access_token"]),
        json={"country_scope": SCOPE},
    )
    assert back.status_code == 200, back.text
    assert back.json()["business_model"] == "proveeduria"
    assert back.json()["share_pct"] is None


def test_an_unknown_business_model_is_refused(client, owner, partner):
    me = client.get("/auth/me", headers=auth(partner["access_token"]))
    user_id = me.json()["id"]
    response = client.patch(
        f"/org/tenants/{owner['tenant_id']}/members/{user_id}",
        headers=auth(owner["access_token"]),
        json={"business_model": "retail"},
    )
    assert response.status_code == 422, response.text
