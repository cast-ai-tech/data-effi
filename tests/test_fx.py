"""Tasas de cambio: contra el dólar y contra el peso colombiano.

What these tests are really asserting:
  - every supported currency shows up, whether or not it has a rate yet
  - the peso column is DERIVED, so it can never disagree with the dollar one
  - a rate typed by hand is stored in the direction the database expects, and
    comes back in the direction people quote
  - a currency with no rate says "no rate" instead of inventing one
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

EMAIL = "tasas@dataeffi.co"
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
    response = client.post(
        "/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "full_name": "Dueña",
            "tenant_name": "Sociedad de tasas",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def rates(client, token: str) -> dict[str, dict]:
    response = client.get("/config/fx", headers=auth(token))
    assert response.status_code == 200, response.text
    return {row["currency_code"]: row for row in response.json()}


# =============================================================================
# El catálogo
# =============================================================================


def test_every_supported_currency_is_listed(client, owner):
    table = rates(client, owner["access_token"])
    # Las monedas de los once países soportados, incluidas las que llegaron por
    # migración: HNL (033), CRC (034), DOP y VES (038).
    for currency in ["COP", "MXN", "PEN", "CLP", "GTQ", "USD", "HNL", "CRC", "DOP", "VES"]:
        assert currency in table, f"falta {currency}"


def test_a_currency_shared_by_two_countries_appears_once(client, owner):
    """Ecuador y Panamá usan el dólar: una fila, dos países."""
    usd = rates(client, owner["access_token"])["USD"]
    assert set(usd["country_codes"]) >= {"EC", "PA"}


def test_a_currency_without_a_rate_says_so(client, owner):
    """Base recién creada: sin tasas todavía, y eso se reporta en vez de inventarse."""
    table = rates(client, owner["access_token"])
    sin_tasa = [row for row in table.values() if not row["has_rate"]]
    assert sin_tasa, "esperaba al menos una moneda sin tasa en una base nueva"
    for row in sin_tasa:
        assert row["to_usd"] is None
        assert row["to_cop"] is None
        assert row["per_usd"] is None


# =============================================================================
# Fijar una tasa a mano
# =============================================================================


def test_a_hand_typed_rate_round_trips_in_the_direction_people_quote(client, owner):
    """Se escribe "un dólar son 3900 pesos" y se lee igual, no invertido.

    La tolerancia es 1e-4 y no algo más fino por una razón concreta:
    `core.fx_rate.rate` es numeric(18,8) y guarda la dirección inversa, así que
    1/3900 se redondea a 0,00025641 y al invertirlo vuelve como 3900,0039. Son
    cuatro milésimas de peso en 3.900 - cuatro pesos por cada millón - y no vale
    recrear mart.v_global_summary para recuperarlas.

    Dónde SÍ importaría: una moneda por encima de unos 100.000 por dólar deja
    menos de cinco cifras significativas y el error deja de ser ruido. El
    bolívar estuvo ahí antes de la redenominación de 2021. Si vuelve a pasar,
    la respuesta es ampliar la columna, no aflojar más este número.
    """
    response = client.put(
        "/config/fx",
        headers=auth(owner["access_token"]),
        json={"currency_code": "COP", "per_usd": 3900},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "manual"
    assert body["per_usd"] == pytest.approx(3900, rel=1e-4)
    # Y por dentro quedó guardada la inversa, que es lo que multiplica el mart.
    assert body["to_usd"] == pytest.approx(1 / 3900, rel=1e-4)


def test_the_peso_column_is_derived_from_the_dollar_one(client, owner):
    """1 GTQ = 3900 / 7,8 COP. Nunca se guarda: se calcula, y por eso no puede
    contradecir a la columna del dólar."""
    client.put(
        "/config/fx",
        headers=auth(owner["access_token"]),
        json={"currency_code": "COP", "per_usd": 3900},
    )
    client.put(
        "/config/fx",
        headers=auth(owner["access_token"]),
        json={"currency_code": "GTQ", "per_usd": 7.8},
    )

    table = rates(client, owner["access_token"])
    assert table["GTQ"]["to_cop"] == pytest.approx(3900 / 7.8, rel=1e-4)
    # El peso contra sí mismo vale uno, que es la comprobación de que la
    # división usa la misma fuente en ambos lados.
    assert table["COP"]["to_cop"] == pytest.approx(1.0, rel=1e-9)


def test_an_unsupported_currency_is_refused(client, owner):
    response = client.put(
        "/config/fx",
        headers=auth(owner["access_token"]),
        json={"currency_code": "JPY", "per_usd": 150},
    )
    assert response.status_code == 404, response.text


def test_a_rate_of_zero_or_less_is_refused(client, owner):
    for value in (0, -3900):
        response = client.put(
            "/config/fx",
            headers=auth(owner["access_token"]),
            json={"currency_code": "COP", "per_usd": value},
        )
        assert response.status_code == 422, response.text


# =============================================================================
# El proveedor: dos convenciones de URL y tres formas de respuesta
# =============================================================================


def test_the_endpoint_is_built_for_each_provider_convention():
    from worker.jobs import _provider_endpoint

    assert (
        _provider_endpoint("https://open.er-api.com/v6/latest")
        == "https://open.er-api.com/v6/latest/USD"
    )
    assert (
        _provider_endpoint("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies")
        == "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
    )
    # Una URL que ya apunta al recurso exacto se respeta tal cual.
    assert _provider_endpoint("https://ejemplo.test/usd.json") == "https://ejemplo.test/usd.json"


def test_every_envelope_shape_is_understood():
    """Elegir la llave equivocada devuelve {} y el job lo reporta como "proveedor
    caído" cuando en realidad la petición funcionó."""
    from worker.jobs import _parse_rate_payload

    assert _parse_rate_payload({"rates": {"COP": 3048.12}}) == {"COP": 3048.12}
    assert _parse_rate_payload({"conversion_rates": {"COP": 3048.12}}) == {"COP": 3048.12}
    # currency-api anida bajo la moneda base y en minúsculas.
    assert _parse_rate_payload({"date": "2026-08-22", "usd": {"cop": 3048.12}}) == {
        "COP": 3048.12
    }
    assert _parse_rate_payload({"algo": "inesperado"}) == {}


