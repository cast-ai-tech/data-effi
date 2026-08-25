"""El alcance por país, en las superficies donde no hay parámetro que vigilar.

`_guard_country` sabe rechazar `?country=CO` a quien solo puede ver Guatemala.
Lo que NO sabe hacer es recortar una lista que nadie filtró, y ahí estaba el
agujero: conexiones, cargas e historial devolvían todos los países de la
sociedad a un socio limitado a uno.

Lo que estos tests afirman:
  - las listas vienen recortadas aunque no se pida ningún país
  - un id escrito a mano no salta el alcance, y responde 404 y no 403: que algo
    exista en un país que no puedes ver no es algo que debas poder averiguar
  - no se puede CARGAR por una conexión de otro país, ni por una global, porque
    un archivo cargado ya no se puede desver
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "duena.alcance@masterdata.app"
OWNER_PASSWORD = "una-clave-larga-de-prueba"
PARTNER_EMAIL = "socio.gt.alcance@masterdata.app"
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
    """Una sociedad que opera en Guatemala y Colombia."""
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña",
            "tenant_name": "Sociedad de dos países",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    for code in ("GT", "CO"):
        activado = client.put(
            "/config/countries",
            headers=auth(body["access_token"]),
            json={"country_code": code, "is_active": True},
        )
        assert activado.status_code == 200, activado.text
    return body


@pytest.fixture(scope="module")
def conexiones(client, owner) -> dict[str, str]:
    """Una conexión por país, ambas de la misma sociedad."""
    creadas = {}
    for code in ("GT", "CO"):
        response = client.post(
            "/config/connections",
            headers=auth(owner["access_token"]),
            json={
                # `ads_manual` es tier 2 y de alcance por país, que es
                # justamente lo que estos tests necesitan: una conexión que
                # PERTENECE a un país concreto.
                "platform_code": "ads_manual",
                "country_code": code,
                "name": f"Pauta manual {code}",
            },
        )
        assert response.status_code == 201, response.text
        creadas[code] = response.json()["connection_id"]
    return creadas


@pytest.fixture(scope="module")
def partner_token(client, owner) -> str:
    """El dueño de la operación de Guatemala: `owner`, pero solo en GT.

    Es owner a propósito, y no analyst: el rol ya le permite editar y borrar
    conexiones, así que lo ÚNICO que puede detenerlo frente a las de Colombia es
    el alcance por país. Con un analyst estos tests pasarían por el motivo
    equivocado - un 403 de rol - y no probarían nada de lo que quieren probar.
    """
    invite = client.post(
        "/auth/invite",
        headers=auth(owner["access_token"]),
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
    assert accepted.json()["countries"] == ["GT"]
    return accepted.json()["access_token"]


# =============================================================================
# Listas sin parámetro de país
# =============================================================================


def test_the_owner_sees_both_countries(client, owner, conexiones):
    """La referencia: sin alcance limitado se ven las dos."""
    response = client.get("/config/connections", headers=auth(owner["access_token"]))
    assert response.status_code == 200, response.text
    assert {row["country_code"] for row in response.json()} == {"GT", "CO"}


def test_connections_come_back_already_trimmed(client, partner_token, conexiones):
    """Sin pedir país: aquí estaba la fuga."""
    response = client.get("/config/connections", headers=auth(partner_token))
    assert response.status_code == 200, response.text
    assert {row["country_code"] for row in response.json()} == {"GT"}


def test_the_load_history_is_trimmed_too(client, partner_token):
    response = client.get("/ingest/batches", headers=auth(partner_token))
    assert response.status_code == 200, response.text
    for row in response.json()["items"]:
        assert row["country_code"] == "GT"


def test_the_job_list_is_trimmed_too(client, partner_token):
    response = client.get("/ingest/jobs", headers=auth(partner_token))
    assert response.status_code == 200, response.text


# =============================================================================
# Un id escrito a mano
# =============================================================================


def test_a_connection_id_from_another_country_is_not_found(
    client, partner_token, conexiones
):
    """404 y no 403: el código de error no confirma que exista."""
    response = client.patch(
        f"/config/connections/{conexiones['CO']}",
        headers=auth(partner_token),
        json={"name": "Renombrada por quien no debe"},
    )
    assert response.status_code == 404, response.text


def test_a_connection_from_another_country_cannot_be_deleted(
    client, owner, partner_token, conexiones
):
    response = client.delete(
        f"/config/connections/{conexiones['CO']}", headers=auth(partner_token)
    )
    assert response.status_code == 404, response.text

    # Y sigue viva para quien sí puede verla: el 404 protegió, no borró.
    from_owner = client.get("/config/connections", headers=auth(owner["access_token"]))
    assert conexiones["CO"] in [row["connection_id"] for row in from_owner.json()]


def test_a_webhook_cannot_be_minted_for_another_country(client, partner_token, conexiones):
    """Regenerar el webhook ajeno sería tomar el control de esa entrada de datos."""
    response = client.post(
        f"/config/connections/{conexiones['CO']}/webhook", headers=auth(partner_token)
    )
    assert response.status_code == 404, response.text


# =============================================================================
# Cargar datos
# =============================================================================


def test_uploading_through_another_countrys_connection_is_refused(
    client, partner_token, conexiones
):
    """Un archivo cargado ya no se puede desver: queda en los totales."""
    response = client.post(
        "/ingest/upload",
        headers=auth(partner_token),
        files={"files": ("guias.csv", b"tracking\nABC-1\n", "text/csv")},
        data={"connection_id": conexiones["CO"], "kind": "shipments"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_uploading_through_your_own_country_still_works(
    client, partner_token, conexiones
):
    """La guarda no puede romper el caso normal."""
    response = client.post(
        "/ingest/upload",
        headers=auth(partner_token),
        files={"files": ("guias.csv", b"tracking\nABC-1\n", "text/csv")},
        data={"connection_id": conexiones["GT"], "kind": "shipments"},
    )
    assert response.status_code == 202, response.text
