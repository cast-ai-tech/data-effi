"""Effi next to Dropi: the platform as a filter and as a dimension.

The report the operator built by hand has one block per platform and a strip
adding them up. This suite checks the two things that make that screen honest:

  1. THE NUMBERS SPLIT. `/kpis/daily-status` returns one row per day AND per
     platform, and each platform's guides add up to what was loaded through
     its connection - not to the country total.
  2. THE FILTER CUTS, AND SAYS SO. `platform=dropi` on any range-aware endpoint
     removes Effi's guides, and the response echoes `platform: "dropi"`. The
     endpoints that cannot separate platforms answer `platform: null` rather
     than accepting the parameter and ignoring it in silence - the same
     contract `date_basis` already has.

THE FIXTURE, IN ONE PARAGRAPH
One tenant, one country (EC), two connections: an Effi one and a Dropi one.
Effi loaded six guides over two days; Dropi loaded three on the first day.
Every count below can be added up from the table in `seeded`.
"""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@plataformas.ec"
OWNER_PASSWORD = "una-clave-larga-de-plataformas"

COUNTRY = "EC"
CURRENCY = "USD"

EFFI_CONNECTION = UUID("aaaaaaaa-0000-0000-0000-00000000effa")
DROPI_CONNECTION = UUID("bbbbbbbb-0000-0000-0000-0000000d0b1a")

DAY_1 = date(2026, 8, 3)
DAY_2 = date(2026, 8, 4)

# (tracking, platform, status, created, delivered)
GUIDES = [
    ("EF-1", "effi", "delivered", DAY_1, date(2026, 8, 5)),
    ("EF-2", "effi", "delivered", DAY_1, date(2026, 8, 6)),
    ("EF-3", "effi", "returned", DAY_1, None),
    ("EF-4", "effi", "in_transit", DAY_1, None),
    ("EF-5", "effi", "delivered", DAY_2, date(2026, 8, 7)),
    ("EF-6", "effi", "delivery_issue", DAY_2, None),
    ("DR-1", "dropi", "delivered", DAY_1, date(2026, 8, 6)),
    ("DR-2", "dropi", "returned", DAY_1, None),
    ("DR-3", "dropi", "in_office", DAY_1, None),
]

EFFI_GUIDES = 6
DROPI_GUIDES = 3
ALL_GUIDES = EFFI_GUIDES + DROPI_GUIDES


