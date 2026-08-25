"""The Dropi source profiles, against fixtures with the real export structure.

The fixtures carry the exact 63-column orders layout and the 9-column wallet
layout of genuine Dropi exports (Guatemala, 2026-08-24), with every value
invented. The real files hold customers' names, phones and addresses; they were
used to build these profiles and they are never committed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.ingest import IngestEngine, MemoryStore
from pipeline.mapping import MOVEMENT_TYPE_SIGNS, STATUS_ALIASES, STATUS_CANON, resolve_status
from pipeline.models import BatchKind
from pipeline.normalize import normalize_text
from pipeline.profiles import (
    DROPI_MOVEMENT_TYPES,
    DROPI_ORDERS,
    DROPI_WALLET,
    detect_profile,
    resolve_dropi_movement_type,
)
from pipeline.readers import read_tabular, sniff_format
from tests.conftest import CONNECTION_ID, TENANT_ID

# The real exports are Guatemalan. The upload declares the country (042); the
# file itself never says it.
COUNTRY = "GT"
PLATFORM = "dropi"
TODAY = date(2026, 8, 25)

FIXTURES = Path(__file__).parent / "fixtures"
ORDERS = FIXTURES / "dropi_ordenes_real_shape.xlsx"
WALLET = FIXTURES / "dropi_cartera_real_shape.xlsx"

# Every distinct ESTATUS the real file carried, with what it must become.
REAL_STATUSES = {
    "ENTREGADO": "delivered",
    "DEVOLUCION": "returning",
    "INCIDENCIA EN RUTA": "delivery_issue",
    "EN RUTA": "in_transit",
    "RECOLECTADO": "picked_up",
    "GUIA_GENERADA": "created",
    "CANCELADO": "cancelled",
    "PREPARADO PARA TRANSPORTADORA": "confirmed",
}


@pytest.fixture
def orders_bytes() -> bytes:
    return ORDERS.read_bytes()


@pytest.fixture
def wallet_bytes() -> bytes:
    return WALLET.read_bytes()


def _ingest(engine, payload, name, kind):
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=kind,
        tenant_id=TENANT_ID,
        connection_id=CONNECTION_ID,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency="GTQ",
    )


# =============================================================================
# Format and profile detection
# =============================================================================


def test_both_dropi_files_are_real_xlsx(orders_bytes, wallet_bytes):
    assert sniff_format(orders_bytes, "ordenes_20260824_152057.xlsx") == "xlsx"
    assert sniff_format(wallet_bytes, "historial de cartera-24-08-2026 15_21.xlsx") == "xlsx"


def test_the_orders_export_is_recognised(orders_bytes):
    headers, rows = read_tabular(orders_bytes, "ordenes.xlsx")
    assert len(headers) == 63
    assert len(rows) == 10
    assert detect_profile(headers) is DROPI_ORDERS
    assert detect_profile(headers, BatchKind.SHIPMENTS) is DROPI_ORDERS
    assert detect_profile(headers, BatchKind.MOVEMENTS) is None


def test_the_wallet_export_is_recognised(wallet_bytes):
    headers, rows = read_tabular(wallet_bytes, "cartera.xlsx")
    assert len(headers) == 9
    assert len(rows) == 6
    assert detect_profile(headers) is DROPI_WALLET


def test_dropi_never_claims_an_effi_file():
    effi_headers = ["Guía transportadora", "Estado global guía inicial",
                    "Nombre transportadora Efficommerce", "Valor recaudo"]
    assert detect_profile(effi_headers) is not DROPI_ORDERS


def test_every_mapped_contact_column_is_declared_as_pii():
    contact_columns = {
        "NOMBRE CLIENTE": "customer_name",
        "TELÉFONO": "customer_identifier",
        "NRO DE IDENTIFICACION": "customer_document",
        "DIRECCION": "customer_address",
    }
    for header, expected_field in contact_columns.items():
        key = normalize_text(header)
        assert DROPI_ORDERS.columns.get(key) == expected_field, header
        assert key in DROPI_ORDERS.pii_columns_norm, header
    # The wallet carries no contact data at all.
    assert DROPI_WALLET.pii_columns == ()


# =============================================================================
# Vocabulary
# =============================================================================


@pytest.mark.parametrize(("raw", "expected"), sorted(REAL_STATUSES.items()))
def test_every_status_seen_in_a_real_export_is_recognised(raw, expected):
    code, recognized = resolve_status(raw)
    assert recognized, f"{raw!r} fell through to the default"
    assert code == expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("ENTRADA POR GANANCIA EN LA ORDEN COMO DROPSHIPPER: 123 GUIA: FD1", "settlement_in"),
        ("SALIDA DE COBRO DE DEVOLUCIÓN POR ENTREGA NO EFECTIVA: 123", "settlement_out"),
        ("SALIDA POR PETICION DE RETIRO DE SALDO EN CARTERA", "withdrawal"),
        ("DEVOLUCION DE DINERO POR GARANTIA ID: 123", "adjustment_in"),
        ("SALIDA POR NUEVA ORDEN: 123", "settlement_out"),
    ],
)
def test_every_real_wallet_phrase_is_mapped(description, expected):
    code, recognized = resolve_dropi_movement_type(description)
    assert recognized
    assert code == expected


def test_an_unknown_wallet_phrase_is_flagged_not_guessed():
    assert resolve_dropi_movement_type("CONCEPTO INVENTADO") == (None, False)


def test_every_dropi_movement_type_has_a_sign():
    for code in DROPI_MOVEMENT_TYPES.values():
        assert code in MOVEMENT_TYPE_SIGNS, code
    assert MOVEMENT_TYPE_SIGNS["settlement_in"] == 1
    assert MOVEMENT_TYPE_SIGNS["settlement_out"] == -1


# =============================================================================
# End to end through the engine
# =============================================================================


def test_orders_load_with_the_profile_and_zero_unknown_statuses(pii_salt, orders_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    report = _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)

    assert report.profile_code == "dropi_ordenes"
    assert report.rows_total == 10
    assert report.rows_inserted == 10
    assert report.rows_failed == 0
    assert [issue for issue in report.sanity_issues if issue.code == "unknown_status"] == []
    # 63 columns in; the invoicing block and the free-text ones are reported, not hidden.
    assert len(report.unmapped_columns) >= 20


def test_the_guide_number_is_the_key_and_a_cancelled_order_falls_back_to_its_id(
    pii_salt, orders_bytes
):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)

    trackings = {key[1] for key in store.shipments}
    assert "FD10000001" in trackings
    assert "DROPI-9000008" in trackings          # cancelled before a guide existed
    first = store.shipments[(CONNECTION_ID, "FD10000001")]
    assert first.external_order_id == "9000001"
    assert first.carrier_tracking_number == "FD10000001"
    assert first.country_code == COUNTRY
    assert first.currency_code == "GTQ"


def test_statuses_land_on_the_ladder(pii_salt, orders_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)

    by_guide = {key[1]: record for key, record in store.shipments.items()}
    assert by_guide["FD10000001"].status_code == "delivered"
    assert by_guide["FD10000003"].status_code == "returning"
    assert by_guide["FD10000004"].status_code == "delivery_issue"
    assert by_guide["FD10000005"].status_code == "in_transit"
    assert by_guide["FD10000006"].status_code == "picked_up"
    assert by_guide["FD10000007"].status_code == "created"
    assert by_guide["DROPI-9000008"].status_code == "cancelled"
    assert by_guide["FD10000009"].status_code == "confirmed"
    # The indemnity counter wins over ESTATUS: the carrier paid this one back.
    assert by_guide["FD10000010"].status_code == "compensated"
    assert STATUS_CANON["compensated"].is_terminal


def test_the_money_columns_land_where_the_engine_computes_from(pii_salt, orders_bytes):
    """GANANCIA is derived (valor - proveedor - flete - comisión - devolución);
    the engine computes it, so the four inputs are what gets stored."""
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)

    delivered = store.shipments[(CONNECTION_ID, "FD10000001")]
    assert delivered.declared_value == Decimal("180")
    assert delivered.freight_cost == Decimal("31.82")
    assert delivered.product_cost == Decimal("85")
    assert delivered.platform_fee == Decimal("0")
    assert delivered.return_freight_cost == Decimal("0")
    assert delivered.declared_value - delivered.product_cost - delivered.freight_cost == Decimal("63.18")

    returned = store.shipments[(CONNECTION_ID, "FD10000003")]
    assert returned.return_freight_cost == Decimal("31.86")
    assert returned.created_date == date(2026, 8, 22)
    # No delivery date in the export: an honest None, never a guess.
    assert delivered.delivered_at is None


def test_the_department_and_carrier_are_kept(pii_salt, orders_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)
    record = store.shipments[(CONNECTION_ID, "FD10000002")]
    assert record.carrier_name == "FORZA"
    assert record.geo_level1 == "SAN MARCOS"
    assert record.city_name == "MALACATAN"
    assert record.store_name == "1331725"


def test_loading_the_same_file_twice_does_not_duplicate_anything(pii_salt, orders_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    first = _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)
    second = _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)

    assert first.rows_inserted == 10
    assert second.rows_inserted == 0
    assert second.rows_failed == 0
    assert len(store.shipments) == 10


def test_the_wallet_loads_and_links_to_the_guides(pii_salt, orders_bytes, wallet_bytes):
    store = MemoryStore()
    engine = IngestEngine(store, pii_salt=pii_salt, today=TODAY)
    _ingest(engine, orders_bytes, "ordenes.xlsx", BatchKind.SHIPMENTS)
    report = _ingest(engine, wallet_bytes, "cartera.xlsx", BatchKind.MOVEMENTS)

    assert report.profile_code == "dropi_cartera"
    assert report.rows_total == 6
    assert report.rows_inserted == 6
    assert report.rows_failed == 0

    by_ref = {record.external_ref: record for record in store.movements.values()}
    assert by_ref["4000001"].movement_type_code == "settlement_in"
    assert by_ref["4000001"].amount == Decimal("63.18")
    assert by_ref["4000001"].movement_date == date(2026, 8, 23)
    assert by_ref["4000003"].movement_type_code == "settlement_out"
    assert by_ref["4000004"].movement_type_code == "withdrawal"
    assert by_ref["4000005"].movement_type_code == "adjustment_in"
    assert by_ref["4000006"].movement_type_code == "settlement_out"

    # The settlement cites the carrier's guide number, which is the shipment key.
    linked = [m for m in store.movements.values() if m.tracking_number_raw == "FD10000001"]
    assert len(linked) == 1

    # Twice is still six.
    again = _ingest(engine, wallet_bytes, "cartera.xlsx", BatchKind.MOVEMENTS)
    assert again.rows_inserted == 0
    assert len(store.movements) == 6


# =============================================================================
# Against the database: SQL and Python agree on Dropi's words
# =============================================================================


@pytest.mark.postgres
def test_the_sql_aliases_and_movement_types_mirror_the_python_copy():
    psycopg = pytest.importorskip("psycopg")
    from tests.pg_helpers import admin_test_dsn, recreate_test_database, resolve_test_dsn

    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    with psycopg.connect(admin_test_dsn()) as conn, conn.cursor() as cur:
        # SQL -> Python: every Dropi alias the database knows resolves the same way.
        cur.execute("SELECT alias_norm, status_code FROM core.status_alias WHERE platform_code = 'dropi'")
        sql_aliases = dict(cur.fetchall())
        for alias, code in sql_aliases.items():
            assert STATUS_ALIASES.get(alias) == code, f"SQL dice {alias!r} -> {code}, Python no"
        # Python -> SQL: every real ESTATUS is registered under dropi in SQL too.
        for raw, expected in REAL_STATUSES.items():
            key = normalize_text(raw)
            assert sql_aliases.get(key) == expected, f"{raw!r} no está bajo 'dropi' en SQL"

        cur.execute("SELECT code, sign, category FROM core.movement_type "
                    "WHERE code IN ('settlement_in', 'settlement_out')")
        rows = {code: (sign, category) for code, sign, category in cur.fetchall()}
        assert rows == {"settlement_in": (1, "transfer"), "settlement_out": (-1, "transfer")}
        for code, (sign, _) in rows.items():
            assert MOVEMENT_TYPE_SIGNS[code] == sign
