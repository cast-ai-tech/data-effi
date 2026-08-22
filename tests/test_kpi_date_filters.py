"""The date range, on every KPI - not only on the finance tab.

The bug this suite exists to prevent is not "the filter throws an error". It is
the quieter one: the filter is accepted, ignored, and the operator reads the
whole history believing they are reading one week. So the assertions here are
mostly about NUMBERS GOING DOWN. If a range that excludes half the guides
returns the same totals as no range at all, the filter is decorative.

THE FIXTURE, IN ONE PARAGRAPH
Two windows, deliberately far apart so nothing straddles them: an EARLY week
(3-5 August) with four guides, and a LATE week (12-14 August) with three. One
guide in each window stays open, and one sits in a carrier office, so `/aging`
and `/office-rescue` have rows in both. A rival tenant gets its own guide inside
the EARLY window - same country, same dates - because a leak that only shows up
outside the range being queried is a leak nobody would notice.
"""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID, uuid4

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@fechas.ec"
OWNER_PASSWORD = "una-clave-larga-de-fechas"
RIVAL_EMAIL = "owner@rival-fechas.ec"
RIVAL_PASSWORD = "otra-clave-larga-del-rival"

COUNTRY = "EC"
CURRENCY = "USD"

RIVAL_TENANT_ID = UUID("77777777-7777-7777-7777-777777777777")
RIVAL_CONNECTION_ID = UUID("66666666-6666-6666-6666-666666666666")

EARLY_FROM, EARLY_TO = date(2026, 8, 3), date(2026, 8, 5)
LATE_FROM, LATE_TO = date(2026, 8, 12), date(2026, 8, 14)

EARLY_GUIDES = 4
LATE_GUIDES = 3
ALL_GUIDES = EARLY_GUIDES + LATE_GUIDES

# Dispatch happens days AFTER creation, and the two windows are chosen so they
# do not overlap the creation windows at all. That is the point: a filter that
# quietly kept reading `created_date` would return the early guides for a
# creation window, and this fixture makes that show up as a count of zero.
DISPATCH_EARLY_FROM, DISPATCH_EARLY_TO = date(2026, 8, 8), date(2026, 8, 9)
DISPATCH_LATE_FROM, DISPATCH_LATE_TO = date(2026, 8, 17), date(2026, 8, 18)

# Delivery windows. Only three of the seven guides were ever delivered.
DELIVERY_FROM, DELIVERY_TO = date(2026, 8, 6), date(2026, 8, 15)
DELIVERED_GUIDES = 3
# The other four have no delivery date and cannot appear in ANY delivery range.
NO_DELIVERY_DATE = ALL_GUIDES - DELIVERED_GUIDES

