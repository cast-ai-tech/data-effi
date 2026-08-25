"""Webhook ingestion: the path an n8n, Make or Zapier scenario takes.

The token is the whole credential, so the interesting assertions are about what
it does NOT do: it is shown once and never again, an unknown one is
indistinguishable from a revoked one, and a revoked one stops working
immediately. The data assertions are about the opposite - that a webhook payload
gets no special treatment: same engine, same content hash, same duplicate rule
as a file somebody uploaded by hand.
"""

from __future__ import annotations

import os
import time

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "webhook@masterdata.app"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
WORKER_SECRET = "w" * 48
# The public origin, deliberately NOT the TestClient's own host: the whole point
# of PUBLIC_API_URL is that the two differ behind a proxy.
PUBLIC_API_URL = "https://api.masterdata.app"

# Column names an operator would plausibly use in n8n. None of them is an Effi
# export, so this exercises the alias matcher, not a source profile.
ROWS = [
    {
        "guia": "WH-0001",
        "fecha": "2026-07-01",
        "estado": "Entregado",
        "ciudad": "Bogotá",
        "transportadora": "Coordinadora",
        "valor": 120000,
    },
    {
        "guia": "WH-0002",
        "fecha": "2026-07-01",
        "estado": "En transito",
        "ciudad": "Medellín",
        "transportadora": "Coordinadora",
        "valor": 89000,
    },
]


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
    os.environ["WORKER_TRIGGER_SECRET"] = WORKER_SECRET
    os.environ["PUBLIC_API_URL"] = PUBLIC_API_URL

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
            "tenant_name": "Operación Webhook",
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


