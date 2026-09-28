"""The Dropi API connector, with Dropi replaced by a fake. No real HTTP.

Three things are defended here:

1. What the API returns lands in the database through the SAME door as an
   uploaded export: the rendered CSV is recognised as `dropi_ordenes` and the
   ingest engine does the rest (status, tracking number, PII, idempotence).
2. The client behaves under failure: a rejected token is final and never
   retried, a rate limit is waited out, a dead server is given up on - and the
   token appears in no message, ever.
3. The window: a first sync backfills, later ones roll, a long outage catches up.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from uuid import UUID

import httpx
import pytest

from connectors.dropi import client as dropi_client
from connectors.dropi.client import (
    INTEGRATION_KEY_HEADER,
    DropiAuthError,
    DropiClient,
    DropiResponseError,
    DropiTransientError,
    UnsupportedCountryError,
    base_url_for,
    parse_orders_envelope,
)
from connectors.dropi.orders import (
    EXPORT_HEADERS,
    build_orders_csv,
    csv_filename,
    order_to_row,
)
from connectors.dropi.sync import BACKFILL_DAYS, LOOKBACK_DAYS, MAX_CATCHUP_DAYS, plan_window
from pipeline.ingest import IngestEngine, MemoryStore
from pipeline.models import BatchKind
from pipeline.normalize import normalize_text
from pipeline.profiles import DROPI_ORDERS, detect_profile
from pipeline.readers import read_tabular

TENANT = UUID("11111111-1111-1111-1111-111111111111")
CONNECTION = UUID("44444444-4444-4444-4444-444444444444")
TOKEN = "tok-ficticio-0123456789abcdef"

# Shaped like the order in Dropi's document [B] (connectors/dropi/client.py).
ORDER_DELIVERED = {
    "id": 2008350,
    "status": "ENTREGADO",
    "shipping_guide": "014117806991",
    "shipping_company": "INTERRAPIDISIMO",
    "total_order": 89900,
    "shipping_amount": "12500.00",
    "name": "Juana",
    "surname": "Ficticia",
    "phone": "3001234567",
    "client_email": "juana@example.com",
    "dir": "Calle 45 # 12-34",
    "state": "CUNDINAMARCA",
    "city": "BOGOTA",
    "created_at": "2026-07-03T15:20:11.000000Z",
    "updated_at": "2026-07-08 10:00:00",
    "rate_type": "CON RECAUDO",
    "amount_earned_dropshipper": 30000,
    "orderdetails": [
        {"quantity": 2, "product": {"sale_price": 20000}},
    ],
}
ORDER_IN_TRANSIT = {
    **ORDER_DELIVERED,
    "id": 2008351,
    "status": "EN RUTA",
    "shipping_guide": "014117806987",
    "phone": "3109876543",
    "created_at": "2026-07-05 09:00:00",
}
ORDER_CANCELLED_NO_GUIDE = {
    **ORDER_DELIVERED,
    "id": 2008352,
    "status": "CANCELADO",
    "shipping_guide": None,
    "created_at": "2026-07-06 09:00:00",
}


# =============================================================================
# 1. The rendered CSV is Dropi's own export, as far as the engine can tell
# =============================================================================


def test_every_header_is_a_column_the_profile_knows():
    known = set(DROPI_ORDERS.columns) | {normalize_text(h) for h in DROPI_ORDERS.pii_columns}
    unknown = [h for h in EXPORT_HEADERS if normalize_text(h) not in known]
    assert unknown == []


def test_the_csv_is_recognised_as_the_dropi_orders_profile():
    payload = build_orders_csv([ORDER_DELIVERED])
    headers, rows = read_tabular(payload, "dropi_api.csv")
    profile = detect_profile(headers, BatchKind.SHIPMENTS)
    assert profile is DROPI_ORDERS
    assert len(rows) == 1


def test_an_order_becomes_the_row_the_export_would_have_had():
    row = order_to_row(ORDER_DELIVERED)
    assert row["ID"] == "2008350"
    assert row["NÚMERO GUIA"] == "014117806991"
    assert row["ESTATUS"] == "ENTREGADO"
    assert row["NOMBRE CLIENTE"] == "Juana Ficticia"
    assert row["VALOR DE COMPRA EN PRODUCTOS"] == "89900"
    assert row["PRECIO FLETE"] == "12500"
    assert row["TOTAL EN PRECIOS DE PROVEEDOR"] == "40000"
    # Unknown to the API documents: blank, never guessed.
    assert row["COMISION"] == ""
    assert row["CONTADOR DE INDEMNIZACIONES"] == ""


def test_money_is_never_written_with_three_decimals():
    """`1234.567` reads as thousands in LATAM; the renderer must not produce it."""
    row = order_to_row({**ORDER_DELIVERED, "total_order": "1234.567"})
    assert row["VALOR DE COMPRA EN PRODUCTOS"] == "1234.57"


def test_nested_names_are_read_whether_text_or_object():
    row = order_to_row({
        **ORDER_DELIVERED,
        "shipping_company": None,
        "distribution_company": {"id": 3, "name": "SERVIENTREGA"},
        "state": {"name": "ANTIOQUIA"},
        "city": {"name": "MEDELLIN"},
    })
    assert row["TRANSPORTADORA"] == "SERVIENTREGA"
    assert row["DEPARTAMENTO DESTINO"] == "ANTIOQUIA"
    assert row["CIUDAD DESTINO"] == "MEDELLIN"


def test_the_same_orders_in_any_order_render_the_same_bytes():
    one = build_orders_csv([ORDER_DELIVERED, ORDER_IN_TRANSIT])
    two = build_orders_csv([ORDER_IN_TRANSIT, ORDER_DELIVERED])
    assert one == two


def test_the_filename_says_country_and_window():
    assert csv_filename("co", date(2026, 7, 1), date(2026, 7, 15)) == (
        "dropi_api_ordenes_CO_20260701_20260715.csv"
    )


def _ingest(store, payload, name="dropi_api.csv"):
    engine = IngestEngine(store, pii_salt="test-salt", today=date(2026, 8, 1))
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=BatchKind.SHIPMENTS,
        tenant_id=TENANT,
        connection_id=CONNECTION,
        country_code="CO",
        platform_code="dropi",
        default_currency="COP",
    )


def test_api_orders_land_as_guides_with_their_canonical_status():
    store = MemoryStore()
    report = _ingest(
        store, build_orders_csv([ORDER_DELIVERED, ORDER_IN_TRANSIT, ORDER_CANCELLED_NO_GUIDE])
    )

    assert report.profile_code == "dropi_ordenes"
    assert report.rows_failed == 0, report.errors
    assert report.rows_inserted == 3

    assert store.shipments[(CONNECTION, "014117806991")].status_code == "delivered"
    assert store.shipments[(CONNECTION, "014117806987")].status_code == "in_transit"
    # No guide: Dropi's id stands in, prefixed, exactly like the export.
    assert store.shipments[(CONNECTION, "DROPI-2008352")].status_code == "cancelled"


def test_the_same_window_twice_is_a_no_op():
    store = MemoryStore()
    payload = build_orders_csv([ORDER_DELIVERED, ORDER_IN_TRANSIT])
    _ingest(store, payload)
    again = _ingest(store, payload)
    assert again.already_loaded is True
    assert len(store.shipments) == 2


def test_a_status_that_moved_updates_the_guide_instead_of_duplicating_it():
    store = MemoryStore()
    _ingest(store, build_orders_csv([ORDER_IN_TRANSIT]))
    moved = {**ORDER_IN_TRANSIT, "status": "ENTREGADO", "updated_at": "2026-07-09 11:00:00"}
    report = _ingest(store, build_orders_csv([moved]), name="dropi_api_2.csv")

    assert report.rows_updated == 1
    assert len(store.shipments) == 1
    assert store.shipments[(CONNECTION, "014117806987")].status_code == "delivered"


# =============================================================================
# 2. The client, against a fake Dropi
# =============================================================================


class FakeDropi:
    """Answers from a script of (status, json, headers) and records requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        status, body, headers = step
        return httpx.Response(status, json=body, headers=headers or {})


