"""End-to-end tests for the ingestion engine against MemoryStore.

These encode the rules the business actually depends on:
  * loading the same file twice changes nothing the second time
  * a stale file can never un-deliver a shipment
  * money that contradicts itself is recorded, not swallowed
  * a customer's phone number never reaches storage

Phase 1 adds the same suite against a real PostgresStore, so any drift between
the Python merge policy and the SQL ON CONFLICT clause shows up as a failure.
"""

from __future__ import annotations

import hashlib
import io
from decimal import Decimal

import pytest

from pipeline.ingest import IngestEngine, MemoryStore, merge_shipment
from pipeline.mapping import STATUS_CANON, build_header_map, resolve_status
from pipeline.models import BatchKind, ShipmentInput
from pipeline.normalize import normalize_text, parse_date, parse_decimal
from tests.conftest import (
    CONNECTION_ID,
    CONNECTION_ID_ALT,
    COUNTRY,
    CURRENCY,
    PLATFORM,
    TENANT_ID,
    TEST_PII_SALT,
    TODAY,
)


def _ingest(engine, payload, name, kind=BatchKind.SHIPMENTS, connection_id=CONNECTION_ID):
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=kind,
        tenant_id=TENANT_ID,
        connection_id=connection_id,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency=CURRENCY,
    )


# =============================================================================
# Parsing primitives
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("89.900", Decimal("89900")),          # Colombian thousands
        ("1.234.567", Decimal("1234567")),
        ("1.234,56", Decimal("1234.56")),      # Colombian decimal
        ("1,234.56", Decimal("1234.56")),      # Mexican decimal
        ("$ 149.900", Decimal("149900")),
        ("(1.200)", Decimal("-1200")),         # accounting negative
        ("0", Decimal("0")),
        ("", None),
        (None, None),
        ("  ", None),
        (149900, Decimal("149900")),
    ],
)
def test_parse_decimal_handles_latam_conventions(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("01/07/2026", (2026, 7, 1)), ("2026-07-01", (2026, 7, 1)), ("01-07-2026", (2026, 7, 1))],
)
def test_parse_date_prefers_day_first(raw, expected):
    parsed = parse_date(raw)
    assert (parsed.year, parsed.month, parsed.day) == expected


def test_normalize_text_matches_sql_semantics():
    assert normalize_text("Bogotá") == "bogota"
    assert normalize_text("BOGOTA") == "bogota"
    assert normalize_text("  Bogotá  ") == "bogota"
    assert normalize_text("Valle  del   Cauca") == "valle del cauca"
    assert normalize_text("") is None


def test_resolve_status_flags_unknown_values():
    assert resolve_status("ENTREGADO") == ("delivered", True)
    assert resolve_status("entregada") == ("delivered", True)
    assert resolve_status("ENTREGADO - OK") == ("delivered", True)
    code, recognized = resolve_status("ALGO RARO")
    assert code == "created" and recognized is False


def test_header_map_reports_unmapped_columns():
    headers = ["Guia", "Fecha", "Estado", "Columna Inventada"]
    mapped, unmapped = build_header_map(headers, BatchKind.SHIPMENTS)
    assert set(mapped.values()) == {"tracking_number", "created_date", "status_raw"}
    assert unmapped == ["Columna Inventada"]


# =============================================================================
# First load
# =============================================================================


def test_first_load_inserts_every_row(engine, memory_store, guias_dia1):
    report = _ingest(engine, guias_dia1, "effi_guias_dia1.csv")

    assert report.already_loaded is False
    assert report.rows_total == 10
    assert report.rows_inserted == 10
    assert report.rows_updated == 0
    assert report.rows_failed == 0
    assert report.errors == []
    assert len(memory_store.shipments) == 10


def test_first_load_maps_statuses_correctly(engine, memory_store, guias_dia1):
    _ingest(engine, guias_dia1, "effi_guias_dia1.csv")
    by_tracking = {k[1]: v for k, v in memory_store.shipments.items()}

    assert by_tracking["E-1001"].status_code == "delivered"
    assert by_tracking["E-1002"].status_code == "returned"
    assert by_tracking["E-1004"].status_code == "in_transit"
    assert by_tracking["E-1006"].status_code == "delivery_issue"
    assert by_tracking["E-1008"].status_code == "cancelled"