# A window with nothing in it. Two years before any fixture guide exists.
VOID_FROM, VOID_TO = date(2024, 1, 1), date(2024, 1, 31)


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
            "full_name": "Dueña de Fechas",
            "tenant_name": "Fechas Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def tenant_id(client, owner_token) -> UUID:
    return UUID(client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"])


def _insert_shipment(
    cur,
    *,
    tenant,
    connection,
    tracking,
    status,
    created,
    delivered=None,
    dispatched=None,
    settled=None,
    carrier_id=None,
    product_id=None,
    geo_id=None,
    declared=50,
    freight=6,
    cogs=12,
    customer_hash=None,
):
    cur.execute(
        """
        INSERT INTO core.shipment
            (tenant_id, connection_id, country_code, tracking_number, customer_hash,
             carrier_id, product_id, geo_id, quantity, status_code, created_date,
             dispatched_batch_at, dispatched_at, delivered_at, settled_at,
             expected_delivery_date, currency_code, declared_value, cod_collected,
             freight_cost, product_cost, weight_kg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 1.5)
        """,
        (
            tenant, connection, COUNTRY, tracking, customer_hash, carrier_id, product_id,
            geo_id, status, created,
            dispatched or created,
            dispatched,                                # NULL unless the test sets one
            delivered, settled,
            delivered,                                 # promise met, so on_time is TRUE
            CURRENCY, declared,
            declared if status == "delivered" else None,
            freight, cogs,
        ),
    )


@pytest.fixture(scope="module")
def seeded(client, api_dsn, tenant_id) -> dict:
    """Four guides in the early window, three in the late one."""
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=uuid4(),
            country_code=COUNTRY,
            slug="fechas",
        )
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "SELECT id FROM core.connection WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        )
        connection_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO core.carrier (tenant_id, country_code, name, name_norm) "
            "VALUES (%s, %s, %s, core.normalize_text(%s)) RETURNING id",
            (tenant_id, COUNTRY, "Servientrega EC", "Servientrega EC"),
        )
        carrier_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO core.geo (tenant_id, country_code, level1_name, city_name, "
            "city_normalized) VALUES (%s, %s, %s, %s, core.normalize_text(%s)) RETURNING id",
            (tenant_id, COUNTRY, "Pichincha", "Quito", "Quito"),
        )
        geo_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO core.product (tenant_id, name, name_norm, unit_cost, currency_code) "
            "VALUES (%s, %s, core.normalize_text(%s), %s, %s) RETURNING id",
            (tenant_id, "Faja Fechas", "Faja Fechas", 12, CURRENCY),
        )
        product_id = cur.fetchone()[0]

        common = {
            "tenant": tenant_id,
            "connection": connection_id,
            "carrier_id": carrier_id,
            "product_id": product_id,
            "geo_id": geo_id,
        }

        # ---- EARLY WINDOW: 4 guides, one open, one waiting in an office -----
        _insert_shipment(
            cur, tracking="EC-E1", status="delivered", created=EARLY_FROM,
            dispatched=DISPATCH_EARLY_FROM, delivered=date(2026, 8, 6),
            settled=date(2026, 8, 9),
            customer_hash="a" * 64, **common,
        )
        _insert_shipment(
            cur, tracking="EC-E2", status="delivered", created=EARLY_FROM,
            dispatched=DISPATCH_EARLY_FROM, delivered=date(2026, 8, 7),
            customer_hash="a" * 64, **common,
        )
        _insert_shipment(
            cur, tracking="EC-E3", status="in_office", created=EARLY_TO,
            dispatched=DISPATCH_EARLY_TO, customer_hash="b" * 64, **common,
        )
        _insert_shipment(
            cur, tracking="EC-E4", status="returned", created=EARLY_TO,
            dispatched=DISPATCH_EARLY_TO, customer_hash="b" * 64, **common,
        )

        # ---- LATE WINDOW: 3 guides, one open, one waiting in an office ------
        _insert_shipment(
            cur, tracking="EC-L1", status="delivered", created=LATE_FROM,
            dispatched=DISPATCH_LATE_FROM, delivered=date(2026, 8, 15),
            settled=date(2026, 8, 18),
            customer_hash="c" * 64, **common,
        )
        _insert_shipment(
            cur, tracking="EC-L2", status="in_office", created=LATE_TO,
            dispatched=DISPATCH_LATE_TO, customer_hash="c" * 64, **common,
        )
        _insert_shipment(
            cur, tracking="EC-L3", status="in_transit", created=LATE_TO,
            dispatched=DISPATCH_LATE_TO, customer_hash="c" * 64, **common,
        )

        # Customer service worked in both windows, and on nothing in particular:
        # these interactions carry no shipment_id, which is exactly why /cs has
        # to filter by its own date and not by a guide's cohort.
        for day, outcome in (
            (EARLY_FROM, "confirmed"),
            (EARLY_TO, "rejected"),
            (LATE_FROM, "confirmed"),
        ):
            cur.execute(
                """
                INSERT INTO core.cs_interaction
                    (tenant_id, connection_id, country_code, interaction_date, outcome,
                     attempts, dedupe_key)
                VALUES (%s, %s, %s, %s, %s, 1, %s)
                """,
                (
                    tenant_id, connection_id, COUNTRY, day, outcome,
                    f"cs-{day:%Y%m%d}-{outcome}".ljust(64, "0"),
                ),
            )

        # Ad spend on one day of each window, so /cpa can be narrowed too.
        for day, spend in ((EARLY_FROM, 30), (LATE_FROM, 45)):
            cur.execute(
                """
                INSERT INTO core.ad_spend
                    (tenant_id, connection_id, country_code, spend_date, campaign_name,
                     spend, impressions, clicks, currency_code, dedupe_key)
                VALUES (%s, %s, %s, %s, 'Campaña', %s, 1000, 100, %s, %s)
                """,
                (
                    tenant_id, connection_id, COUNTRY, day, spend, CURRENCY,
                    f"ads-{day:%Y%m%d}".ljust(64, "0"),
                ),
            )

        conn.commit()

    return {"carrier_id": carrier_id, "product_id": product_id, "geo_id": geo_id}


