"""Uploading BY PLATFORM (migration 042): the operator names the platform, the
file-mode connection is found or created, and a recognised report is refused
when it is being loaded into the wrong platform.

Why this exists: the operator's real Effi export sat under "Carga manual"
because the upload screen asked for a connection, not for a platform, and an
Effi connection could not even be created without a session consent nobody
needed. Every guide then read as `manual_xlsx` and the dashboard could not put
Effi next to Dropi.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

FIXTURES = Path(__file__).parent / "fixtures"

OWNER_EMAIL = "owner@carga.ec"
OWNER_PASSWORD = "una-clave-larga-de-cargas"
COUNTRY = "EC"
MANUAL_CONNECTION = UUID("cccccccc-0000-0000-0000-000000000c0c")


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
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = api_dsn
    os.environ["DATABASE_URL_READONLY"] = ""
    os.environ["AI_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(tmp_path_factory.mktemp("uploads"))
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
def owner_token(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña de Cargas",
            "tenant_name": "Cargas Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def tenant_id(client, owner_token) -> UUID:
    return UUID(client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"])


@pytest.fixture(scope="module")
def workspace(client, api_dsn, tenant_id) -> None:
    """The country active, one manual connection - and NO Effi or Dropi one."""
    with psycopg.connect(api_dsn) as conn:
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=MANUAL_CONNECTION,
            country_code=COUNTRY,
            platform_code="manual_xlsx",
            slug="cargas",
        )


@pytest.fixture(scope="module")
def effi_real_export() -> bytes:
    """Four real-shaped Effi guides: the profile detector recognises these headers."""
    return (FIXTURES / "effi_guias_real_shape.xlsx").read_bytes()


@pytest.fixture(scope="module")
def generic_csv() -> bytes:
    """A plain report that matches no known profile - could be from anywhere."""
    return (FIXTURES / "effi_guias_dia1.csv").read_bytes()


def _wait_for_job(client, token, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/ingest/jobs/{job_id}", headers=auth(token)).json()
        if job["status"] in ("done", "failed", "duplicate"):
            return job
        time.sleep(0.3)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _connections(client, token, platform: str) -> list[dict]:
    rows = client.get("/config/connections", headers=auth(token)).json()
    return [row for row in rows if row["platform_code"] == platform]


# =============================================================================
# 1. Naming the platform is enough: the connection appears on its own
# =============================================================================


def test_uploading_as_dropi_creates_the_file_connection_and_lands_the_guides(
    client, owner_token, workspace, generic_csv
):
    assert _connections(client, owner_token, "dropi") == []

    response = client.post(
        "/ingest/upload",
        data={"platform_code": "dropi", "country_code": COUNTRY, "kind": "shipments"},
        files={"files": ("pedidos_dropi.csv", generic_csv, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert job["status"] == "done", job

    created = _connections(client, owner_token, "dropi")
    assert len(created) == 1
    assert created[0]["country_code"] == COUNTRY
    assert "archivo" in created[0]["connection_name"]

    # And the dashboard now sees them as Dropi's.
    platforms = client.get(
        "/kpis/platforms", params={"country": COUNTRY}, headers=auth(owner_token)
    ).json()["rows"]
    by_code = {row["platform_code"]: row for row in platforms}
    assert by_code["dropi"]["shipments"] == 10


def test_a_second_upload_reuses_the_same_connection(
    client, owner_token, workspace, generic_csv
):
    payload = generic_csv.replace(b"E-1001", b"E-2001")   # different bytes, new content hash
    response = client.post(
        "/ingest/upload",
        data={"platform_code": "dropi", "country_code": COUNTRY, "kind": "shipments"},
        files={"files": ("pedidos_dropi_2.csv", payload, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text
    _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert len(_connections(client, owner_token, "dropi")) == 1


def test_effi_by_file_needs_no_session_consent(
    client, owner_token, workspace, effi_real_export
):
    """Before 042 this INSERT was refused: Effi is tier 3. A file is not a session."""
    response = client.post(
        "/ingest/upload",
        data={"platform_code": "effi", "country_code": COUNTRY, "kind": "shipments"},
        files={
            "files": (
                "reporte_effi.xlsx", effi_real_export,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text
    job = _wait_for_job(client, owner_token, response.json()["jobs"][0]["id"])
    assert job["status"] == "done", job

    effi = _connections(client, owner_token, "effi")
    assert len(effi) == 1
    assert effi[0]["consent_granted_at"] is None


def test_the_file_connection_is_never_picked_up_by_the_session_sync(api_dsn, tenant_id, workspace):
    """The tier-3 job replays sessions. A file-mode Effi connection has none."""
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            """
            SELECT count(*) FROM core.connection c
            JOIN core.platform p ON p.code = c.platform_code
            WHERE c.tenant_id = %s AND p.tier = 3 AND c.status = 'active'
              AND c.consent_granted_at IS NOT NULL AND c.source_mode = 'session'
            """,
            (tenant_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT source_mode FROM core.connection WHERE tenant_id = %s AND platform_code = 'effi'",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == "file"


# =============================================================================
# 2. The check: a recognised report goes to its own platform, or nowhere
# =============================================================================


def test_an_effi_export_loaded_as_dropi_is_refused_by_name(
    client, owner_token, workspace, effi_real_export
):
    response = client.post(
        "/ingest/upload",
        data={"platform_code": "dropi", "country_code": COUNTRY, "kind": "shipments"},
        files={
            "files": (
                "reporte_effi.xlsx", effi_real_export,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "platform_mismatch"
    assert body["detail"]["detected_platform_code"] == "effi"
    assert body["detail"]["target_platform_code"] == "dropi"
    assert "Effi" in body["message"]


def test_an_effi_export_on_the_classic_manual_connection_is_refused_too(
    client, owner_token, workspace, effi_real_export
):
    """The rule holds on the old path: naming a connection is naming a platform."""
    response = client.post(
        "/ingest/upload",
        data={"connection_id": str(MANUAL_CONNECTION), "kind": "shipments"},
        files={
            "files": (
                "reporte_effi.xlsx", effi_real_export,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=auth(owner_token),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "platform_mismatch"


def test_a_generic_file_is_let_through_on_any_platform(
    client, owner_token, workspace, generic_csv
):
    """A plain CSV says nothing about where it came from. manual_xlsx is for that."""
    response = client.post(
        "/ingest/upload",
        data={"platform_code": "manual_xlsx", "country_code": COUNTRY, "kind": "shipments"},
        files={"files": ("otro.csv", generic_csv.replace(b"E-1001", b"E-3001"), "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text


def test_detect_names_the_platform_of_a_recognised_report(
    client, owner_token, workspace, effi_real_export, generic_csv
):
    """What the upload screen pre-selects, and what the check compares against."""
    known = client.post(
        "/ingest/detect",
        files={
            "file": (
                "reporte_effi.xlsx", effi_real_export,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=auth(owner_token),
    ).json()
    assert known["detected_platform_code"] == "effi"
    assert known["detected_platform_name"].startswith("Effi")

    unknown = client.post(
        "/ingest/detect",
        files={"file": ("otro.csv", generic_csv, "text/csv")},
        headers=auth(owner_token),
    ).json()
    assert unknown["detected_platform_code"] is None


# =============================================================================
# 3. Refusals that keep a typo from becoming a platform
# =============================================================================


def test_an_unknown_platform_is_refused(client, owner_token, workspace, generic_csv):
    response = client.post(
        "/ingest/upload",
        data={"platform_code": "shopee", "country_code": COUNTRY, "kind": "shipments"},
        files={"files": ("x.csv", generic_csv, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_platform"


def test_a_country_the_platform_does_not_operate_in_is_refused(
    client, owner_token, workspace, generic_csv
):
    """Effi operates in CO, EC and PA (migration 001). Not in Peru."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.workspace_country (tenant_id, country_code) "
            "SELECT tenant_id, 'PE' FROM core.connection WHERE id = %s ON CONFLICT DO NOTHING",
            (MANUAL_CONNECTION,),
        )
        conn.commit()

    response = client.post(
        "/ingest/upload",
        data={"platform_code": "effi", "country_code": "PE", "kind": "shipments"},
        files={"files": ("x.csv", generic_csv, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "platform_unavailable"


def test_neither_connection_nor_platform_is_a_clear_error(
    client, owner_token, workspace, generic_csv
):
    response = client.post(
        "/ingest/upload",
        data={"kind": "shipments"},
        files={"files": ("x.csv", generic_csv, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "target_required"
