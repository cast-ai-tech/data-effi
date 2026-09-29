"""The extension's redeem against a REAL database, row-level security on.

QA 2026-09-28: `redeem_pairing` commits the stored session and then runs the
probe on the same connection. `SET LOCAL norte.service` dies with that commit,
so the probe ran as nobody: the credential row it had just written was
invisible, the answer was "Esta conexión no tiene una cuenta conectada.
Ingresa tu usuario y contraseña" (a path the extension exists to avoid), Effi
was never asked, and the outcome UPDATE silently touched zero rows.

The unit tests in test_effi_pairing.py replace `connection` and `fetch_one`, so
they could not see it. This one keeps the database and swaps only the probe
for a fake that reports what IT can see.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@pairing-db.co"
OWNER_PASSWORD = "una-clave-larga-de-pairing"
EFFI_CONNECTION = UUID("eeeeeeee-0000-0000-0000-00000000effe")


@pytest.fixture(scope="module")
def api_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture(scope="module")
def client(api_dsn, tmp_path_factory):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    previous_vault = os.environ.get("CONNECTION_VAULT_KEY")
    os.environ["DATABASE_URL"] = api_dsn
    os.environ["DATABASE_URL_READONLY"] = ""
    os.environ["AI_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(tmp_path_factory.mktemp("uploads"))
    os.environ["CONNECTION_VAULT_KEY"] = Fernet.generate_key().decode()
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)

    from api.settings import get_settings

    get_settings.cache_clear()
    from api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()
    if previous_vault is None:
        os.environ.pop("CONNECTION_VAULT_KEY", None)
    else:
        os.environ["CONNECTION_VAULT_KEY"] = previous_vault


@pytest.fixture(scope="module")
def owner_token(client, api_dsn) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña Pairing",
            "tenant_name": "Pairing DB",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    tenant_id = UUID(
        client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["tenant_id"]
    )
    with psycopg.connect(api_dsn) as conn:
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=EFFI_CONNECTION,
            country_code="CO",
            platform_code="effi",
            slug="pairing-db",
        )
    return token


def test_the_probe_sees_the_session_the_redeem_just_stored(client, owner_token, monkeypatch):
    from api import credentials
    from api.routers import effi_pairing as ep
    from api.schemas import ConnectionPreflightResponse

    seen: dict[str, object] = {}

    def fake_preflight(conn, *, connection_id, tenant_id, **_kwargs):
        stored = credentials.load_session(conn, connection_id=connection_id, tenant_id=tenant_id)
        seen["browser_session"] = stored.is_browser_session
        ok = stored.is_browser_session
        return ConnectionPreflightResponse(
            connection_id=connection_id,
            credential_status="ok" if ok else "none",
            is_usable=ok,
            summary="Conectado." if ok else "Sin sesión visible.",
            permissions=[],
        )

    monkeypatch.setattr(ep, "run_preflight_for_connection", fake_preflight)

    issued = client.post(
        f"/config/effi/connections/{EFFI_CONNECTION}/pairing",
        json={"consent_granted": True},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert issued.status_code == 201, issued.text
    pairing = issued.json()

    redeemed = client.post(
        "/config/effi/pairing/redeem",
        json={
            "code": pairing["code"],
            "cookies": [{"name": "ci_session", "value": "sesion-de-prueba-0123456789"}],
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    assert seen["browser_session"] is True
    body = redeemed.json()
    assert body["connected"] is True
    assert body["credential_status"] == "ok"

    # And the outcome was written where the owner's screen polls it.
    status = client.get(
        f"/config/effi/connections/{EFFI_CONNECTION}/pairing/{pairing['pairing_id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()
    assert status["state"] == "connected", status


def test_a_probe_that_blows_up_still_records_the_outcome(client, owner_token, monkeypatch):
    from api.routers import effi_pairing as ep

    def boom(*_args, **_kwargs):
        raise RuntimeError("Effi no contestó")

    monkeypatch.setattr(ep, "run_preflight_for_connection", boom)

    pairing = client.post(
        f"/config/effi/connections/{EFFI_CONNECTION}/pairing",
        json={"consent_granted": True},
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()
    redeemed = client.post(
        "/config/effi/pairing/redeem",
        json={
            "code": pairing["code"],
            "cookies": [{"name": "ci_session", "value": "otra-sesion-de-prueba-0123"}],
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["connected"] is False

    status = client.get(
        f"/config/effi/connections/{EFFI_CONNECTION}/pairing/{pairing['pairing_id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()
    assert status["state"] == "unverified", status
