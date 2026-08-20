"""The Effi source profile, against fixtures with the real report structure.

The fixtures carry the exact 87 and 56 column layouts of genuine Effi exports,
with every value invented. The real files hold customers' names, phone numbers,
national id numbers and home addresses; they were used to build this profile and
they are never committed.

Everything asserted here is a rule that a real export broke at least once.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.ingest import IngestEngine, MemoryStore
from pipeline.mapping import STATUS_CANON, resolve_status
from pipeline.models import BatchKind
from pipeline.profiles import (
    EFFI_GUIDES,
    EFFI_MOVEMENTS,
    detect_profile,
    is_yes,
    parse_content,
    resolve_effi_movement_type,
)
from pipeline.readers import read_tabular, sniff_format
from tests.conftest import CONNECTION_ID, COUNTRY, PLATFORM, TENANT_ID

# These fixtures mirror an export taken on 2026-08-14, so "today" has to be
# after the guides were dispatched - otherwise the engine correctly rejects them
# as future-dated.
TODAY = date(2026, 8, 20)

FIXTURES = Path(__file__).parent / "fixtures"
GUIDES = FIXTURES / "effi_guias_real_shape.xlsx"
MOVEMENTS = FIXTURES / "effi_movimientos_real_shape.xls"


@pytest.fixture
def guides_bytes() -> bytes:
    return GUIDES.read_bytes()


@pytest.fixture
def movements_bytes() -> bytes:
    return MOVEMENTS.read_bytes()


def _ingest(engine, payload, name, kind):
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=kind,
        tenant_id=TENANT_ID,
        connection_id=CONNECTION_ID,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency="USD",
    )


# =============================================================================
# Format sniffing
# =============================================================================


def test_effi_movements_file_is_html_despite_the_xls_name(movements_bytes):
    """Effi names its wallet export .xls and writes an HTML table into it.

    Trusting the extension gets you a corrupt-file error on a perfectly good
    report, which is exactly what happened the first time.
    """
    assert sniff_format(movements_bytes, "Reporte de movimientos.xls") == "html"


def test_effi_guides_file_is_a_real_xlsx(guides_bytes):
    assert sniff_format(guides_bytes, "Reporte de Guías.xlsx") == "xlsx"


def test_a_real_binary_xls_is_rejected_with_an_actionable_message():
    from pipeline.readers import UnsupportedFileError, read_tabular

    ole2_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(UnsupportedFileError, match="guárdalo como .xlsx"):
        read_tabular(ole2_header, "viejo.xls")


def test_html_reader_strips_tags_and_entities(movements_bytes):
    headers, rows = read_tabular(movements_bytes, "movimientos.xls")
    assert "ID movimiento" in headers
    assert "Tipo de movimiento" in headers
    assert len(headers) == 56
    assert all("<" not in header for header in headers)
    assert len(rows) == 7


# =============================================================================
# Profile detection
# =============================================================================


def test_guides_report_is_recognised(guides_bytes):
    headers, _ = read_tabular(guides_bytes, "guias.xlsx")
    assert len(headers) == 87
    assert detect_profile(headers) is EFFI_GUIDES


def test_movements_report_is_recognised(movements_bytes):
    headers, _ = read_tabular(movements_bytes, "movimientos.xls")
    assert detect_profile(headers) is EFFI_MOVEMENTS


def test_an_unrelated_spreadsheet_is_not_claimed_by_a_profile():
    assert detect_profile(["Guia", "Fecha", "Estado", "Valor"]) is None


def test_profile_never_maps_customer_pii():
    """Names, national ids and street addresses have no field to land in.

    The phone is mapped only so the engine can hash it; there is no column map
    entry that would store any of these as given.
    """
    forbidden = (
        "destinatario",
        "id destinatario",
        "direccion destinatario",
        "nombre destinatario guia inicial",
        "direccion destinatario guia inicial",
    )
    for profile in (EFFI_GUIDES, EFFI_MOVEMENTS):
        for header in forbidden:
            assert header not in profile.columns, f"{profile.code} maps PII column {header!r}"

    # The one PII column that IS mapped goes to the field that gets hashed.
    assert EFFI_GUIDES.columns["telefonos destinatario"] == "customer_identifier"


# =============================================================================
# Field transforms
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3 * CLOROFILA , DETOX.", (3, "CLOROFILA DETOX", 0)),
        ("1 * ZOOONE.", (1, "ZOOONE", 0)),
        ("2 * DRENAJE LINFATICO.", (2, "DRENAJE LINFATICO", 0)),
        ("1 * A. 2 * B.", (1, "A", 1)),
        ("PRODUCTO SIN CANTIDAD", (1, "PRODUCTO SIN CANTIDAD", 0)),
        ("", (None, None, 0)),
        (None, (None, None, 0)),
    ],
)
def test_content_field_parsing(raw, expected):
    assert parse_content(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Recaudo de venta", "cod_collected"),
        ("Flete crédito con recaudo", "freight_out"),
        ("Flete crédito devolución", "freight_return"),
        ("Compra de mercancía dropshipping a proveedor", "product_cost"),
        ("Retiro de dinero de cuenta", "withdrawal"),
        ("Comisión local por retiro de Wallet", "withdrawal_fee"),
        ("Retención en la fuente a favor", "tax_withholding"),
    ],
)
def test_every_real_movement_type_is_mapped(raw, expected):
    code, recognized = resolve_effi_movement_type(raw)
    assert recognized, f"{raw!r} not recognised"
    assert code == expected


def test_unknown_movement_type_is_flagged_not_guessed():
    code, recognized = resolve_effi_movement_type("Concepto inventado")
    assert code is None and recognized is False


def test_si_no_parsing():
    assert is_yes("Si") is True
    assert is_yes("SI") is True
    assert is_yes("No") is False
    assert is_yes("") is None      # unknown is not False


# =============================================================================
# Status vocabulary
# =============================================================================


def test_the_office_state_exists_and_is_not_terminal():
    """278 of 1,649 guides in a real export were sitting in an agency.

    Filing those as 'created' - which is what happened before this state
    existed - understated the problem rate by 17 points.
    """
    assert "in_office" in STATUS_CANON
    status = STATUS_CANON["in_office"]
    assert status.is_terminal is False
    assert status.is_delivered is False
    assert status.is_returned is False
    assert STATUS_CANON["out_for_delivery"].sort_order < status.sort_order
    assert status.sort_order < STATUS_CANON["delivered"].sort_order


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Entregada a destino", "delivered"),
        ("Disponible para retiro en oficina", "in_office"),
        ("En transito", "in_transit"),
        ("Novedad", "delivery_issue"),
        ("Generada", "created"),
        ("En reparto", "out_for_delivery"),
        ("Devolución a origen", "returning"),
        ("Cancelada por transportadora", "cancelled"),
        ("Reportado Entregado en Agencia", "in_office"),
        ("Reportado Entregado en App", "delivered"),
        ("En Distribucion a Cliente", "out_for_delivery"),
        ("Ingresando en Agencia", "in_transit"),
    ],
)
def test_every_status_seen_in_a_real_export_is_recognised(raw, expected):
    code, recognized = resolve_status(raw)
    assert recognized, f"{raw!r} fell through to the default"
    assert code == expected


# =============================================================================
# End to end through the engine
# =============================================================================


def test_guides_load_with_the_profile(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)

    report = _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    assert report.profile_code == "effi_guias"
    assert report.rows_total == 4
    assert report.rows_inserted == 4
    assert report.rows_failed == 0
    # 87 columns in, a couple of dozen mapped: the rest are reported, not hidden.
    assert len(report.unmapped_columns) > 40


def test_the_carrier_number_becomes_the_tracking_number(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    trackings = {key[1] for key in store.shipments}
    assert trackings == {"TEST-0001", "TEST-0002", "TEST-0003", "TEST-0004"}

    # Effi's own id is kept too - it is what the operator sees in the ERP.
    first = store.shipments[(CONNECTION_ID, "TEST-0001")]
    assert first.external_order_id == "9001"
    assert first.carrier_tracking_number == "TEST-0001"


def test_valor_recaudo_is_the_amount_that_matters(pii_salt, guides_bytes):
    """Effi's `Valor declarado` is the INSURED value and is often a flat 20.00.

    Mapping it onto declared_value made 124 of 1,649 real guides look like they
    over-collected by 80%.
    """
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    first = store.shipments[(CONNECTION_ID, "TEST-0001")]
    assert first.declared_value == Decimal("36.99")     # Valor recaudo
    assert first.declared_value != Decimal("20.00")     # NOT Valor declarado


def test_statuses_and_dates_land_where_they_belong(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    delivered = store.shipments[(CONNECTION_ID, "TEST-0001")]
    assert delivered.status_code == "delivered"
    # `Fecha de estado final` means delivery here...
    assert delivered.delivered_at is not None
    assert delivered.returned_at is None
    assert delivered.settled_at is not None
    assert delivered.settled_with_collection is True

    returned = store.shipments[(CONNECTION_ID, "TEST-0003")]
    assert returned.status_code == "returning"
    # ...and the same column means return here.
    assert returned.returned_at is not None
    assert returned.delivered_at is None

    in_office = store.shipments[(CONNECTION_ID, "TEST-0002")]
    assert in_office.status_code == "in_office"
    assert in_office.settled_with_collection is False


def test_product_and_quantity_come_out_of_the_content_field(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    first = store.shipments[(CONNECTION_ID, "TEST-0001")]
    assert first.product_name == "PRODUCTO ALFA"
    assert first.quantity == 3


def test_a_multi_product_guide_is_reported_not_silently_truncated(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    report = _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    issues = [i for i in report.sanity_issues if i.code == "multi_product_guide"]
    assert len(issues) == 1
    assert issues[0].entity_key == "TEST-0004"


def test_customer_phone_is_hashed_and_the_rest_of_the_pii_never_arrives(
    pii_salt, guides_bytes
):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    stored = repr(store.shipments)
    assert "0900000001" not in stored
    assert "Persona Ficticia" not in stored

    first = store.shipments[(CONNECTION_ID, "TEST-0001")]
    assert first.customer_hash is not None
    assert len(first.customer_hash) == 64


# =============================================================================
# Movements
# =============================================================================


def test_movements_load_and_link_by_the_carrier_number(
    pii_salt, guides_bytes, movements_bytes
):
    """The wallet report only ever cites the CARRIER's number.

    Before the engine learned that, every movement from a real export stayed an
    orphan and the whole P&L fell back to estimates.
    """
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)

    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)
    report = _ingest(engine, movements_bytes, "movimientos.xls", BatchKind.MOVEMENTS)

    assert report.profile_code == "effi_movimientos"
    assert report.rows_inserted == 7
    assert report.rows_failed == 0

    linked = [m for m in store.movements.values() if m.shipment_id is not None]
    orphans = [m for m in store.movements.values() if m.shipment_id is None]

    # TEST-9999 has no guide; the withdrawal and its fee have no guide by nature.
    assert len(linked) == 4
    assert len(orphans) == 3


def test_movement_types_from_the_real_wallet(pii_salt, guides_bytes, movements_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)
    _ingest(engine, movements_bytes, "movimientos.xls", BatchKind.MOVEMENTS)

    kinds = {m.movement_type_code for m in store.movements.values()}
    assert kinds == {
        "cod_collected",
        "freight_out",
        "product_cost",
        "freight_return",
        "withdrawal",
        "withdrawal_fee",
    }


def test_a_withdrawal_is_never_treated_as_an_operating_cost(
    pii_salt, guides_bytes, movements_bytes
):
    """Moving your own money to your own bank is not a cost.

    Counting it as one would charge the P&L twice: once when the sale was
    collected, again when it was withdrawn.
    """
    from pipeline.profiles import TRANSFER_TYPES

    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)
    _ingest(engine, movements_bytes, "movimientos.xls", BatchKind.MOVEMENTS)

    withdrawals = [
        m for m in store.movements.values() if m.movement_type_code in TRANSFER_TYPES
    ]
    assert len(withdrawals) == 1
    assert withdrawals[0].amount == Decimal("1500.00")
    # It has no guide, so it can never be folded into a shipment's economics.
    assert withdrawals[0].shipment_id is None


def test_negative_amounts_are_stored_as_magnitudes(
    pii_salt, guides_bytes, movements_bytes
):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)
    _ingest(engine, movements_bytes, "movimientos.xls", BatchKind.MOVEMENTS)

    assert all(m.amount > 0 for m in store.movements.values())


def test_loading_movements_before_guides_still_links(
    pii_salt, guides_bytes, movements_bytes
):
    """The realistic order: the wallet is exported daily, guides weekly."""
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)

    _ingest(engine, movements_bytes, "movimientos.xls", BatchKind.MOVEMENTS)
    assert all(m.shipment_id is None for m in store.movements.values())

    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)

    linked = [m for m in store.movements.values() if m.shipment_id is not None]
    assert len(linked) == 4


def test_reloading_the_same_export_changes_nothing(pii_salt, guides_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)

    _ingest(engine, guides_bytes, "guias.xlsx", BatchKind.SHIPMENTS)
    second = _ingest(engine, guides_bytes, "guias_copia.xlsx", BatchKind.SHIPMENTS)

    assert second.already_loaded is True
    assert len(store.shipments) == 4
