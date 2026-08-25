"""The transport layer of a file upload, at the sizes that matter.

Why this exists: a 5.8 MB wallet-movements export died with HTTP 413. Not in
the API - its limit was 10 MB on Render and is 25 MB now - but in the Netlify
function the web's proxy runs as, which caps a request body at 6 MB (about
4.5 MB once multipart is base64-encoded). The web now posts files straight to
the API. This module pins the API side of that contract: a file well past the
proxy's old ceiling is accepted, lands in raw.load_batch, and the API's own
25 MB ceiling still refuses what it should.
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

OWNER_EMAIL = "owner@pesados.ec"
OWNER_PASSWORD = "una-clave-larga-de-pesados"
COUNTRY = "EC"
MANUAL_CONNECTION = UUID("dddddddd-0000-0000-0000-000000000d0d")

MB = 1024 * 1024


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
    os.environ["MAX_UPLOAD_MB"] = "25"        # what render.yaml ships
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
            "full_name": "Dueña de Pesados",
            "tenant_name": "Pesados Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def tenant_id(client, owner_token) -> UUID:
    return UUID(client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"])


@pytest.fixture(scope="module")
def workspace(client, api_dsn, tenant_id) -> None:
    with psycopg.connect(api_dsn) as conn:
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=MANUAL_CONNECTION,
            country_code=COUNTRY,
            platform_code="manual_xlsx",
            slug="pesados",
        )


PADDING = "x" * 4_000   # well under the csv module's 128 KB field ceiling


def _padded_movements(target_bytes: int) -> bytes:
    """The wallet-movements fixture, grown to at least `target_bytes`.

    The fixture's rows are repeated with a fresh `Referencia` each time (so no
    two movements collide) and a long free-text `Descripcion`, which is what a
    real export's description column looks like at scale. Guides, concepts and
    amounts stay the fixture's own. The BODY on the wire is what a big export
    weighs, which is the only thing this module is about.
    """
    lines = (FIXTURES / "effi_movimientos.csv").read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], [line for line in lines[1:] if line.strip()]
    out = [header]
    size = len(header) + 1
    serial = 0
    while size < target_bytes:
        for row in rows:
            serial += 1
            guide, date, concept, amount, _reference, description = row.split(";")
            line = ";".join([guide, date, concept, amount, f"MOV-{serial:07d}", f"{description} {PADDING}"])
            out.append(line)
            size += len(line) + 1
    return ("\n".join(out) + "\n").encode("utf-8")


def _wait_for_job(client, token, job_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/ingest/jobs/{job_id}", headers=auth(token)).json()
        if job["status"] in ("done", "failed", "duplicate"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_an_eight_megabyte_wallet_export_is_accepted_and_lands_in_load_batch(
    client, owner_token, workspace, api_dsn, tenant_id
):
    payload = _padded_movements(8 * MB)
    assert len(payload) > 8 * MB   # past anything the Netlify function could carry

    response = client.post(
        "/ingest/upload",
        data={"platform_code": "effi", "country_code": COUNTRY, "kind": "movements"},
        files={"files": ("movimientos_wallet.csv", payload, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 202, response.text
    accepted = response.json()["jobs"][0]
    assert accepted["size_bytes"] == len(payload)

    job = _wait_for_job(client, owner_token, accepted["id"])
    assert job["status"] == "done", job
    assert job["batch_id"], job

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute("SELECT tenant_id FROM raw.load_batch WHERE id = %s", (job["batch_id"],))
        row = cur.fetchone()
    assert row is not None, "the accepted upload never became a load batch"
    assert row[0] == tenant_id


def test_the_api_still_refuses_a_file_past_its_own_ceiling(client, owner_token, workspace):
    """25 MB is the API's limit, not a formality: 26 MB is a 413 with a reason."""
    payload = _padded_movements(26 * MB)

    response = client.post(
        "/ingest/upload",
        data={"platform_code": "effi", "country_code": COUNTRY, "kind": "movements"},
        files={"files": ("movimientos_enormes.csv", payload, "text/csv")},
        headers=auth(owner_token),
    )
    assert response.status_code == 413, response.text
    body = response.json()["error"]
    assert body["code"] == "payload_too_large"
    assert "25 MB" in body["message"]