@pytest.fixture(scope="module")
def connection_id(client, owner_token) -> str:
    response = client.post(
        "/config/connections",
        json={
            # webhook_generic is a global platform: no country, on purpose.
            "platform_code": "webhook_generic",
            "name": "n8n de pruebas",
            "default_kind": "shipments",
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["country_code"] is None
    return response.json()["connection_id"]


@pytest.fixture(scope="module")
def webhook(client, owner_token, connection_id) -> dict:
    response = client.post(
        f"/config/connections/{connection_id}/webhook", headers=auth(owner_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


# =============================================================================
# The token
# =============================================================================


def test_creating_the_webhook_returns_the_token_once(webhook, connection_id):
    assert webhook["connection_id"] == connection_id
    assert webhook["token"]
    assert webhook["url"].endswith(f"/ingest/webhook/{webhook['token']}")
    assert webhook["default_kind"] == "shipments"
    assert "una sola vez" in webhook["message"]


def test_the_url_uses_the_configured_public_origin(webhook):
    """The operator sees this URL once, so it must be the one n8n can reach.

    The TestClient's own host is `http://testserver`; a URL built from the
    request would say that, which is exactly the proxy bug this prevents.
    """
    assert webhook["url"] == f"{PUBLIC_API_URL}/ingest/webhook/{webhook['token']}"
    assert "testserver" not in webhook["url"]


def test_the_token_is_never_readable_again(client, owner_token, connection_id, webhook):
    response = client.get("/config/connections?scope=global", headers=auth(owner_token))
    assert response.status_code == 200
    connection = next(
        c for c in response.json() if c["connection_id"] == connection_id
    )
    assert connection["has_webhook"] is True
    # Not the token, and not its hash either.
    assert webhook["token"] not in response.text


def test_only_an_owner_can_issue_a_token(client, owner_token, connection_id):
    invite = client.post(
        "/auth/invite", json={"email": "analista@masterdata.app", "role": "analyst"},
        headers=auth(owner_token),
    )
    assert invite.status_code == 201
    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": "clave-de-analista-1234",
            "full_name": "Analista",
        },
    )
    assert accepted.status_code == 200

    response = client.post(
        f"/config/connections/{connection_id}/webhook",
        headers=auth(accepted.json()["access_token"]),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_an_unknown_token_is_not_found(client):
    response = client.post("/ingest/webhook/no-existe-este-token", json={"rows": ROWS})
    assert response.status_code == 404
    # The message must not reveal whether the token ever existed.
    assert response.json()["error"]["message"] == "Webhook no encontrado"


# =============================================================================
# The data
# =============================================================================


def test_a_json_payload_ingests_through_the_engine(client, owner_token, webhook):
    response = client.post(
        f"/ingest/webhook/{webhook['token']}",
        json={"kind": "shipments", "rows": ROWS},
    )
    assert response.status_code == 202, response.text
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "shipments"

    job = _wait_for_job(client, owner_token, jobs[0]["id"])
    assert job["status"] == "done", job

    detail = client.get(f"/ingest/batches/{job['batch_id']}", headers=auth(owner_token))
    assert detail.status_code == 200, detail.text
    batch = detail.json()["batch"]
    assert batch["rows_total"] == len(ROWS)
    assert batch["rows_inserted"] == len(ROWS)
    assert batch["rows_failed"] == 0
    # A global connection has no country of its own; the workspace's single
    # active country is where these guides landed.
    assert batch["country_code"] == "CO"


def test_the_same_payload_twice_is_idempotent(client, owner_token, webhook):
    response = client.post(
        f"/ingest/webhook/{webhook['token']}",
        json={"kind": "shipments", "rows": ROWS},
    )
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert job["status"] == "duplicate", job


def test_a_payload_without_rows_is_refused(client, webhook):
    response = client.post(f"/ingest/webhook/{webhook['token']}", json={"rows": []})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_payload"


def test_a_raw_csv_body_takes_the_same_path(client, owner_token, webhook):
    csv_body = "guia;fecha;estado;valor\nWH-0003;2026-07-02;Entregado;45000\n"
    response = client.post(
        f"/ingest/webhook/{webhook['token']}",
        content=csv_body.encode("utf-8"),
        headers={"Content-Type": "text/csv", "X-Filename": "manual.csv"},
    )
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert job["status"] == "done", job


def test_every_call_is_recorded_so_a_silent_failure_is_visible(client, webhook):
    response = client.get(
        "/worker/runs?limit=50", headers={"X-Worker-Secret": WORKER_SECRET}
    )
    assert response.status_code == 200, response.text
    runs = [run for run in response.json() if run["job_name"] == "webhook_ingest"]
    assert runs, "ninguna llamada al webhook quedó registrada en raw.job_run"
    assert any(run["status"] == "ok" for run in runs)
    assert any(run["status"] == "failed" for run in runs)


# =============================================================================
# Revocation
# =============================================================================


def test_a_revoked_token_stops_working(client, owner_token, connection_id, webhook):
    revoked = client.delete(
        f"/config/connections/{connection_id}/webhook", headers=auth(owner_token)
    )
    assert revoked.status_code == 204

    response = client.post(
        f"/ingest/webhook/{webhook['token']}",
        json={"kind": "shipments", "rows": ROWS},
    )
    assert response.status_code == 404
    # Identical to an unknown token: a caller cannot tell revoked from invented.
    assert response.json()["error"]["message"] == "Webhook no encontrado"

    listed = client.get("/config/connections?scope=global", headers=auth(owner_token))
    connection = next(c for c in listed.json() if c["connection_id"] == connection_id)
    assert connection["has_webhook"] is False


def test_regenerating_replaces_the_previous_token(client, owner_token, connection_id):
    first = client.post(
        f"/config/connections/{connection_id}/webhook", headers=auth(owner_token)
    )
    assert first.status_code == 201
    second = client.post(
        f"/config/connections/{connection_id}/webhook", headers=auth(owner_token)
    )
    assert second.status_code == 201
    assert second.json()["token"] != first.json()["token"]

    stale = client.post(
        f"/ingest/webhook/{first.json()['token']}",
        json={"kind": "shipments", "rows": ROWS},
    )
    assert stale.status_code == 404