def test_first_load_parses_colombian_money(engine, memory_store, guias_dia1):
    _ingest(engine, guias_dia1, "effi_guias_dia1.csv")
    shipment = memory_store.shipments[(CONNECTION_ID, "E-1001")]

    assert shipment.declared_value == Decimal("89900")
    assert shipment.freight_cost == Decimal("12000")
    assert shipment.product_cost == Decimal("35000")
    assert shipment.currency_code == "COP"


def test_city_variants_normalize_to_one_key(engine, memory_store, guias_dia1):
    """Bogotá, BOGOTA and Bogota must collapse into a single geo key.

    The fixture spells the same city four different ways on purpose (E-1001,
    E-1005, E-1007, E-1010). If normalization ever regresses, the geo dashboard
    silently splits one city into several and every rate it shows is wrong.
    """
    _ingest(engine, guias_dia1, "effi_guias_dia1.csv")
    bogota_rows = [
        s for s in memory_store.shipments.values() if normalize_text(s.city_name) == "bogota"
    ]
    assert len(bogota_rows) == 4
    assert {s.city_name for s in bogota_rows} == {"Bogotá", "BOGOTA", "Bogota"}
    assert {normalize_text(s.city_name) for s in bogota_rows} == {"bogota"}


# =============================================================================
# PII
# =============================================================================


def test_customer_phone_is_never_stored_in_the_clear(engine, memory_store, guias_dia1):
    _ingest(engine, guias_dia1, "effi_guias_dia1.csv")
    shipment = memory_store.shipments[(CONNECTION_ID, "E-1001")]

    assert shipment.customer_hash is not None
    assert len(shipment.customer_hash) == 64
    assert "3001234567" not in shipment.customer_hash

    expected = hashlib.sha256(f"{TEST_PII_SALT}:3001234567".encode()).hexdigest()
    assert shipment.customer_hash == expected

    # And nothing anywhere in the stored record leaks the raw number.
    assert "3001234567" not in repr(memory_store.shipments)


def test_same_customer_across_formats_hashes_identically():
    from pipeline.normalize import hash_customer

    a = hash_customer("+57 300 123 4567", TEST_PII_SALT)
    b = hash_customer("300-123-4567", TEST_PII_SALT)
    assert a is not None and a != b       # country code changes identity, by design

    c = hash_customer("3001234567", TEST_PII_SALT)
    assert b == c


def test_engine_refuses_to_run_without_a_salt(memory_store):
    with pytest.raises(ValueError, match="pii_salt"):
        IngestEngine(memory_store, pii_salt="", today=TODAY)


# =============================================================================
# Idempotence
# =============================================================================


def test_second_load_of_same_file_is_a_no_op(engine, memory_store, guias_dia1):
    first = _ingest(engine, guias_dia1, "effi_guias_dia1.csv")
    second = _ingest(engine, guias_dia1, "effi_guias_dia1.csv")

    assert first.rows_inserted == 10
    assert second.already_loaded is True
    assert second.rows_inserted == 0
    assert second.rows_updated == 0
    assert len(memory_store.shipments) == 10


def test_renaming_the_file_does_not_defeat_idempotence(engine, memory_store, guias_dia1):
    _ingest(engine, guias_dia1, "reporte_julio.csv")
    second = _ingest(engine, guias_dia1, "reporte_julio_COPIA.csv")

    assert second.already_loaded is True
    assert len(memory_store.shipments) == 10


def test_same_file_on_a_different_connection_loads_separately(engine, memory_store, guias_dia1):
    _ingest(engine, guias_dia1, "guias.csv", connection_id=CONNECTION_ID)
    second = _ingest(engine, guias_dia1, "guias.csv", connection_id=CONNECTION_ID_ALT)

    assert second.already_loaded is False
    assert second.rows_inserted == 10
    assert len(memory_store.shipments) == 20      # two independent sources


# =============================================================================
# Merge policy
# =============================================================================