def _client(fake: FakeDropi, *, page_size: int = 2, sleeps: list[float] | None = None):
    record = sleeps if sleeps is not None else []
    return DropiClient(
        token=TOKEN,
        country_code="CO",
        transport=httpx.MockTransport(fake.handler),
        page_size=page_size,
        min_interval_seconds=0,
        sleep=record.append,
    )


def _page(orders, count=None):
    body = {"isSuccess": True, "status": 200, "objects": orders}
    if count is not None:
        body["count"] = count
    return (200, body, None)


def test_the_token_travels_in_the_integration_header_and_nowhere_else():
    fake = FakeDropi([_page([ORDER_DELIVERED])])
    _client(fake).test_connection()

    request = fake.requests[0]
    assert request.headers[INTEGRATION_KEY_HEADER] == TOKEN
    assert TOKEN not in str(request.url)
    assert request.method == "GET"
    assert request.url.path == "/integrations/orders/myorders"


def test_a_window_pages_until_a_short_page_and_sends_the_documented_filters():
    fake = FakeDropi([
        _page([ORDER_DELIVERED, ORDER_IN_TRANSIT], count=3),
        _page([ORDER_CANCELLED_NO_GUIDE], count=3),
    ])
    summary = _client(fake).fetch_orders(date_from=date(2026, 7, 1), date_to=date(2026, 7, 15))

    assert [o["id"] for o in summary.orders] == [2008350, 2008351, 2008352]
    assert summary.pages == 2
    assert summary.warning is None

    first, second = (dict(r.url.params) for r in fake.requests)
    assert first["from"] == "2026-07-01"
    assert first["untill"] == "2026-07-15"
    assert first["filter_date_by"] == "FECHA DE CREADO"
    assert first["result_number"] == "2"
    assert first["start"] == "0"
    assert second["start"] == "2"


