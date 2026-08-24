"""End-to-end API tests: the whole onboarding path a real user walks.

register -> login -> activate a country -> create a connection -> upload the Effi
fixture -> read the KPIs back. If this suite passes, the platform works.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@dataeffi.co"
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
    """A TestClient wired to the throwaway test database."""
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
            "full_name": "Dueño de Prueba",
            "tenant_name": "Operación Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# Health and auth
# =============================================================================


def test_health_is_public_and_reports_the_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_protected_endpoints_reject_anonymous_callers(client):
    for path in ("/config/countries", "/kpis/global", "/ingest/batches", "/auth/me"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized"


def test_register_creates_the_first_owner(owner_token):
    assert owner_token


def test_register_refuses_a_second_time(client, owner_token):
    response = client.post(
        "/auth/register",
        json={
            "email": "otro@dataeffi.co",
            "password": "otra-clave-larga-1234",
            "full_name": "Otro",
            "tenant_name": "Otra empresa",
        },
    )
    assert response.status_code == 403
    assert "invitación" in response.json()["error"]["message"]


def test_login_returns_tokens(client, owner_token):
    response = client.post(
        "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


def test_login_with_a_wrong_password_is_rejected(client, owner_token):
    response = client.post("/auth/login", json={"email": OWNER_EMAIL, "password": "incorrecta"})
    assert response.status_code == 401
    # The message must not reveal whether the account exists.
    assert response.json()["error"]["message"] == "Correo o contraseña incorrectos"


def test_login_with_an_unknown_email_gives_the_same_message(client, owner_token):
    response = client.post(
        "/auth/login", json={"email": "nadie@dataeffi.co", "password": "loquesea1234"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Correo o contraseña incorrectos"


def test_refresh_rotates_the_token(client, owner_token):
    login = client.post(
        "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    ).json()

    first = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert first.status_code == 200

    # The old token is dead the moment it is used.
    second = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert second.status_code == 401


def test_me_returns_the_caller(client, owner_token):
    response = client.get("/auth/me", headers=auth(owner_token))
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == OWNER_EMAIL
    assert body["role"] == "owner"
    assert body["tenant_name"] == "Operación Demo"


def test_a_viewer_cannot_create_connections(client, owner_token):
    invite = client.post(
        "/auth/invite", json={"email": "viewer@dataeffi.co", "role": "viewer"},
        headers=auth(owner_token),
    )
    assert invite.status_code == 201
    token = invite.json()["invitation_token"]

    accepted = client.post(
        "/auth/accept-invite",
        json={"token": token, "password": "clave-de-viewer-1234", "full_name": "Solo Lectura"},
    )
    assert accepted.status_code == 200
    viewer_token = accepted.json()["access_token"]

    response = client.post(
        "/config/connections",
        json={"platform_code": "manual_xlsx", "name": "intento"},
        headers=auth(viewer_token),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# =============================================================================
# Onboarding
# =============================================================================


def test_countries_list_marks_inactive_by_default(client, owner_token):
    response = client.get("/config/countries", headers=auth(owner_token))
    assert response.status_code == 200
    countries = {c["code"]: c for c in response.json()}
    assert "CO" in countries
    assert countries["CO"]["currency_code"] == "COP"
    assert countries["CO"]["decimal_places"] == 0
    assert countries["MX"]["decimal_places"] == 2
    assert countries["CO"]["is_active"] is False


def test_activating_a_country(client, owner_token):
    response = client.put(
        "/config/countries",
        json={"country_code": "CO", "is_active": True, "maturation_days": 21},
        headers=auth(owner_token),
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert response.json()["maturation_days"] == 21


def test_platforms_are_country_specific(client, owner_token):
    response = client.get("/config/platforms?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    codes = {p["platform_code"] for p in response.json()}
    assert "effi" in codes and "dropi" in codes and "manual_xlsx" in codes

    effi = next(p for p in response.json() if p["platform_code"] == "effi")
    assert effi["tier"] == 3
    assert effi["requires_consent"] is True


def test_tier3_connection_without_consent_is_refused(client, owner_token):
    response = client.post(
        "/config/connections",
        json={
            "country_code": "CO",
            "platform_code": "effi",
            "name": "Effi sin consentimiento",
            "consent_granted": False,
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "consent_required"
    assert "tier3-politica.md" in body["error"]["detail"]["docs"]


@pytest.fixture(scope="module")
def connection_id(client, owner_token) -> str:
    client.put(
        "/config/countries",
        json={"country_code": "CO", "is_active": True},
        headers=auth(owner_token),
    )
    response = client.post(
        "/config/connections",
        json={
            # Manual upload is a GLOBAL platform since migration 012: no country
            # here, because the file itself says which country it is about.
            "platform_code": "manual_xlsx",
            "name": "Carga manual",
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["country_code"] is None
    return response.json()["connection_id"]


def test_connection_starts_never_synced(client, owner_token, connection_id):
    # A global connection belongs to the workspace, not to one country, so it is
    # `scope`, not `country`, that finds it.
    response = client.get("/config/connections?scope=global", headers=auth(owner_token))
    assert response.status_code == 200
    connection = next(c for c in response.json() if c["connection_id"] == connection_id)
    assert connection["status"] == "active"
    assert connection["health"] == "never_synced"


# =============================================================================
# Ingestion through the API
# =============================================================================


def _wait_for_job(client, token, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/ingest/jobs/{job_id}", headers=auth(token))
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("done", "failed", "duplicate"):
            return job
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_upload_ingests_the_fixture(client, owner_token, connection_id, guias_dia1):
    response = client.post(
        "/ingest/upload",
        data={"connection_id": connection_id, "kind": "shipments"},
        files={"files": ("effi_guias_dia1.csv", guias_dia1, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text
    jobs = response.json()["jobs"]
    assert len(jobs) == 1

    job = _wait_for_job(client, owner_token, jobs[0]["id"])
    assert job["status"] == "done", job
    assert job["batch_id"]

    detail = client.get(f"/ingest/batches/{job['batch_id']}", headers=auth(owner_token))
    assert detail.status_code == 200
    batch = detail.json()["batch"]
    assert batch["rows_total"] == 10
    assert batch["rows_inserted"] == 10
    assert batch["rows_failed"] == 0


def test_uploading_the_same_file_again_is_a_duplicate(
    client, owner_token, connection_id, guias_dia1
):
    response = client.post(
        "/ingest/upload",
        data={"connection_id": connection_id, "kind": "shipments"},
        files={"files": ("otro_nombre.csv", guias_dia1, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202
    job = _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert job["status"] == "duplicate"


def test_upload_rejects_an_unsupported_format(client, owner_token, connection_id):
    response = client.post(
        "/ingest/upload",
        data={"connection_id": connection_id, "kind": "shipments"},
        files={"files": ("reporte.pdf", b"%PDF-1.4", "application/pdf")},
        headers=auth(owner_token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_file"


def test_upload_rejects_a_connection_from_another_workspace(client, owner_token):
    response = client.post(
        "/ingest/upload",
        data={"connection_id": "00000000-0000-0000-0000-000000000000", "kind": "shipments"},
        files={"files": ("x.csv", b"Guia;Fecha\nE-1;01/07/2026\n", "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 404


def test_batch_history_lists_the_load(client, owner_token):
    response = client.get("/ingest/batches?page=1&page_size=10", headers=auth(owner_token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["items"][0]["rows_inserted"] >= 0


# =============================================================================
# KPIs
# =============================================================================


def test_daily_contribution_matches_the_fixture(client, owner_token):
    response = client.get("/kpis/daily-contribution?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    body = response.json()
    # No range asked for, so the whole history - but the response still names the
    # date it WOULD have filtered on, which is what the UI puts on the widget.
    assert body["date_basis"] == "creacion"
    assert body["date_from"] is None and body["date_to"] is None
    rows = body["rows"]
    assert rows

    totals = {
        "shipments": sum(r["shipments"] for r in rows),
        "delivered": sum(r["delivered"] for r in rows),
        "returned": sum(r["returned"] for r in rows),
    }
    assert totals == {"shipments": 10, "delivered": 5, "returned": 2}
    assert all(r["ad_spend_missing"] for r in rows), "no ads connection: must be flagged"
    assert rows[0]["currency_code"] == "COP"


def test_carriers_endpoint(client, owner_token):
    response = client.get("/kpis/carriers?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    carriers = {c["carrier_name"]: c for c in response.json()["rows"]}
    assert carriers["Interrapidisimo"]["shipments"] == 4
    assert Decimal(carriers["Interrapidisimo"]["delivery_rate_pct"]) == Decimal("75.00")


def test_geo_endpoint_collapses_city_spellings(client, owner_token):
    response = client.get("/kpis/geo?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    cities = [g["city_name"] for g in response.json()["rows"]]
    bogota_rows = [c for c in cities if c.lower().startswith("bogot")]
    assert len(bogota_rows) == 1, f"Bogotá must be one row, got {bogota_rows}"


def test_products_endpoint(client, owner_token):
    response = client.get("/kpis/products?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    names = {p["product_name"] for p in response.json()["rows"]}
    assert names == {"Faja Reductora", "Reloj Inteligente"}


def test_aging_endpoint(client, owner_token):
    response = client.get("/kpis/aging?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    buckets = response.json()["rows"]
    assert all(r["aging_bucket"] in ("0-3", "4-7", "8-12", "13-20", "21+") for r in buckets)


def test_cpa_is_empty_without_an_ads_connection(client, owner_token):
    response = client.get("/kpis/cpa?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    # Empty, and still able to say what it would have filtered on. That is the
    # whole reason date_basis lives on the response and not on each row.
    assert body["date_basis"] == "pauta"


def test_layout_blocks_cpa_and_cs(client, owner_token):
    response = client.get("/kpis/layout?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    widgets = {w["widget_code"]: w for w in response.json()["widgets"]}

    assert widgets["cpa_roas"]["state"] == "blocked"
    assert widgets["cpa_roas"]["missing_required"] == ["ads"]
    assert "pauta" in widgets["cpa_roas"]["state_message"].lower()

    assert widgets["cs_confirmation"]["state"] == "blocked"
    assert widgets["carrier_table"]["state"] == "available"

    # A blocked widget is still returned. The UI must show it, greyed out.
    assert len(widgets) >= 10


def test_global_endpoint(client, owner_token):
    response = client.get("/kpis/global", headers=auth(owner_token))
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["country_code"] == "CO"
    assert rows[0]["shipments"] == 10
    assert rows[0]["fx_missing"] is True      # no FX loaded in the test database


# =============================================================================
# AI layer degradation
# =============================================================================


def test_brief_degrades_cleanly_when_ai_is_disabled(client, owner_token):
    response = client.get("/ai/brief?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["summary"]      # a real message, not an empty string


def test_ask_degrades_cleanly_when_ai_is_disabled(client, owner_token):
    response = client.post(
        "/ai/ask", json={"question": "¿cuánto vendí?"}, headers=auth(owner_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rejected"] is True
    assert body["suggestions"], "a refusal must offer something to try instead"


def test_alerts_work_without_the_model(client, owner_token):
    """Alerts are deterministic SQL: they must work with AI switched off."""
    response = client.get("/ai/alerts?country=CO", headers=auth(owner_token))
    assert response.status_code == 200
    assert "alerts" in response.json()


def test_decisions_work_without_the_model(client, owner_token):
    for scope in ("products", "carriers", "office", "cash"):
        response = client.get(
            f"/ai/decisions?country=CO&scope={scope}", headers=auth(owner_token)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == scope
        assert body["degraded"] is False
        assert all(
            item["verdict"] in ("keep", "cut", "watch", "switch", "call", "hold", "ok")
            for item in body["items"]
        )


def test_carrier_by_zone_endpoint(client, owner_token):
    response = client.get("/kpis/carrier-by-zone?country=CO", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    body = response.json()
    # Same envelope as every other KPI; `date_basis` null says no range applies.
    assert body["date_basis"] is None
    rows = body["rows"]
    assert isinstance(rows, list)
    # The view is a rolling 90-day window, so the fixture's July guides drop
    # out of it with time; the shape is what this pins, not the count.
    for row in rows:
        assert {"carrier_name", "city_name", "delivery_rate_pct", "terminal"} <= set(row)


# =============================================================================
# Notifications and the event feed
# =============================================================================


def test_events_without_a_cursor_returns_the_cursor_only(client, owner_token):
    response = client.get("/events", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["events"] == []
    # The uploads earlier in this module already appended events.
    assert body["cursor"] >= 1


def test_events_after_the_cursor_come_back_in_order(client, owner_token):
    response = client.get("/events?since=0&wait=0", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert events, "the uploads must have emitted upload_job.updated"
    ids = [event["id"] for event in events]
    assert ids == sorted(ids)
    assert {"upload_job.updated", "batch.finished"} <= {event["type"] for event in events}
    assert response.json()["cursor"] == ids[-1]


def test_events_refuse_a_wait_past_the_ceiling(client, owner_token):
    response = client.get("/events?since=0&wait=30", headers=auth(owner_token))
    assert response.status_code == 422


def test_notifications_list_and_counts(client, owner_token):
    response = client.get("/notifications?country=CO", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"items", "unread_count", "critical_unread_count", "next_before"} <= set(body)

    counts = client.get("/notifications/unread-count", headers=auth(owner_token))
    assert counts.status_code == 200
    assert counts.json()["unread_count"] == body["unread_count"]

    marked = client.post("/notifications/read-all?country=CO", headers=auth(owner_token))
    assert marked.status_code == 200
    assert marked.json()["marked"] == body["unread_count"]


def test_thresholds_round_trip(client, owner_token):
    response = client.get("/notifications/thresholds?country=CO", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    assert response.json()["country_code"] == "CO"

    put = client.put(
        "/notifications/thresholds?country=CO",
        json={"thresholds": {"efectividad_tipica_pct": "60"}},
        headers=auth(owner_token),
    )
    assert put.status_code == 200, put.text
    rows = {row["key"]: row for row in put.json()["thresholds"]}
    assert rows["efectividad_tipica_pct"]["value"] == "60"
    assert rows["efectividad_tipica_pct"]["source"] == "user"

    bad = client.put(
        "/notifications/thresholds?country=CO",
        json={"thresholds": {"no_existe": "1"}},
        headers=auth(owner_token),
    )
    assert bad.status_code == 400

    reset = client.put(
        "/notifications/thresholds?country=CO",
        json={"thresholds": {"efectividad_tipica_pct": ""}},
        headers=auth(owner_token),
    )
    assert reset.status_code == 200
    assert all(
        row["source"] != "user" for row in reset.json()["thresholds"]
    ), "an empty value must remove the hand-set threshold"


# =============================================================================
# Worker webhook
# =============================================================================


def test_worker_trigger_requires_the_secret(client):
    response = client.post("/worker/trigger/relink_orphans")
    assert response.status_code == 401


def test_worker_trigger_rejects_a_wrong_secret(client):
    response = client.post(
        "/worker/trigger/relink_orphans", headers={"X-Worker-Secret": "incorrecto"}
    )
    assert response.status_code == 401


def test_worker_trigger_runs_a_known_job(client):
    secret = os.environ["WORKER_TRIGGER_SECRET"]
    response = client.post(
        "/worker/trigger/relink_orphans", headers={"X-Worker-Secret": secret}
    )
    assert response.status_code == 200
    assert response.json()["job"] == "relink_orphans"


def test_worker_trigger_rejects_an_unknown_job(client):
    secret = os.environ["WORKER_TRIGGER_SECRET"]
    response = client.post(
        "/worker/trigger/rm_minus_rf", headers={"X-Worker-Secret": secret}
    )
    assert response.status_code == 404


# =============================================================================
# Error envelope and security headers
# =============================================================================


def test_errors_use_a_uniform_envelope(client, owner_token):
    response = client.get("/kpis/carriers", headers=auth(owner_token))      # missing ?country
    assert response.status_code == 422
    body = response.json()
    assert set(body["error"].keys()) == {"code", "message", "detail"}
    assert body["error"]["code"] == "validation_error"


def test_security_headers_are_present(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_openapi_is_served(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Data Effi API"
    assert "/kpis/layout" in schema["paths"]