def test_status_advances_but_never_regresses(engine, memory_store, guias_dia1, guias_dia2):
    _ingest(engine, guias_dia1, "dia1.csv")
    report = _ingest(engine, guias_dia2, "dia2.csv")

    by_tracking = {k[1]: v for k, v in memory_store.shipments.items()}

    # E-1002 was already 'returned' (terminal). Day 2 says 'en transito'. Frozen.
    assert by_tracking["E-1002"].status_code == "returned"
    # E-1004 was in transit, day 2 delivers it. Advances.
    assert by_tracking["E-1004"].status_code == "delivered"
    assert by_tracking["E-1004"].delivered_at is not None
    # Two brand new guides.
    assert by_tracking["E-1011"].status_code == "delivered"
    assert by_tracking["E-1012"].status_code == "in_transit"
    assert report.rows_inserted == 2


def test_money_change_wins_and_leaves_a_discrepancy(engine, memory_store, guias_dia1, guias_dia2):
    _ingest(engine, guias_dia1, "dia1.csv")
    report = _ingest(engine, guias_dia2, "dia2.csv")

    shipment = memory_store.shipments[(CONNECTION_ID, "E-1003")]
    assert shipment.declared_value == Decimal("159900")      # newest wins

    discrepancies = [d for d in report.discrepancies if d.entity_key == "E-1003"]
    assert len(discrepancies) == 1
    assert discrepancies[0].field_name == "declared_value"
    assert discrepancies[0].old_value == "149900"
    assert discrepancies[0].new_value == "159900"


def test_static_fields_fill_gaps_but_are_never_overwritten():
    from pipeline.ingest import ShipmentRecord

    existing = ShipmentRecord(
        tracking_number="E-1",
        connection_id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        country_code=COUNTRY,
        created_date=parse_date("01/07/2026"),
        currency_code="COP",
        status_code="in_transit",
        city_name="Cali",
        product_name=None,
    )
    incoming = ShipmentInput(
        tracking_number="E-1",
        created_date=parse_date("01/07/2026"),
        currency_code="COP",
        status_code="in_transit",
        city_name="Medellin",        # contradicts what we know: ignored
        product_name="Reloj",        # unknown until now: taken
    )

    outcome = merge_shipment(existing, incoming)
    assert "city_name" not in outcome.updates
    assert outcome.updates["product_name"] == "Reloj"


def test_identical_reload_of_a_row_reports_skipped(engine, memory_store, guias_dia1):
    """A file with new bytes but unchanged rows must update nothing."""
    _ingest(engine, guias_dia1, "dia1.csv")
    tweaked = guias_dia1 + b"\n"      # different bytes, identical data
    report = _ingest(engine, tweaked, "dia1_reexport.csv")

    assert report.already_loaded is False
    assert report.rows_inserted == 0
    assert report.rows_updated == 0
    assert report.rows_skipped == 10


def test_terminal_status_is_frozen_even_against_a_higher_sort_order():
    from pipeline.ingest import ShipmentRecord

    delivered = ShipmentRecord(
        tracking_number="E-1",
        connection_id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        country_code=COUNTRY,
        created_date=parse_date("01/07/2026"),
        currency_code="COP",
        status_code="delivered",
    )
    later = ShipmentInput(
        tracking_number="E-1",
        created_date=parse_date("01/07/2026"),
        currency_code="COP",
        status_code="returned",       # sort_order 80 > 60, but delivered is terminal
    )

    outcome = merge_shipment(delivered, later)
    assert "status_code" not in outcome.updates
    assert outcome.status_advanced is False


def test_status_ladder_is_internally_consistent():
    orders = [s.sort_order for s in STATUS_CANON.values()]
    assert len(orders) == len(set(orders)), "sort_order must be unique"
    assert STATUS_CANON["delivered"].is_terminal
    assert STATUS_CANON["returned"].is_terminal
    assert not STATUS_CANON["in_transit"].is_terminal
    assert STATUS_CANON["delivered"].sort_order > STATUS_CANON["out_for_delivery"].sort_order


# =============================================================================
# Movements and orphans
# =============================================================================