# =============================================================================
# Harness
# =============================================================================


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
def owner_token(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña de Plataformas",
            "tenant_name": "Plataformas Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def tenant_id(client, owner_token) -> UUID:
    return UUID(client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"])


@pytest.fixture(scope="module")
def seeded(client, api_dsn, tenant_id) -> None:
    """Two connections on one country, nine guides between them."""
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=DROPI_CONNECTION,
            country_code=COUNTRY,
            platform_code="dropi",
            slug="plataformas",
        )
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        # Effi is tier 3: the database refuses the connection without the
        # operator's consent on record (migration 001), so it is granted here.
        cur.execute(
            """
            INSERT INTO core.connection
                (id, tenant_id, country_code, platform_code, name, status, consent_granted_at)
            VALUES (%s, %s, %s, 'effi', 'conn-effi', 'active', now())
            ON CONFLICT (id) DO NOTHING
            """,
            (EFFI_CONNECTION, tenant_id, COUNTRY),
        )

        for tracking, platform, status, created, delivered in GUIDES:
            cur.execute(
                """
                INSERT INTO core.shipment
                    (tenant_id, connection_id, country_code, tracking_number, customer_hash,
                     quantity, status_code, created_date, dispatched_batch_at, delivered_at,
                     currency_code, declared_value, cod_collected, freight_cost, product_cost)
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, 50, %s, 6, 12)
                """,
                (
                    tenant_id,
                    EFFI_CONNECTION if platform == "effi" else DROPI_CONNECTION,
                    COUNTRY, tracking, "a" * 64, status, created, created, delivered,
                    CURRENCY, 50 if status == "delivered" else None,
                ),
            )
        conn.commit()


def get(client, token, path, params=None, **extra):
    return client.get(path, params={**(params or {}), **extra}, headers=auth(token))


def rows_by_platform(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["platform_code"], []).append(row)
    return out


# =============================================================================
# 1. The daily table splits by platform and adds up
# =============================================================================


def test_daily_status_has_one_row_per_day_and_platform(client, owner_token, seeded):
    body = get(client, owner_token, "/kpis/daily-status", {"country": COUNTRY}).json()
    assert body["date_basis"] == "creacion"
    # No platform was asked for, so none was applied - and the response says so.
    assert body["platform"] is None

    by_platform = rows_by_platform(body["rows"])
    assert set(by_platform) == {"effi", "dropi"}
    assert [row["day"] for row in by_platform["effi"]] == [DAY_1.isoformat(), DAY_2.isoformat()]
    assert [row["day"] for row in by_platform["dropi"]] == [DAY_1.isoformat()]

    assert sum(row["shipments"] for row in body["rows"]) == ALL_GUIDES
    assert sum(row["shipments"] for row in by_platform["effi"]) == EFFI_GUIDES
    assert sum(row["shipments"] for row in by_platform["dropi"]) == DROPI_GUIDES


def test_daily_status_groups_the_twelve_statuses_into_five_columns(client, owner_token, seeded):
    """Effi, day 1: two delivered, one returned, one in transit."""
    body = get(client, owner_token, "/kpis/daily-status", {"country": COUNTRY}).json()
    effi_day1 = next(
        r for r in body["rows"] if r["platform_code"] == "effi" and r["day"] == DAY_1.isoformat()
    )
    assert effi_day1["platform_name"].startswith("Effi")
    assert effi_day1["shipments"] == 4
    assert effi_day1["entregada"] == 2
    assert effi_day1["devolucion"] == 1
    assert effi_day1["en_transito"] == 1
    assert effi_day1["novedad"] == 0
    assert effi_day1["indemnizacion"] == 0
    assert effi_day1["cerradas"] == 3

    # The two percentages the migration explains: over all guides of the day
    # (what the hand-made report prints) and over the closed ones.
    assert effi_day1["pct_devolucion_total"] == 25.0
    assert effi_day1["pct_devolucion_cerradas"] == pytest.approx(33.33, abs=0.01)
    assert effi_day1["pct_entrega_cerradas"] == pytest.approx(66.67, abs=0.01)
    # Fewer than ten closed guides: an estimate, and flagged as one.
    assert effi_day1["sample_quality"] == "muestra_corta"

    # Dropi, day 1: "en oficina" counts as novedad on the screen.
    dropi_day1 = next(r for r in body["rows"] if r["platform_code"] == "dropi")
    assert dropi_day1["novedad"] == 1
    assert dropi_day1["entregada"] == 1
    assert dropi_day1["devolucion"] == 1


def test_the_view_and_the_function_agree(api_dsn, tenant_id, seeded):
    """The copilot reads the view; the dashboard reads the function.

    Unfiltered, on the creation date, they must say the same thing column for
    column - otherwise the operator and the assistant answer "¿cuántas devolvió
    Dropi el lunes?" with two numbers.
    """
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, true)", (str(tenant_id),))
        cur.execute(
            "SELECT * FROM mart.v_daily_status_by_platform ORDER BY day, platform_code"
        )
        from_view = cur.fetchall()
        cur.execute(
            "SELECT * FROM mart.f_daily_status(NULL, NULL, 'creacion', NULL) "
            "ORDER BY day, platform_code"
        )
        from_function = cur.fetchall()

        cur.execute("SELECT * FROM mart.v_platform_summary ORDER BY platform_code")
        summary_view = cur.fetchall()
        cur.execute(
            "SELECT * FROM mart.f_platform_summary(NULL, NULL, 'creacion', NULL) "
            "ORDER BY platform_code"
        )
        summary_function = cur.fetchall()

    assert from_view, "la vista no devolvió filas para el tenant"
    assert from_view == from_function
    assert summary_view, "el consolidado no devolvió filas para el tenant"
    assert summary_view == summary_function


