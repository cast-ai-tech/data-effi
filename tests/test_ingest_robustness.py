"""Regresiones de la auditoría del motor de ingesta (rama audit/pipeline-worker).

Cada prueba fija un error real que hacía que un número del tablero saliera mal
sin que nadie se enterara.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime

import psycopg
import pytest

from pipeline.ingest import _tally
from pipeline.models import (
    BatchContext,
    BatchKind,
    IngestReport,
    RowOutcome,
    ShipmentInput,
    UpsertResult,
)
from pipeline.mapping import resolve_status
from pipeline.normalize import normalize_tracking
from pipeline.store_pg import PostgresStore
from tests.conftest import CONNECTION_ID, COUNTRY, PLATFORM, TENANT_ID


# =============================================================================
# Números de guía
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # El sufijo ".0" de Excel se va; los ceros de la guía se quedan.
        ("240012345670.0", "240012345670"),
        ("1000.00", "1000"),
        ("100", "100"),
        # Celda numérica leída como float, incluida la que str() pondría en
        # notación científica.
        (240012345670.0, "240012345670"),
        (1e15, "1000000000000000"),
        (12345, "12345"),
        (" 'abc-10' ", "ABC-10"),
        (None, None),
        ("", None),
    ],
)
def test_tracking_keeps_its_trailing_zeros(raw, expected):
    assert normalize_tracking(raw) == expected


# =============================================================================
# Estados decorados
# =============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Con "entregado" antes en el diccionario, estas dos se contaban como
        # entregadas y devolución en curso.
        ("Entregado en agencia (Pasto)", "in_office"),
        ("Devolución entregada - bodega", "returned"),
        ("Entregado - OK", "delivered"),
        ("Novedad (cliente ausente)", "delivery_issue"),
    ],
)
def test_decorated_status_takes_the_most_specific_alias(raw, expected):
    assert resolve_status(raw) == (expected, True)


# =============================================================================
# Una fila que la base rechaza no tumba el archivo
# =============================================================================


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, *_args, **_kwargs):
        raise psycopg.errors.NumericValueOutOfRange("numeric field overflow")


class _FailingBatchConn:
    """Conexión mínima: el executemany de la tanda siempre falla."""

    def transaction(self):
        return nullcontext()

    def cursor(self, **_kwargs):
        return _Cursor()


def _ctx() -> BatchContext:
    return BatchContext(
        batch_id=CONNECTION_ID, tenant_id=TENANT_ID, connection_id=CONNECTION_ID,
        country_code=COUNTRY, platform_code=PLATFORM, kind=BatchKind.SHIPMENTS,
    )


def _shipment(tracking: str) -> ShipmentInput:
    return ShipmentInput(
        tracking_number=tracking, created_date=date(2026, 7, 1),
        currency_code="COP", status_code="created",
    )


def test_a_row_the_database_rejects_fails_alone_in_the_batch_fallback(monkeypatch):
    """Antes la excepción del respaldo fila-a-fila subía fuera de ingest() y el
    archivo entero se revertía por una sola guía con un número fuera de rango."""
    store = PostgresStore(_FailingBatchConn())
    monkeypatch.setattr(store, "_shipment_params", lambda ctx, s: {})
    monkeypatch.setattr(store, "_probe_money_bulk", lambda ctx, t: {})

    def fake_upsert(ctx, shipment):
        if shipment.tracking_number == "BAD":
            raise psycopg.errors.NumericValueOutOfRange("numeric field overflow")
        return UpsertResult(RowOutcome.INSERTED, shipment.tracking_number)

    monkeypatch.setattr(store, "upsert_shipment", fake_upsert)

    results = store.upsert_shipments(_ctx(), [_shipment("G1"), _shipment("BAD"), _shipment("G3")])

    assert [r.outcome for r in results] == [
        RowOutcome.INSERTED, RowOutcome.FAILED, RowOutcome.INSERTED,
    ]
    assert "BAD" in (results[1].error or "")


def test_the_failed_row_is_counted_and_explained_in_the_report():
    report = IngestReport(
        batch_id=None, content_hash="x", source_name="f.csv",
        kind=BatchKind.SHIPMENTS, started_at=datetime.now(UTC),
    )
    _tally(report, UpsertResult(RowOutcome.FAILED, "BAD", error="BAD: rechazada"))
    assert report.rows_failed == 1
    assert report.errors[0].message == "BAD: rechazada"