def test_movements_link_to_existing_shipments(engine, memory_store, guias_dia1, movimientos):
    _ingest(engine, guias_dia1, "dia1.csv")
    report = _ingest(engine, movimientos, "movimientos.csv", kind=BatchKind.MOVEMENTS)

    assert report.rows_total == 10
    assert report.rows_inserted == 10
    assert report.rows_failed == 0

    linked = [m for m in memory_store.movements.values() if m.shipment_id is not None]
    orphans = [m for m in memory_store.movements.values() if m.shipment_id is None]

    assert len(linked) == 8
    # E-1011 has not been loaded yet: its two movements wait.
    assert len(orphans) == 2
    assert {m.tracking_number_raw for m in orphans} == {"E-1011"}


def test_orphan_movements_relink_when_their_shipment_arrives(
    engine, memory_store, guias_dia1, guias_dia2, movimientos
):
    _ingest(engine, guias_dia1, "dia1.csv")
    _ingest(engine, movimientos, "movimientos.csv", kind=BatchKind.MOVEMENTS)

    orphans_before = [m for m in memory_store.movements.values() if m.shipment_id is None]
    assert len(orphans_before) == 2

    _ingest(engine, guias_dia2, "dia2.csv")      # brings E-1011

    orphans_after = [m for m in memory_store.movements.values() if m.shipment_id is None]
    assert orphans_after == []


def test_relink_orphans_is_idempotent(engine, memory_store, guias_dia1, movimientos):
    _ingest(engine, guias_dia1, "dia1.csv")
    _ingest(engine, movimientos, "movimientos.csv", kind=BatchKind.MOVEMENTS)

    assert memory_store.relink_orphans() == 0     # nothing new to link
    assert memory_store.relink_orphans() == 0


def test_movement_amounts_are_stored_as_magnitudes(engine, memory_store, guias_dia1, movimientos):
    _ingest(engine, guias_dia1, "dia1.csv")
    _ingest(engine, movimientos, "movimientos.csv", kind=BatchKind.MOVEMENTS)

    freights = [
        m for m in memory_store.movements.values() if m.movement_type_code == "freight_out"
    ]
    assert freights, "expected freight movements in the fixture"
    assert all(m.amount > 0 for m in freights)


# =============================================================================
# Sanity checks and error reporting
# =============================================================================


def test_row_without_tracking_number_fails_without_killing_the_load(engine, memory_store):
    payload = (
        b"Guia;Fecha;Estado;Valor\n"
        b"E-2001;01/07/2026;ENTREGADO;50.000\n"
        b";01/07/2026;ENTREGADO;60.000\n"
        b"E-2003;01/07/2026;ENTREGADO;70.000\n"
    )
    report = _ingest(engine, payload, "parcial.csv")

    assert report.rows_total == 3
    assert report.rows_inserted == 2
    assert report.rows_failed == 1
    assert "sin número de guía" in report.errors[0].message


def test_future_created_date_is_rejected(engine):
    payload = (
        b"Guia;Fecha;Estado;Valor\n"
        b"E-3001;01/12/2027;ENTREGADO;50.000\n"
    )
    report = _ingest(engine, payload, "futuro.csv")

    assert report.rows_failed == 1
    assert report.rows_inserted == 0
    assert any(i.code == "future_created_date" for i in report.sanity_issues)


def test_unknown_status_is_reported_not_hidden(engine):
    payload = (
        b"Guia;Fecha;Estado;Valor\n"
        b"E-4001;01/07/2026;TELEPORTADO;50.000\n"
    )
    report = _ingest(engine, payload, "raro.csv")

    assert report.rows_inserted == 1
    codes = [i.code for i in report.sanity_issues]
    assert "unknown_status" in codes


def test_missing_required_column_rejects_the_file(engine):
    payload = b"Fecha;Estado;Valor\n01/07/2026;ENTREGADO;50.000\n"
    report = _ingest(engine, payload, "sin_guia.csv")

    assert report.rows_inserted == 0
    assert report.rows_failed == 1
    assert "tracking_number" in report.errors[0].message


def test_unmapped_columns_are_surfaced(engine, guias_dia1):
    payload = guias_dia1.replace(b"Telefono", b"Dato Misterioso")
    report = _ingest(engine, payload, "dia1.csv")

    assert "Dato Misterioso" in report.unmapped_columns