# =============================================================================
# 2. The filter cuts every range-aware endpoint, and each answer names it
# =============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/kpis/carriers",
        "/kpis/geo",
        "/kpis/products",
        "/kpis/aging",
        "/kpis/contribution-split",
        "/kpis/dropshipping-margin",
        "/kpis/fulfillment",
        "/kpis/freight",
        "/kpis/cash-cycle",
        "/kpis/problem-rate",
        "/kpis/daily-status",
    ],
)
def test_a_platform_narrows_the_answer_and_echoes_itself(client, owner_token, seeded, path):
    everything = get(client, owner_token, path, {"country": COUNTRY})
    only_dropi = get(client, owner_token, path, {"country": COUNTRY}, platform="dropi")
    assert everything.status_code == 200, everything.text
    assert only_dropi.status_code == 200, only_dropi.text

    assert everything.json()["platform"] is None
    assert only_dropi.json()["platform"] == "dropi"
    assert only_dropi.json()["rows"], f"{path} no devolvió nada para dropi"
    assert everything.json()["rows"] != only_dropi.json()["rows"], (
        f"{path} devuelve lo mismo con y sin plataforma: el filtro no se aplicó"
    )


def test_the_shipment_count_matches_the_platform_exactly(client, owner_token, seeded):
    def shipments(**kwargs) -> int:
        body = get(client, owner_token, "/kpis/global", {}, **kwargs).json()
        return body["rows"][0]["shipments"]

    assert shipments() == ALL_GUIDES
    assert shipments(platform="effi") == EFFI_GUIDES
    assert shipments(platform="dropi") == DROPI_GUIDES


def test_the_filter_composes_with_the_date_range(client, owner_token, seeded):
    """Day 2 only has Effi guides. Dropi on day 2 is an empty, well-labelled answer."""
    body = get(
        client, owner_token, "/kpis/daily-status", {"country": COUNTRY},
        platform="dropi", date_from=DAY_2, date_to=DAY_2,
    ).json()
    assert body["rows"] == []
    assert body["platform"] == "dropi"
    assert body["date_from"] == DAY_2.isoformat()


def test_the_excluded_count_describes_the_filtered_guides(client, owner_token, seeded):
    """Under `entrega`, two of Dropi's three guides have no delivery date."""
    body = get(
        client, owner_token, "/kpis/aging", {"country": COUNTRY},
        platform="dropi", date_field="entrega", date_from=DAY_1, date_to=DAY_2,
    ).json()
    assert body["excluded_no_date"] == 2


# =============================================================================
# 3. Where the filter cannot apply, the answer says so
# =============================================================================


def test_the_platform_comparison_ignores_the_platform_filter(client, owner_token, seeded):
    """`/kpis/platforms` IS the comparison, so a filter would defeat it."""
    body = get(
        client, owner_token, "/kpis/platforms", {"country": COUNTRY}, platform="effi"
    ).json()
    assert body["platform"] is None

    by_code = {row["platform_code"]: row for row in body["rows"]}
    assert set(by_code) == {"effi", "dropi"}
    assert by_code["effi"]["shipments"] == EFFI_GUIDES
    assert by_code["dropi"]["shipments"] == DROPI_GUIDES
    assert by_code["effi"]["share_pct"] == pytest.approx(66.7, abs=0.05)
    assert by_code["dropi"]["share_pct"] == pytest.approx(33.3, abs=0.05)
    assert sum(row["share_pct"] for row in body["rows"]) == pytest.approx(100, abs=0.1)
    # Ordered by volume: the platform "con más ventas" comes first.
    assert body["rows"][0]["platform_code"] == "effi"


@pytest.mark.parametrize("path", ["/kpis/daily-contribution", "/kpis/cohorts", "/kpis/cs", "/kpis/cpa"])
def test_a_view_backed_endpoint_reports_that_it_did_not_separate(
    client, owner_token, seeded, path
):
    """These four have no platform in their grain. They must not pretend."""
    response = get(client, owner_token, path, {"country": COUNTRY}, platform="effi")
    assert response.status_code == 200, response.text
    assert response.json()["platform"] is None


def test_an_unknown_platform_is_refused_by_name(client, owner_token, seeded):
    """A typo must never widen to "todas"."""
    response = get(client, owner_token, "/kpis/carriers", {"country": COUNTRY}, platform="shopee")
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "invalid_platform"
    assert "effi" in body["error"]["message"]
    assert "dropi" in body["error"]["message"]


def test_the_global_summary_under_a_platform_has_no_ad_spend(client, owner_token, seeded):
    """Ad spend belongs to an ads connection, so under Effi there is none to subtract."""
    body = get(client, owner_token, "/kpis/global", {}, platform="effi").json()
    assert body["rows"][0]["ad_spend"] == 0
    assert body["platform"] == "effi"
