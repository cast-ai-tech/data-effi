"""The free month, the plans and the door (migration 048).

  - registering is open: every registration is its own organisation on a
    30-day trial for ONE company
  - the second company is refused (402 plan_limit) until a bigger plan is active
  - choosing a plan records it as pending; an advisor activates it
  - when the free month ends with no active plan, every data endpoint answers
    402 and /billing, /auth/me still open
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

A_EMAIL = "dueno.a@masterdata.app"
B_EMAIL = "dueno.b@masterdata.app"
PASSWORD = "una-clave-larga-de-prueba"


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
    os.environ["ADVISOR_WHATSAPP"] = "+57 300 123 4567"
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)

    from api.settings import get_settings

    get_settings.cache_clear()

    from api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
    os.environ.pop("ADVISOR_WHATSAPP", None)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str, company: str) -> dict:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Dueño", "tenant_name": company},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def owner_a(client) -> dict:
    return register(client, A_EMAIL, "Comercial Andina")


def test_registering_starts_a_free_month_for_one_company(client, owner_a):
    me = client.get("/auth/me", headers=auth(owner_a["access_token"])).json()
    sub = me["subscription"]
    assert sub["status"] == "trial"
    assert sub["blocked"] is False
    assert sub["max_tenants"] == 1
    assert sub["tenants_used"] == 1
    assert 28 <= sub["days_left"] <= 30


def test_a_second_registration_is_its_own_organisation(client, owner_a):
    """Two operators may even call their company the same thing."""
    owner_b = register(client, B_EMAIL, "Comercial Andina")
    me_b = client.get("/auth/me", headers=auth(owner_b["access_token"])).json()
    me_a = client.get("/auth/me", headers=auth(owner_a["access_token"])).json()
    assert me_b["org_id"] != me_a["org_id"]
    assert me_b["tenant_id"] != me_a["tenant_id"]
    assert me_b["subscription"]["status"] == "trial"


def test_the_same_email_cannot_register_twice(client, owner_a):
    response = client.post(
        "/auth/register",
        json={"email": A_EMAIL, "password": PASSWORD, "full_name": "Otra", "tenant_name": "Otra"},
    )
    assert response.status_code == 409, response.text


def test_registering_without_a_company_then_creating_it_with_its_country(client):
    """The simplified flow: account first, then "crea tu empresa" with ONE country."""
    response = client.post(
        "/auth/register",
        json={"email": "solo.cuenta@masterdata.app", "password": PASSWORD, "full_name": "Solo Cuenta"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    assert response.json()["tenant_id"] is None

    me = client.get("/auth/me", headers=auth(token)).json()
    assert me["tenant_id"] is None and me["workspaces"] == []
    assert me["subscription"]["status"] == "trial"
    assert me["subscription"]["tenants_used"] == 0
    assert me["is_org_admin"] is True

    created = client.post(
        "/org/tenants", json={"name": "Distrilatam Ecuador", "countries": ["EC"]}, headers=auth(token)
    )
    assert created.status_code == 201, created.text
    assert created.json()["countries"] == ["EC"]

    switched = client.post(
        "/auth/switch", json={"tenant_id": created.json()["tenant_id"]}, headers=auth(token)
    )
    assert switched.status_code == 200, switched.text
    token2 = switched.json()["access_token"]
    me2 = client.get("/auth/me", headers=auth(token2)).json()
    assert me2["tenant_id"] == created.json()["tenant_id"]
    assert me2["role"] == "owner"
    assert me2["subscription"]["tenants_used"] == 1
    assert [ws["countries"] for ws in me2["workspaces"]] == [["EC"]]

    # The free month holds one company: the second is refused by the plan.
    second = client.post(
        "/org/tenants", json={"name": "Distrilatam Colombia", "countries": ["CO"]}, headers=auth(token2)
    )
    assert second.status_code == 402, second.text


def test_the_free_month_holds_one_company(client, owner_a):
    response = client.post(
        "/org/tenants", json={"name": "Segunda empresa", "countries": []},
        headers=auth(owner_a["access_token"]),
    )
    assert response.status_code == 402, response.text
    assert response.json()["error"]["code"] == "plan_limit"


def test_the_plans_screen_lists_the_four_offers_and_the_advisor(client, owner_a):
    response = client.get("/billing", headers=auth(owner_a["access_token"]))
    assert response.status_code == 200, response.text
    body = response.json()
    plans = {plan["code"]: plan for plan in body["plans"]}
    assert plans["master"]["price_usd"] == 29 and plans["master"]["max_tenants"] == 1
    assert plans["master_pro"]["price_usd"] == 59 and plans["master_pro"]["max_tenants"] == 3
    assert plans["master_elite"]["price_usd"] == 99 and plans["master_elite"]["max_tenants"] == 6
    assert plans["custom"]["is_custom"] is True and plans["custom"]["price_usd"] is None
    assert body["advisor_whatsapp_url"].startswith("https://wa.me/573001234567?text=")
    assert body["can_choose"] is True


def test_choosing_a_plan_is_pending_until_an_advisor_activates_it(client, owner_a):
    chosen = client.post(
        "/billing/choose", json={"plan_code": "master_pro"}, headers=auth(owner_a["access_token"])
    )
    assert chosen.status_code == 200, chosen.text
    sub = chosen.json()["subscription"]
    assert sub["status"] == "pending"
    assert sub["requested_plan_code"] == "master_pro"
    assert sub["blocked"] is False
    # Still one company: a request is not a plan.
    assert sub["max_tenants"] == 1

    unknown = client.post(
        "/billing/choose", json={"plan_code": "platino"}, headers=auth(owner_a["access_token"])
    )
    assert unknown.status_code == 404


def test_when_the_free_month_ends_the_door_closes(client, api_dsn, owner_a):
    me = client.get("/auth/me", headers=auth(owner_a["access_token"])).json()
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE core.org_subscription SET trial_ends_at = now() - interval '1 day' WHERE org_id = %s",
            (me["org_id"],),
        )
        conn.commit()

    headers = auth(owner_a["access_token"])
    for path in ("/config/countries", "/kpis/global"):
        response = client.get(path, headers=headers)
        assert response.status_code == 402, (path, response.text)
        assert response.json()["error"]["code"] == "subscription_required"

    # What stays open: who am I, and the plans.
    still_me = client.get("/auth/me", headers=headers)
    assert still_me.status_code == 200
    assert still_me.json()["subscription"]["blocked"] is True
    billing = client.get("/billing", headers=headers)
    assert billing.status_code == 200
    assert "mes gratis terminó" in billing.json()["subscription"]["message"]


def test_the_advisor_activates_the_plan_and_the_limit_grows(client, api_dsn, owner_a):
    from api.billing import activate_plan

    me = client.get("/auth/me", headers=auth(owner_a["access_token"])).json()
    with psycopg.connect(api_dsn) as conn:
        state = activate_plan(conn, me["org_id"], "master_pro", months=1)
        conn.commit()
    assert state.status == "active" and state.max_tenants == 3 and not state.blocked

    headers = auth(owner_a["access_token"])
    assert client.get("/config/countries", headers=headers).status_code == 200

    second = client.post("/org/tenants", json={"name": "Segunda empresa", "countries": []}, headers=headers)
    assert second.status_code == 201, second.text
    third = client.post("/org/tenants", json={"name": "Tercera empresa", "countries": []}, headers=headers)
    assert third.status_code == 201, third.text
    fourth = client.post("/org/tenants", json={"name": "Cuarta empresa", "countries": []}, headers=headers)
    assert fourth.status_code == 402, fourth.text


def test_an_expired_plan_closes_the_door_again(client, api_dsn, owner_a):
    from api.billing import expire_subscription

    me = client.get("/auth/me", headers=auth(owner_a["access_token"])).json()
    with psycopg.connect(api_dsn) as conn:
        expire_subscription(conn, me["org_id"], notes="prueba")
        conn.commit()
    response = client.get("/config/countries", headers=auth(owner_a["access_token"]))
    assert response.status_code == 402
