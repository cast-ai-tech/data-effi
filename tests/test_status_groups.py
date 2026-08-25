"""The five status groups (migration 045), Dropi's vocabulary (040) and the one
terminal step the merge allows (lost -> compensated, 045).

Two copies exist on purpose - `pipeline/mapping.py` for the engine that must
work without a database, `core.status_canon.status_group` for the views - and
this file is what keeps them from drifting apart.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from pipeline.ingest import ShipmentRecord, merge_shipment
from pipeline.mapping import (
    STATUS_ALIASES,
    STATUS_CANON,
    STATUS_GROUP_LABELS,
    STATUS_GROUPS,
    STATUS_TERMINAL_EXCEPTIONS,
    resolve_status,
    status_group,
)
from pipeline.models import ShipmentInput

GROUPS = {"entregada", "devolucion", "en_transito", "novedad", "indemnizacion"}
_TENANT = UUID("00000000-0000-0000-0000-000000000001")
_CONNECTION = UUID("00000000-0000-0000-0000-000000000002")


def test_every_canonical_status_has_exactly_one_group():
    assert set(STATUS_GROUPS) == set(STATUS_CANON)
    assert set(STATUS_GROUPS.values()) == GROUPS
    assert set(STATUS_GROUP_LABELS) == GROUPS
    # The order the operator reads them in.
    assert list(STATUS_GROUP_LABELS) == [
        "entregada", "en_transito", "novedad", "devolucion", "indemnizacion",
    ]


def test_the_groups_respect_the_flags_of_the_canonical_ladder():
    """A group is a coarser reading of the flags, never a contradiction of them."""
    for code, status in STATUS_CANON.items():
        group = status_group(code)
        if status.is_delivered:
            assert group == "entregada", code
        elif status.is_returned or code == "cancelled":
            assert group == "devolucion", code
        elif status.is_terminal:
            assert group == "indemnizacion", code
        else:
            assert group in {"en_transito", "novedad"}, code


def test_office_and_issue_count_as_novedad():
    """Both are a parcel that stopped moving and needs a call. The report has
    one column for that, and "en oficina" must not disappear into "en tránsito"."""
    assert status_group("in_office") == "novedad"
    assert status_group("delivery_issue") == "novedad"


def test_lost_and_compensated_are_the_indemnity_column():
    """A siniestro is an indemnity still owed; a compensated guide is the same
    parcel with the money paid back. Both belong to the operator's last column."""
    assert status_group("lost") == "indemnizacion"
    assert status_group("compensated") == "indemnizacion"
    assert STATUS_CANON["compensated"].is_terminal
    assert STATUS_CANON["compensated"].sort_order > STATUS_CANON["lost"].sort_order


def test_a_cancelled_guide_counts_as_a_return():
    """Operator's decision (045): the sale is lost and the product is back."""
    assert status_group("cancelled") == "devolucion"


def test_an_unknown_code_reads_as_still_moving():
    assert status_group("algo-raro") == "en_transito"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Incidencia en ruta", "delivery_issue"),
        ("INCIDENCIA EN RUTA", "delivery_issue"),
        ("Incidencia", "delivery_issue"),
        ("Entregado", "delivered"),
        ("En ruta", "in_transit"),
        ("Devolución", "returning"),
        ("Devolución a origen", "returning"),
        ("Entregada a destino", "delivered"),
        ("Siniestro", "lost"),
        ("Indemnizada", "compensated"),
        ("INDEMNIZACIÓN", "compensated"),
        ("Guía indemnizada", "compensated"),
    ],
)
def test_dropi_and_effi_words_land_on_the_same_ladder(raw, expected):
    code, recognized = resolve_status(raw)
    assert recognized, raw
    assert code == expected


def test_the_two_platforms_agree_on_the_screen_group():
    """"Entregada a destino" (Effi) and "Entregado" (Dropi) are one column."""
    effi, _ = resolve_status("Entregada a destino")
    dropi, _ = resolve_status("Entregado")
    assert status_group(effi) == status_group(dropi) == "entregada"

    effi_issue, _ = resolve_status("Novedad")
    dropi_issue, _ = resolve_status("Incidencia en ruta")
    assert status_group(effi_issue) == status_group(dropi_issue) == "novedad"


def test_every_alias_points_at_a_canonical_code():
    assert set(STATUS_ALIASES.values()) <= set(STATUS_CANON)


# -----------------------------------------------------------------------------
# The merge: terminal is frozen, except lost -> compensated
# -----------------------------------------------------------------------------