def test_report_serializes_to_json(engine, guias_dia1, guias_dia2):
    _ingest(engine, guias_dia1, "dia1.csv")
    report = _ingest(engine, guias_dia2, "dia2.csv")

    payload = report.to_json()
    assert payload["rows"]["inserted"] == 2
    assert isinstance(payload["discrepancies"], list)
    assert payload["discrepancies"][0]["field"] == "declared_value"


# =============================================================================
# Excel parity
# =============================================================================


def test_xlsx_and_csv_produce_identical_results(guias_dia1, pii_salt):
    """The same data as .xlsx must land exactly like the .csv did."""
    openpyxl = pytest.importorskip("openpyxl")

    rows = [line.split(";") for line in guias_dia1.decode("utf-8").strip().splitlines()]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)

    csv_store, xlsx_store = MemoryStore(), MemoryStore()
    csv_engine = IngestEngine(csv_store, pii_salt=pii_salt, today=TODAY)
    xlsx_engine = IngestEngine(xlsx_store, pii_salt=pii_salt, today=TODAY)

    csv_report = _ingest(csv_engine, guias_dia1, "guias.csv")
    xlsx_report = _ingest(xlsx_engine, buffer.getvalue(), "guias.xlsx")

    assert csv_report.rows_inserted == xlsx_report.rows_inserted == 10
    assert xlsx_report.rows_failed == 0

    csv_rows = {k[1]: v for k, v in csv_store.shipments.items()}
    xlsx_rows = {k[1]: v for k, v in xlsx_store.shipments.items()}
    assert csv_rows.keys() == xlsx_rows.keys()
    for tracking, csv_row in csv_rows.items():
        other = xlsx_rows[tracking]
        assert csv_row.status_code == other.status_code
        assert csv_row.declared_value == other.declared_value
        assert csv_row.created_date == other.created_date


# =============================================================================
# Hand-checkable totals - the fixture is small enough to add up on paper
# =============================================================================


def test_fixture_totals_match_manual_arithmetic(engine, memory_store, guias_dia1):
    """Day-1 fixture, counted by hand:

        delivered : E-1001, E-1003, E-1005, E-1007, E-1010  -> 5
        returned  : E-1002, E-1009                          -> 2
        cancelled : E-1008                                  -> 1
        open      : E-1004 (in transit), E-1006 (novedad)   -> 2
        declared value of delivered guides:
            89.900 + 149.900 + 179.800 + 149.900 + 89.900   = 659.400
    """
    _ingest(engine, guias_dia1, "dia1.csv")
    shipments = list(memory_store.shipments.values())

    delivered = [s for s in shipments if STATUS_CANON[s.status_code].is_delivered]
    returned = [s for s in shipments if STATUS_CANON[s.status_code].is_returned]
    open_ones = [s for s in shipments if not STATUS_CANON[s.status_code].is_terminal]

    assert len(delivered) == 5
    assert len(returned) == 2
    assert len(open_ones) == 2
    assert sum(s.declared_value for s in delivered) == Decimal("659400")


def test_reprocessing_a_movements_file_replaces_instead_of_duplicating(
    engine, memory_store, guias_dia1, movimientos
):
    """`reprocess=True` on the in-memory store must mean "replace", as in Postgres.

    Regression: `MemoryStore.clear_batch_rows` iterated the dict's KEYS, so it
    never removed a row and turned the dict into a list on the way out.
    """
    _ingest(engine, guias_dia1, "dia1.csv")
    first = _ingest(engine, movimientos, "movimientos.csv", kind=BatchKind.MOVEMENTS)
    assert first.rows_inserted == 10
    assert len(memory_store.movements) == 10

    again = engine.ingest(
        payload=movimientos,
        source_name="movimientos.csv",
        kind=BatchKind.MOVEMENTS,
        tenant_id=TENANT_ID,
        connection_id=CONNECTION_ID,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency=CURRENCY,
        reprocess=True,
    )
    assert again.already_loaded is False
    assert again.batch_id == first.batch_id
    assert isinstance(memory_store.movements, dict)
    assert len(memory_store.movements) == 10