def test_a_repeated_page_stops_the_loop_instead_of_spinning():
    """If `start` turns out to mean something else, the same page comes back."""
    same = _page([ORDER_DELIVERED, ORDER_IN_TRANSIT])
    fake = FakeDropi([same, same])
    summary = _client(fake).fetch_orders(date_from=date(2026, 7, 1), date_to=date(2026, 7, 2))
    assert len(summary.orders) == 2
    assert len(fake.requests) == 2


def test_fewer_orders_than_dropi_announced_is_reported_not_hidden():
    fake = FakeDropi([_page([ORDER_DELIVERED], count=250)])
    summary = _client(fake).fetch_orders(date_from=date(2026, 7, 1), date_to=date(2026, 7, 2))
    assert "250" in (summary.warning or "")


def test_a_page_cap_is_reported_as_truncated(monkeypatch):
    monkeypatch.setattr(dropi_client, "MAX_PAGES_PER_WINDOW", 2)
    fake = FakeDropi([
        _page([ORDER_DELIVERED, ORDER_IN_TRANSIT]),
        _page([ORDER_CANCELLED_NO_GUIDE, {**ORDER_DELIVERED, "id": 9}]),
    ])
    summary = _client(fake).fetch_orders(date_from=date(2026, 7, 1), date_to=date(2026, 7, 2))
    assert summary.truncated is True
    assert summary.warning


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_is_final_and_never_retried(status):
    fake = FakeDropi([(status, {"isSuccess": False, "message": "Access denied"}, None)])
    with pytest.raises(DropiAuthError) as excinfo:
        _client(fake).test_connection()
    assert len(fake.requests) == 1
    assert TOKEN not in str(excinfo.value)
    assert "Access denied" in str(excinfo.value)


def test_a_rate_limit_is_waited_out_honouring_retry_after():
    sleeps: list[float] = []
    fake = FakeDropi([
        (429, {"message": "Too Many Attempts."}, {"Retry-After": "7"}),
        _page([ORDER_DELIVERED]),
    ])
    page = _client(fake, sleeps=sleeps).test_connection()
    assert len(page.orders) == 1
    assert 7.0 in sleeps


def test_a_server_that_keeps_failing_is_given_up_on_with_backoff():
    sleeps: list[float] = []
    fake = FakeDropi([(503, {}, None)] * 4)
    with pytest.raises(DropiTransientError) as excinfo:
        _client(fake, sleeps=sleeps).test_connection()
    assert len(fake.requests) == 4
    assert sleeps == [2.0, 4.0, 8.0]
    assert "503" in str(excinfo.value)


def test_a_timeout_is_retried_like_a_5xx():
    fake = FakeDropi([httpx.ReadTimeout("slow"), _page([ORDER_DELIVERED])])
    assert len(_client(fake).test_connection().orders) == 1


def test_a_network_error_never_carries_the_url_or_token():
    fake = FakeDropi([httpx.ConnectError(f"boom {TOKEN}")] * 4)
    with pytest.raises(DropiTransientError) as excinfo:
        _client(fake).test_connection()
    assert TOKEN not in str(excinfo.value)
    assert "ConnectError" in str(excinfo.value)


def test_is_success_false_is_an_error_with_dropis_message():
    with pytest.raises(DropiResponseError, match="token vencido"):
        parse_orders_envelope({"isSuccess": False, "message": "token vencido"})


def test_a_nested_paginator_is_unwrapped():
    page = parse_orders_envelope({"isSuccess": True, "objects": {"data": [ORDER_DELIVERED]}})
    assert len(page.orders) == 1


def test_the_repr_hides_the_token():
    fake = FakeDropi([])
    assert TOKEN not in repr(_client(fake))


def test_an_unknown_country_says_which_ones_exist():
    with pytest.raises(UnsupportedCountryError, match="CO"):
        base_url_for("AR")


def test_a_blank_token_is_refused_before_any_request():
    with pytest.raises(DropiAuthError):
        DropiClient(token="  ", country_code="CO")


# =============================================================================
# 3. The window
# =============================================================================

TODAY = date(2026, 9, 28)


def test_the_first_sync_backfills():
    window = plan_window(today=TODAY, synced_through=None)
    assert (window.date_to - window.date_from).days == BACKFILL_DAYS - 1
    assert window.date_to == TODAY


def test_later_syncs_roll_back_far_enough_for_open_orders():
    window = plan_window(today=TODAY, synced_through=TODAY)
    assert (window.date_to - window.date_from).days == LOOKBACK_DAYS - 1


def test_a_long_outage_catches_up_from_the_cursor():
    cursor = date(2026, 7, 1)
    window = plan_window(today=TODAY, synced_through=cursor)
    assert window.date_from == cursor


def test_catching_up_is_capped():
    window = plan_window(today=TODAY, synced_through=date(2025, 1, 1))
    assert (window.date_to - window.date_from).days == MAX_CATCHUP_DAYS - 1


def test_slices_cover_the_window_without_gaps_or_overlap():
    window = plan_window(today=TODAY, synced_through=None)
    slices = window.slices()
    assert slices[0][0] == window.date_from
    assert slices[-1][1] == window.date_to
    for (_, end), (start, _) in pairwise(slices):
        assert (start - end).days == 1