def _existing(status_code: str) -> ShipmentRecord:
    return ShipmentRecord(
        tracking_number="G-1",
        connection_id=_CONNECTION,
        tenant_id=_TENANT,
        country_code="EC",
        created_date=date(2026, 8, 1),
        currency_code="USD",
        status_code=status_code,
    )


def _incoming(status_code: str) -> ShipmentInput:
    return ShipmentInput(
        tracking_number="G-1",
        created_date=date(2026, 8, 1),
        currency_code="USD",
        status_code=status_code,
        status_raw=status_code,
    )


def test_the_only_terminal_exception_is_lost_to_compensated():
    assert STATUS_TERMINAL_EXCEPTIONS == {("lost", "compensated")}


def test_a_lost_guide_can_still_be_compensated():
    outcome = merge_shipment(_existing("lost"), _incoming("compensated"))
    assert outcome.status_advanced
    assert outcome.updates["status_code"] == "compensated"


def test_a_compensated_guide_is_frozen_and_other_terminals_stay_frozen():
    assert not merge_shipment(_existing("compensated"), _incoming("lost")).status_advanced
    assert not merge_shipment(_existing("delivered"), _incoming("compensated")).status_advanced
    assert not merge_shipment(_existing("returned"), _incoming("compensated")).status_advanced
    assert not merge_shipment(_existing("cancelled"), _incoming("compensated")).status_advanced
    # And a stale file still cannot undo a delivery.
    assert not merge_shipment(_existing("delivered"), _incoming("in_transit")).status_advanced


# -----------------------------------------------------------------------------
# Against the database, when there is one
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_conn():
    psycopg = pytest.importorskip("psycopg")
    from tests.pg_helpers import admin_test_dsn, recreate_test_database, resolve_test_dsn

    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")
    with psycopg.connect(admin_test_dsn()) as conn:
        yield conn


@pytest.mark.postgres
def test_the_sql_seed_mirrors_the_python_copy(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute("SELECT code, status_group FROM core.status_canon")
        assert dict(cur.fetchall()) == STATUS_GROUPS

        cur.execute(
            "SELECT code, label, sort_order, is_terminal, is_delivered, is_returned, bucket "
            "FROM core.status_canon WHERE code = 'compensated'"
        )
        row = cur.fetchone()
        assert row is not None, "045 no registró el estado 'compensated'"
        canon = STATUS_CANON["compensated"]
        assert row == (
            canon.code, canon.label, canon.sort_order, canon.is_terminal,
            canon.is_delivered, canon.is_returned, canon.bucket,
        )

        cur.execute(
            "SELECT status_code FROM core.status_alias "
            "WHERE platform_code = 'dropi' AND alias_norm = 'incidencia en ruta'"
        )
        row = cur.fetchone()
        assert row is not None, "040 no registró 'incidencia en ruta' para Dropi"
        assert row[0] == "delivery_issue"

        cur.execute(
            "SELECT platform_code, alias_norm FROM core.status_alias WHERE status_code = 'compensated'"
        )
        aliases = cur.fetchall()
        assert {"effi", "dropi"} <= {platform for platform, _ in aliases}
        for _, alias in aliases:
            assert STATUS_ALIASES.get(alias) == "compensated", alias


@pytest.mark.postgres
def test_every_canonical_status_maps_to_exactly_one_group_in_sql(admin_conn):
    """The column is NOT NULL and CHECKed, so "exactly one" is a schema fact;
    this reads it back the way the views will, and pins the five words."""
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE status_group IS NULL), "
            "       count(*) FILTER (WHERE status_group NOT IN "
            "         ('entregada','devolucion','en_transito','novedad','indemnizacion')), "
            "       count(DISTINCT status_group) "
            "FROM core.status_canon"
        )
        without_group, off_list, distinct_groups = cur.fetchone()
        assert without_group == 0
        assert off_list == 0
        assert distinct_groups == 5

        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'core' AND table_name = 'status_canon' "
                    "  AND column_name = 'display_group'")
        assert cur.fetchone() is None, "display_group debía renombrarse a status_group"


@pytest.mark.postgres
def test_status_advance_in_sql_mirrors_the_python_merge(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute("SELECT core.status_advance('lost', 'compensated')")
        assert cur.fetchone()[0] == "compensated"
        cur.execute("SELECT core.status_advance('compensated', 'lost')")
        assert cur.fetchone()[0] == "compensated"
        cur.execute("SELECT core.status_advance('delivered', 'compensated')")
        assert cur.fetchone()[0] == "delivered"
        cur.execute("SELECT core.status_advance('delivered', 'in_transit')")
        assert cur.fetchone()[0] == "delivered"
        cur.execute("SELECT core.status_advance('in_transit', 'delivered')")
        assert cur.fetchone()[0] == "delivered"