@pytest.fixture(scope="module")
def rival_token(client, api_dsn, seeded) -> str:
    """A second tenant whose only guide sits INSIDE the early window.

    Same country, same dates. A range filter that lost its tenant clause would
    hand this guide to the first tenant and the count would go up by one.
    """
    from api.security import hash_password

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        seed_workspace(
            conn,
            tenant_id=RIVAL_TENANT_ID,
            connection_id=RIVAL_CONNECTION_ID,
            country_code=COUNTRY,
            slug="rival-fechas",
        )
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.app_user (tenant_id, email, password_hash, full_name, role) "
            "VALUES (%s, %s, %s, %s, 'owner') ON CONFLICT DO NOTHING",
            (RIVAL_TENANT_ID, RIVAL_EMAIL, hash_password(RIVAL_PASSWORD), "Dueño Rival"),
        )
        # Since 032 a person reaches a company through core.membership, not
        # through app_user.tenant_id alone: /auth/login reads the companies from
        # core.user_workspaces, and a user with no membership is refused with
        # "Tu usuario no pertenece a ninguna sociedad". `tenant_id` now only
        # decides WHICH company opens first, so seeding a rival straight in SQL
        # has to grant the membership too.
        cur.execute(
            "INSERT INTO core.membership (user_id, tenant_id, role) "
            "SELECT id, tenant_id, role FROM core.app_user WHERE lower(email) = lower(%s) "
            "ON CONFLICT (user_id, tenant_id) DO NOTHING",
            (RIVAL_EMAIL,),
        )
        _insert_shipment(
            cur, tenant=RIVAL_TENANT_ID, connection=RIVAL_CONNECTION_ID,
            tracking="RIVAL-E1", status="delivered", created=EARLY_FROM,
            dispatched=DISPATCH_EARLY_FROM, delivered=date(2026, 8, 6),
            declared=999, customer_hash="d" * 64,
        )
        conn.commit()

    response = client.post(
        "/auth/login", json={"email": RIVAL_EMAIL, "password": RIVAL_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# =============================================================================
# The endpoint inventory
#
# Every KPI endpoint appears here exactly once, with the basis it must report.
# A new endpoint that forgets the range shows up as a missing entry, because the
# coverage test below compares this table against the router's own route list.
# =============================================================================

# (path, extra query, date_basis) - the sixteen that take a range.
RANGED_ENDPOINTS: list[tuple[str, dict[str, str], str]] = [
    ("/kpis/daily-contribution", {"country": COUNTRY}, "creacion"),
    ("/kpis/contribution-split", {"country": COUNTRY}, "creacion"),
    ("/kpis/carriers", {"country": COUNTRY}, "creacion"),
    ("/kpis/geo", {"country": COUNTRY}, "creacion"),
    ("/kpis/products", {"country": COUNTRY}, "creacion"),
    ("/kpis/cohorts", {"country": COUNTRY, "only_observable": "false"}, "creacion"),
    ("/kpis/aging", {"country": COUNTRY}, "creacion"),
    ("/kpis/cs", {"country": COUNTRY}, "interaccion"),
    ("/kpis/cpa", {"country": COUNTRY}, "pauta"),
    ("/kpis/dropshipping-margin", {"country": COUNTRY}, "creacion"),
    ("/kpis/fulfillment", {"country": COUNTRY}, "creacion"),
    ("/kpis/office-rescue", {"country": COUNTRY}, "creacion"),
    ("/kpis/freight", {"country": COUNTRY}, "creacion"),
    ("/kpis/cash-cycle", {"country": COUNTRY}, "creacion"),
    ("/kpis/problem-rate", {"country": COUNTRY}, "creacion"),
    ("/kpis/global", {}, "creacion"),
]

# The one that does not, and says so.
UNRANGED_ENDPOINTS = [("/kpis/layout", {"country": COUNTRY})]


def get(client, token, path, params=None, **extra):
    return client.get(path, params={**(params or {}), **extra}, headers=auth(token))


def rows_of(response) -> list[dict]:
    body = response.json()
    return body["rows"] if isinstance(body, dict) and "rows" in body else body["widgets"]


# =============================================================================
# 1. Every endpoint is covered, and every endpoint reports its basis
# =============================================================================


def test_every_kpi_route_appears_in_this_suite(client):
    """The inventory above is the contract. This keeps it honest.

    A KPI added later without a date range will fail here rather than ship as a
    picker that silently does nothing - which is the exact failure this whole
    change was written to remove.
    """
    schema = client.get("/openapi.json").json()
    routed = {p for p in schema["paths"] if p.startswith("/kpis/")}
    covered = {p for p, _, _ in RANGED_ENDPOINTS} | {p for p, _ in UNRANGED_ENDPOINTS}
    assert routed == covered, f"sin cubrir: {routed - covered}; de más: {covered - routed}"


@pytest.mark.parametrize("path,params,basis", RANGED_ENDPOINTS, ids=lambda v: str(v))
def test_a_call_without_dates_covers_the_whole_history_and_still_names_its_basis(
    client, owner_token, seeded, path, params, basis
):
    response = get(client, owner_token, path, params)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["date_basis"] == basis
    # Absent means "todo el histórico", and the response says the range was
    # empty rather than echoing dates the caller never sent.
    assert body["date_from"] is None
    assert body["date_to"] is None
    assert body["rows"], f"{path} debería tener datos sin filtro"


@pytest.mark.parametrize("path,params", UNRANGED_ENDPOINTS, ids=lambda v: str(v))
def test_an_endpoint_without_a_range_reports_a_null_basis(
    client, owner_token, seeded, path, params
):
    """`/kpis/layout` answers "which widgets exist", which no date narrows.

    It reports `date_basis: null` instead of accepting the parameters and
    ignoring them - the UI can grey its picker out on this call.
    """
    response = get(client, owner_token, path, params)
    assert response.status_code == 200, response.text
    assert response.json()["date_basis"] is None


# =============================================================================
# 2. A range actually cuts
# =============================================================================


@pytest.mark.parametrize("path,params,basis", RANGED_ENDPOINTS, ids=lambda v: str(v))
def test_a_range_narrows_the_answer_and_echoes_itself(
    client, owner_token, seeded, path, params, basis
):
    """The early and late windows must not describe the same operation.

    Compared as a whole response rather than row by row: some of these views are
    one row per carrier and stay one row per carrier whatever the range, but the
    numbers inside them have to move.
    """
    early = get(client, owner_token, path, params, date_from=EARLY_FROM, date_to=EARLY_TO)
    late = get(client, owner_token, path, params, date_from=LATE_FROM, date_to=LATE_TO)
    assert early.status_code == 200, early.text
    assert late.status_code == 200, late.text

    assert early.json()["date_from"] == EARLY_FROM.isoformat()
    assert early.json()["date_to"] == EARLY_TO.isoformat()
    assert early.json()["date_basis"] == basis

    assert early.json()["rows"], f"{path} no devolvió nada para la ventana temprana"
    assert late.json()["rows"], f"{path} no devolvió nada para la ventana tardía"
    assert early.json()["rows"] != late.json()["rows"], (
        f"{path} devuelve lo mismo para dos ventanas distintas: el filtro no se aplicó"
    )


def test_the_shipment_count_matches_the_window_exactly(client, owner_token, seeded):
    """The arithmetic check, on the one endpoint where it is unambiguous."""
    def shipments(**kwargs) -> int:
        body = get(client, owner_token, "/kpis/global", {}, **kwargs).json()
        return body["rows"][0]["shipments"]

    assert shipments() == ALL_GUIDES
    assert shipments(date_from=EARLY_FROM, date_to=EARLY_TO) == EARLY_GUIDES
    assert shipments(date_from=LATE_FROM, date_to=LATE_TO) == LATE_GUIDES
    # A half-open range: only `date_to` given means "todo hasta esa fecha".
    assert shipments(date_to=EARLY_TO) == EARLY_GUIDES
    assert shipments(date_from=LATE_FROM) == LATE_GUIDES


def test_a_boundary_date_is_inside_the_range(client, owner_token, seeded):
    """`>=` and `<=`, not `>` and `<`.

    Both windows begin and end on a day that has guides on it, so an exclusive
    comparison would quietly drop them.
    """
    body = get(
        client, owner_token, "/kpis/global", {},
        date_from=EARLY_FROM, date_to=EARLY_FROM,
    ).json()
    # EC-E1 and EC-E2 were both created on the first day of the window.
    assert body["rows"][0]["shipments"] == 2


def test_an_open_guide_survives_the_filter(client, owner_token, seeded):
    """The reason the basis is the creation date and not the delivery date.

    EC-L3 is still in transit: it has no delivery date and no settlement date.
    A filter on either would erase it, and an open guide is the one the operator
    still has to do something about.
    """
    body = get(
        client, owner_token, "/kpis/aging", {"country": COUNTRY},
        date_from=LATE_FROM, date_to=LATE_TO,
    ).json()
    assert sum(r["shipments"] for r in body["rows"]) >= 1
    assert body["date_basis"] == "creacion"


def test_cash_cycle_keeps_the_unsettled_guides_inside_a_range(client, owner_token, seeded):
    """Filtering by settlement date would have deleted exactly this row.

    The late window has one delivered guide that HAS settled and none that has
    not; the early window has one delivered-and-settled and one delivered-and-not.
    `delivered_unsettled` is only non-zero if the filter used the creation date.
    """
    early = get(
        client, owner_token, "/kpis/cash-cycle", {"country": COUNTRY},
        date_from=EARLY_FROM, date_to=EARLY_TO,
    ).json()["rows"][0]

    assert early["settled"] == 1
    assert early["delivered_unsettled"] == 1, (
        "una guía entregada y sin liquidar desapareció: el filtro usó settled_at"
    )
    assert early["cash_in_transit"] > 0


# =============================================================================
# 3. An empty range is empty, and still says what it looked for
# =============================================================================


@pytest.mark.parametrize("path,params,basis", RANGED_ENDPOINTS, ids=lambda v: str(v))
def test_a_window_with_no_data_returns_no_rows_and_keeps_its_basis(
    client, owner_token, seeded, path, params, basis
):
    """Zero rows is an answer, and it has to be a legible one.

    "No hay datos" and "no hay guías CREADAS entre esas dos fechas" are different
    sentences, and the second is the one the operator can act on. That is the
    whole reason `date_basis` lives on the response instead of on each row -
    there are no rows here to carry it.
    """
    response = get(client, owner_token, path, params, date_from=VOID_FROM, date_to=VOID_TO)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["rows"] == []
    assert body["date_basis"] == basis
    assert body["date_from"] == VOID_FROM.isoformat()
    assert body["date_to"] == VOID_TO.isoformat()


# =============================================================================
# 4. An inverted range is a mistake, not an empty result
# =============================================================================


@pytest.mark.parametrize("path,params,basis", RANGED_ENDPOINTS, ids=lambda v: str(v))
def test_an_inverted_range_is_rejected_with_a_readable_message(
    client, owner_token, seeded, path, params, basis
):
    response = get(client, owner_token, path, params, date_from=LATE_TO, date_to=EARLY_FROM)
    assert response.status_code == 422, response.text

    error = response.json()["error"]
    assert error["code"] == "invalid_date_range"
    # Written for a person, in Spanish, naming both dates so they can see which
    # way round they typed them.
    assert "invertido" in error["message"]
    assert LATE_TO.isoformat() in error["message"]
    assert EARLY_FROM.isoformat() in error["message"]


def test_the_two_dates_being_equal_is_a_valid_range(client, owner_token, seeded):
    """`date_from == date_to` is one day, not an error."""
    response = get(
        client, owner_token, "/kpis/global", {},
        date_from=EARLY_FROM, date_to=EARLY_FROM,
    )
    assert response.status_code == 200, response.text


def test_a_date_that_is_not_a_date_is_a_validation_error(client, owner_token, seeded):
    """Still 422, but a different code, so the client can point at the field."""
    response = get(client, owner_token, "/kpis/global", {}, date_from="ayer")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# =============================================================================
# 5. Two tenants, one range, no leaks
# =============================================================================


@pytest.mark.parametrize("path,params,basis", RANGED_ENDPOINTS, ids=lambda v: str(v))
def test_a_range_does_not_open_a_door_between_tenants(
    client, owner_token, rival_token, seeded, path, params, basis
):
    """The rival's only guide sits inside the early window, on purpose.

    Row-level security lives in the mart views, and migration 018 moved the
    aggregation into functions - a `SECURITY DEFINER` slip there would show up
    as the rival's numbers appearing in the owner's answer for the exact range
    both tenants have data in.
    """
    mine = get(
        client, owner_token, path, params, date_from=EARLY_FROM, date_to=EARLY_TO
    ).json()["rows"]
    theirs = get(
        client, rival_token, path, params, date_from=EARLY_FROM, date_to=EARLY_TO
    ).json()["rows"]

    assert mine != theirs, f"{path}: dos inquilinos ven exactamente lo mismo"


def test_the_rivals_guide_never_lands_in_the_owners_totals(
    client, owner_token, rival_token, seeded
):
    """The countable version of the test above.

    The rival's guide is declared at 999 - an amount nothing in the owner's
    fixture comes close to - so a leak moves the revenue, not just the shape.
    """
    mine = get(
        client, owner_token, "/kpis/global", {}, date_from=EARLY_FROM, date_to=EARLY_TO
    ).json()["rows"][0]
    theirs = get(
        client, rival_token, "/kpis/global", {}, date_from=EARLY_FROM, date_to=EARLY_TO
    ).json()["rows"][0]

    assert mine["shipments"] == EARLY_GUIDES
    assert theirs["shipments"] == 1
    assert theirs["revenue"] == 999.0
    assert mine["revenue"] != theirs["revenue"]


# =============================================================================
# 6. The clientes tab reads the same picker
# =============================================================================


def test_the_customers_table_narrows_to_the_range(client, owner_token, seeded):
    """Three customers overall; two bought only in the early window.

    `f_customer_metrics` regroups from the guides rather than clipping a
    precomputed total, which is what keeps `main_city` - a mode() - correct.
    """
    whole = client.get(
        "/customers", params={"country": COUNTRY}, headers=auth(owner_token)
    ).json()
    early = client.get(
        "/customers",
        params={"country": COUNTRY, "date_from": EARLY_FROM, "date_to": EARLY_TO},
        headers=auth(owner_token),
    ).json()

    assert whole["date_basis"] == "creacion"
    assert whole["date_from"] is None
    assert whole["total"] == 3
    assert early["total"] == 2
    assert early["date_from"] == EARLY_FROM.isoformat()


def test_the_customers_table_rejects_an_inverted_range(client, owner_token, seeded):
    response = client.get(
        "/customers",
        params={"country": COUNTRY, "date_from": LATE_TO, "date_to": EARLY_FROM},
        headers=auth(owner_token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date_range"


# =============================================================================
# 7. Parity: the function and the view compute the same number
#
# Migration 018 could not repoint the views at the functions - a view reads its
# tables as the view owner, but a function runs as the CALLER, and threading the
# aggregates through one would have cost `norte_readonly` its access to `core`
# and `stg` (the header of 018 has the full reasoning). The price is that each
# of these thirteen aggregates is now written twice.
#
# This is the test that keeps that price honest. With no range, a function must
# return EXACTLY what its view returns - same rows, same columns, same order.
# The day someone fixes a margin in one place and not the other, this fails
# before a dashboard shows two different numbers for the same thing.
# =============================================================================

PARITY_PAIRS = [
    ("v_contribution_split", "f_contribution_split", "country_code"),
    ("v_carrier_effectiveness", "f_carrier_effectiveness", "carrier_name"),
    ("v_geo_performance", "f_geo_performance", "city_name, level1_name"),
    ("v_product_performance", "f_product_performance", "product_name"),
    ("v_aging", "f_aging", "bucket_order"),
    ("v_dropshipping_margin", "f_dropshipping_margin", "product_name"),
    ("v_fulfillment_sla", "f_fulfillment_sla", "carrier_name, service_level"),
    ("v_office_rescue", "f_office_rescue", "carrier_name, city_name"),
    ("v_freight_analysis", "f_freight_analysis", "carrier_name, service_level"),
    ("v_cash_cycle", "f_cash_cycle", "country_code"),
    ("v_problem_rate", "f_problem_rate", "carrier_name"),
    ("v_global_summary", "f_global_summary", "country_code"),
    ("v_customer_metrics", "f_customer_metrics", "customer_hash"),
]


@pytest.mark.parametrize("view,function,order", PARITY_PAIRS, ids=lambda v: str(v))
def test_the_function_and_the_view_agree_column_for_column(
    api_dsn, tenant_id, seeded, view, function, order
):
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, true)", (str(tenant_id),))

        # Both statements are built only from literals in PARITY_PAIRS above;
        # nothing here comes from a request, hence the two noqa markers.
        cur.execute(f"SELECT * FROM mart.{view} ORDER BY {order}")  # noqa: S608
        view_columns = [c.name for c in cur.description]
        view_rows = cur.fetchall()

        cur.execute(f"SELECT * FROM mart.{function}(NULL, NULL) ORDER BY {order}")  # noqa: S608
        function_columns = [c.name for c in cur.description]
        function_rows = cur.fetchall()

    assert function_columns == view_columns, (
        f"mart.{function} y mart.{view} ya no tienen las mismas columnas"
    )
    assert view_rows, f"mart.{view} no devolvió nada: la comparación no probaría nada"
    assert function_rows == view_rows, (
        f"mart.{function}(NULL, NULL) y mart.{view} devuelven números distintos"
    )


# =============================================================================
# 8. Three dates, and knowing which one you are looking through
#
# `date_field` picks between the dates a guide has. The fixture puts dispatch
# days AFTER the creation window and outside it, so an endpoint that ignored the
# parameter and kept reading `created_date` returns a different count than the
# one asserted here rather than the same one by luck.
# =============================================================================

# The thirteen that honour `date_field`, and the four whose basis is fixed.
FIELD_AWARE = [
    (path, params)
    for path, params, _ in RANGED_ENDPOINTS
    if path not in {"/kpis/daily-contribution", "/kpis/cohorts", "/kpis/cs", "/kpis/cpa"}
]
FIXED_BASIS = [
    ("/kpis/daily-contribution", {"country": COUNTRY}, "creacion"),
    ("/kpis/cohorts", {"country": COUNTRY, "only_observable": "false"}, "creacion"),
    ("/kpis/cs", {"country": COUNTRY}, "interaccion"),
    ("/kpis/cpa", {"country": COUNTRY}, "pauta"),
]


def test_the_default_is_still_the_creation_date(client, owner_token, seeded):
    """Nothing about migration 020 moves the default.

    Omitting the parameter and asking for `creacion` have to be the same
    request, or every caller written before this change quietly means something
    new.
    """
    implicit = get(
        client, owner_token, "/kpis/global", {}, date_from=EARLY_FROM, date_to=EARLY_TO
    ).json()
    explicit = get(
        client, owner_token, "/kpis/global", {},
        date_from=EARLY_FROM, date_to=EARLY_TO, date_field="creacion",
    ).json()

    assert implicit == explicit
    assert implicit["date_basis"] == "creacion"
    assert implicit["rows"][0]["shipments"] == EARLY_GUIDES


def test_the_same_window_means_different_guides_on_each_date(client, owner_token, seeded):
    """The one test that proves the column really changed.

    The creation window holds four guides. Not one of them was DISPATCHED in
    that window - dispatch happens days later - so `despacho` over the same two
    dates must answer zero. An endpoint still reading `created_date` answers
    four, and that is the failure this whole parameter exists to prevent.
    """
    def shipments(**kwargs) -> int:
        rows = get(client, owner_token, "/kpis/global", {}, **kwargs).json()["rows"]
        return rows[0]["shipments"] if rows else 0

    assert shipments(date_from=EARLY_FROM, date_to=EARLY_TO) == EARLY_GUIDES
    assert shipments(date_from=EARLY_FROM, date_to=EARLY_TO, date_field="despacho") == 0

    # And the dispatch window picks exactly those four guides back up.
    assert shipments(
        date_from=DISPATCH_EARLY_FROM, date_to=DISPATCH_EARLY_TO, date_field="despacho"
    ) == EARLY_GUIDES
    assert shipments(
        date_from=DISPATCH_LATE_FROM, date_to=DISPATCH_LATE_TO, date_field="despacho"
    ) == LATE_GUIDES


@pytest.mark.parametrize("path,params", FIELD_AWARE, ids=lambda v: str(v))
@pytest.mark.parametrize("field", ["creacion", "despacho", "entrega"])
def test_date_basis_reports_the_field_that_was_applied(
    client, owner_token, seeded, path, params, field
):
    response = get(
        client, owner_token, path, params,
        date_from=EARLY_FROM, date_to=LATE_TO, date_field=field,
    )
    assert response.status_code == 200, response.text
    assert response.json()["date_basis"] == field


@pytest.mark.parametrize("path,params,basis", FIXED_BASIS, ids=lambda v: str(v))
def test_a_fixed_basis_endpoint_says_so_instead_of_pretending(
    client, owner_token, seeded, path, params, basis
):
    """Four endpoints cannot honour `date_field`, and none of them lies about it.

    They accept the parameter - the frontend sends one field to every widget -
    and report the basis they really used, so the interface can label the card
    "siempre por fecha de creacion" rather than let the operator assume the
    picker reached it.
    """
    response = get(
        client, owner_token, path, params,
        date_from=EARLY_FROM, date_to=LATE_TO, date_field="entrega",
    )
    assert response.status_code == 200, response.text
    assert response.json()["date_basis"] == basis


# -----------------------------------------------------------------------------
# The delivery-date trap
# -----------------------------------------------------------------------------


def test_filtering_by_delivery_hides_every_guide_still_in_the_street(
    client, owner_token, seeded
):
    """The whole reason `excluded_no_date` exists.

    Four of the seven guides have no delivery date - they are in transit, in an
    office, or came back. A delivery-date filter cannot include them, and an
    operator who is not told would read three guides as their whole operation.
    """
    body = get(
        client, owner_token, "/kpis/global", {},
        date_from=DELIVERY_FROM, date_to=DELIVERY_TO, date_field="entrega",
    ).json()

    assert body["date_basis"] == "entrega"
    assert body["rows"][0]["shipments"] == DELIVERED_GUIDES
    assert body["excluded_no_date"] == NO_DELIVERY_DATE, (
        "la respuesta tiene que decir cuantas guias no pueden entrar en el filtro"
    )


def test_aging_goes_empty_under_a_delivery_filter_and_explains_why(
    client, owner_token, seeded
):
    """Not a bug. An open guide has no delivery date, by definition.

    "Que guias abiertas se entregaron en enero" has no answer, and zero rows
    plus a count of what was left out is the honest way to give it.
    """
    body = get(
        client, owner_token, "/kpis/aging", {"country": COUNTRY},
        date_from=DELIVERY_FROM, date_to=DELIVERY_TO, date_field="entrega",
    ).json()

    assert body["rows"] == []
    assert body["date_basis"] == "entrega"
    assert body["excluded_no_date"] == NO_DELIVERY_DATE


def test_a_creation_filter_excludes_nobody(client, owner_token, seeded):
    """`created_date` is NOT NULL, so the count is zero - read from SQL here,
    not assumed by the router."""
    body = get(
        client, owner_token, "/kpis/global", {},
        date_from=EARLY_FROM, date_to=EARLY_TO, date_field="creacion",
    ).json()
    assert body["excluded_no_date"] == 0


def test_without_a_range_nothing_is_excluded_and_no_count_is_invented(
    client, owner_token, seeded
):
    """No range means no filter, so "cuantas quedaron fuera" has no answer:
    null, rather than a count of guides that are all present anyway."""
    body = get(client, owner_token, "/kpis/global", {}, date_field="entrega").json()

    assert body["excluded_no_date"] is None
    assert body["rows"][0]["shipments"] == ALL_GUIDES


@pytest.mark.parametrize("path,params,basis", FIXED_BASIS, ids=lambda v: str(v))
def test_a_fixed_basis_endpoint_never_reports_an_exclusion_it_did_not_measure(
    client, owner_token, seeded, path, params, basis
):
    """`/kpis/cs` and `/kpis/cpa` count interactions and spend, not guides, so a
    guide exclusion count would come from the wrong universe entirely."""
    body = get(
        client, owner_token, path, params,
        date_from=EARLY_FROM, date_to=LATE_TO, date_field="entrega",
    ).json()

    if basis == "creacion":
        assert body["excluded_no_date"] == 0
    else:
        assert body["excluded_no_date"] is None


# -----------------------------------------------------------------------------
# Bad input, and tenants
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("path,params", FIELD_AWARE, ids=lambda v: str(v))
def test_an_unknown_date_field_is_refused_by_name(
    client, owner_token, seeded, path, params
):
    response = get(client, owner_token, path, params, date_field="liquidacion")
    assert response.status_code == 422, response.text

    error = response.json()["error"]
    assert error["code"] == "invalid_date_field"
    # The message names what IS available, so the caller can fix it in one go.
    assert "liquidacion" in error["message"]
    for valid in ("creacion", "despacho", "entrega"):
        assert valid in error["message"]


def test_a_bad_field_is_refused_even_with_no_range(client, owner_token, seeded):
    """The value is wrong on its own; it does not become wrong only once used."""
    response = get(
        client, owner_token, "/kpis/carriers", {"country": COUNTRY}, date_field="entregada"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date_field"


def test_the_dispatch_filter_does_not_open_a_door_between_tenants(
    client, owner_token, rival_token, seeded
):
    """The rival's guide was dispatched in the same window as the owner's four."""
    mine = get(
        client, owner_token, "/kpis/global", {},
        date_from=DISPATCH_EARLY_FROM, date_to=DISPATCH_EARLY_TO, date_field="despacho",
    ).json()["rows"][0]
    theirs = get(
        client, rival_token, "/kpis/global", {},
        date_from=DISPATCH_EARLY_FROM, date_to=DISPATCH_EARLY_TO, date_field="despacho",
    ).json()["rows"][0]

    assert mine["shipments"] == EARLY_GUIDES
    assert theirs["shipments"] == 1
    assert theirs["revenue"] == 999.0


def test_the_customers_table_takes_the_same_field(client, owner_token, seeded):
    """The clientes tab reads one picker with the rest of the dashboard."""
    creation = client.get(
        "/customers",
        params={"country": COUNTRY, "date_from": EARLY_FROM, "date_to": EARLY_TO},
        headers=auth(owner_token),
    ).json()
    dispatch = client.get(
        "/customers",
        params={
            "country": COUNTRY,
            "date_from": DISPATCH_EARLY_FROM,
            "date_to": DISPATCH_EARLY_TO,
            "date_field": "despacho",
        },
        headers=auth(owner_token),
    ).json()

    assert creation["date_basis"] == "creacion"
    assert dispatch["date_basis"] == "despacho"
    assert dispatch["excluded_no_date"] == 0
    # The same four guides, so the same two customers, reached two different ways.
    assert dispatch["total"] == creation["total"] == 2


def test_the_customers_table_refuses_an_unknown_field(client, owner_token, seeded):
    response = client.get(
        "/customers",
        params={"country": COUNTRY, "date_field": "liquidacion"},
        headers=auth(owner_token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date_field"


# =============================================================================
# 9. The copilot must still be able to READ what it is allowed to read
#
# This section exists because migration 021 broke that and every test stayed
# green. It rebuilt `mart.v_carrier_effectiveness` as a call to
# `mart.f_carrier_effectiveness`, and the copilot - which has no USAGE on `stg`
# - started getting "permission denied for schema stg" from a view it had read
# for weeks.
#
# The existing guard in test_ai_memory.py asks `has_table_privilege`, and that
# kept answering TRUE the whole time, because the GRANT on the view was never
# the thing that broke. A view reads its TABLES as its owner, but the body of a
# FUNCTION it calls runs as the CALLER. The only question that catches this is
# the one an actual copilot query asks: does `SELECT` work.
#
# So these tests connect as `norte_readonly` and select. No tenant GUC: the
# views fail closed and return zero rows without one, which is fine - a
# permission error raises either way, and that is what is being watched.
# =============================================================================


@pytest.fixture(scope="module")
def readonly_dsn(api_dsn) -> str:
    from tests.pg_helpers import resolve_readonly_test_dsn

    dsn = resolve_readonly_test_dsn()
    if not dsn:
        pytest.skip("No DATABASE_URL_READONLY configured")
    try:
        with psycopg.connect(dsn):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"norte_readonly unreachable: {exc}")
    return dsn


def _allowed_views() -> list[str]:
    from ai.nl2sql import ALLOWED_VIEWS

    return sorted(ALLOWED_VIEWS)


@pytest.mark.parametrize("view", _allowed_views())
def test_the_copilot_can_actually_select_every_view_on_its_allow_list(
    readonly_dsn, seeded, view
):
    """Reads it, rather than asking whether it is allowed to.

    Driven off `ai.nl2sql.ALLOWED_VIEWS` itself, not a copied list, so a view
    added to the copilot's reach is covered here the moment it is added.
    """
    with psycopg.connect(readonly_dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(f"SELECT count(*) FROM mart.{view}")  # noqa: S608
            cur.fetchone()
        except psycopg.errors.InsufficientPrivilege as exc:
            pytest.fail(
                f"el copiloto no puede leer mart.{view}, que SÍ está en su "
                f"lista blanca: {str(exc).splitlines()[0]}"
            )


def test_the_copilot_cannot_select_any_mart_view_outside_its_allow_list(
    readonly_dsn, api_dsn, seeded
):
    """The other half of the same rule, and the reason the fix was not "just
    grant norte_readonly access to stg".

    Widening that role until the carrier view worked again would have handed it
    `stg.v_shipment_economics` - one row per guide, tracking numbers included -
    which is exactly what migrations 011, 015 and 025 spent their time closing.

    Derived from the catalogue rather than a copied list, so a mart view added
    tomorrow is covered the moment it exists: if it is not on the allow-list,
    the copilot must not be able to read it.
    """
    from ai.nl2sql import ALLOWED_VIEWS

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mart' AND c.relkind = 'v'"
        )
        outside = sorted({r[0] for r in cur.fetchall()} - ALLOWED_VIEWS)

    assert outside, "sin vistas fuera de la lista blanca no se prueba nada"

    reachable = []
    for view in outside:
        with psycopg.connect(readonly_dsn) as conn, conn.cursor() as cur:
            try:
                cur.execute(f"SELECT count(*) FROM mart.{view}")  # noqa: S608
                cur.fetchone()
                reachable.append(view)
            except psycopg.errors.InsufficientPrivilege:
                pass

    assert not reachable, (
        f"el copiloto puede leer vistas que no están en su lista blanca: "
        f"{', '.join(reachable)}"
    )


def test_the_grants_and_the_allow_list_describe_exactly_the_same_set(api_dsn, seeded):
    """Layer 1 and layer 3 have to agree, or one of them is decoration.

    The allow-list in `ai/nl2sql.py` is what the validator enforces; the GRANTs
    are what the database enforces. When they drift, the looser one is the real
    policy and the tighter one is a comment. Asserting they are the same set is
    what keeps "adding a view here is a deliberate act" true in both files.
    """
    from ai.nl2sql import ALLOWED_VIEWS

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname, "
            "  has_table_privilege('norte_readonly', 'mart.' || c.relname, 'SELECT') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'mart' AND c.relkind = 'v'"
        )
        granted = {name for name, ok in cur.fetchall() if ok}

    assert granted - ALLOWED_VIEWS == set(), (
        f"con GRANT pero fuera de la lista blanca: {sorted(granted - ALLOWED_VIEWS)}"
    )
    assert ALLOWED_VIEWS - granted == set(), (
        f"en la lista blanca pero sin GRANT: {sorted(ALLOWED_VIEWS - granted)}"
    )


def test_the_copilot_cannot_reach_the_staging_schema_at_all(readonly_dsn, seeded):
    """The specific door migration 021 propped open, nailed shut."""
    with psycopg.connect(readonly_dsn) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT count(*) FROM stg.v_shipment_economics")


def test_a_mart_view_never_reads_through_one_of_the_range_functions(api_dsn, seeded):
    """The structural version of the rule, so the next person does not have to
    rediscover it from an error message.

    Migration 018 gave the KPIs their range through `mart.f_*` functions and
    deliberately left the views alone. A view that calls one stops running its
    body as its owner, and the copilot loses it. This catches the pattern in the
    catalogue rather than waiting for a permission error to surface.
    """
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, pg_get_viewdef(c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'mart' AND c.relkind = 'v'
            """
        )
        offenders = [
            name for name, body in cur.fetchall() if "mart.f_" in (body or "")
        ]

    assert not offenders, (
        "estas vistas de mart llaman a una función de rango y por eso dejan de "
        f"correr con los privilegios de su dueño: {', '.join(offenders)}. "
        "Ver la cabecera de la migración 023."
    )


def test_the_carrier_table_carries_its_sample_flag_all_the_way_to_the_json(
    client, owner_token, seeded
):
    """`sample_quality` reaching the API, not just the view.

    Migration 021 added the column and the Pydantic model dropped it on the way
    out, so the flag existed in SQL and never reached a screen. A field that is
    computed and then discarded is worse than one that was never added: the
    query cost is paid and the operator still cannot tell a measured rate from
    one built on two guides.
    """
    body = get(client, owner_token, "/kpis/carriers", {"country": COUNTRY}).json()
    assert body["rows"], "sin filas no se prueba nada"

    for row in body["rows"]:
        assert "sample_quality" in row, "el campo se pierde entre la vista y el JSON"
        assert row["sample_quality"] in ("suficiente", "muestra_corta")


def test_a_narrow_window_marks_the_carrier_rate_as_a_short_sample(
    client, owner_token, seeded
):
    """The whole point of the flag: seven guides is not a delivery rate.

    The fixture has one carrier and well under ten terminal guides, so every row
    in this window has to come back flagged. If this ever reads 'suficiente' the
    threshold moved and the UI is presenting arithmetic as evidence.
    """
    body = get(
        client, owner_token, "/kpis/carriers", {"country": COUNTRY},
        date_from=EARLY_FROM, date_to=EARLY_TO,
    ).json()

    assert body["rows"]
    assert all(r["sample_quality"] == "muestra_corta" for r in body["rows"])