# =============================================================================
# Fuentes oficiales
# =============================================================================


def test_a_source_that_fails_does_not_take_the_others_down():
    """Una caída del Banguat no puede dejar sin actualizar al resto del continente."""
    from worker import official_rates

    class ClienteRoto:
        def get(self, *args, **kwargs):
            raise RuntimeError("banco central caído")

        def post(self, *args, **kwargs):
            raise RuntimeError("banco central caído")

    assert official_rates.fetch_official_rates(ClienteRoto(), ("COP", "PEN", "GTQ")) == {}


def test_a_currency_with_no_official_source_is_skipped():
    """VES, DOP y HNL no tienen API pública: se dejan al proveedor general."""
    from worker.official_rates import OFFICIAL_SOURCES

    for currency in ("VES", "DOP", "HNL", "USD"):
        assert currency not in OFFICIAL_SOURCES


def test_the_sources_that_need_a_token_skip_themselves_without_one(monkeypatch):
    """Sin credencial no fallan: simplemente no existen, y el proveedor cubre."""
    from worker import official_rates

    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    monkeypatch.delenv("BCCR_TOKEN", raising=False)
    monkeypatch.delenv("BCCR_EMAIL", raising=False)

    class ClienteQueNoDeberiaUsarse:
        def get(self, *args, **kwargs):
            raise AssertionError("no debería llamarse sin token")

    assert official_rates._banxico_mexico(ClienteQueNoDeberiaUsarse()) == (None, None)
    assert official_rates._bccr_costa_rica(ClienteQueNoDeberiaUsarse()) == (None, None)


def test_the_peruvian_holiday_gap_is_walked_backwards():
    """El BCRP manda "n.d." en feriados; hay que retroceder, no leer el último."""
    from worker.official_rates import _bcrp_peru

    class Cliente:
        def get(self, *args, **kwargs):
            class R:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {
                        "periods": [
                            {"name": "20.Ago.26", "values": ["3.361"]},
                            {"name": "21.Ago.26", "values": ["n.d."]},
                            {"name": "22.Ago.26", "values": ["n.d."]},
                        ]
                    }

            return R()

    rate, stamped = _bcrp_peru(Cliente())
    assert rate == 3.361
    assert stamped is not None and stamped.day == 20
