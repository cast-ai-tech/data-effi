"""Dropi API orders -> the same table Dropi's own export produces.

WHY A CSV AND NOT A DIRECT INSERT
---------------------------------
Master Data has ONE way into the database (docs/arquitectura-multipais-conectores.md
§5): bytes -> IngestEngine -> Store. An upload, a published sheet and a tier-3
fetch all go through it, and so does this connector. The API's JSON is rendered
as a CSV whose headers are exactly those of Dropi's "Reporte de órdenes" export,
so `detect_profile` recognises it as `dropi_ordenes` (pipeline/profiles.py) and
everything downstream is already written and tested:

  - status vocabulary (migrations 002/040/047/061) and the merge rule that a
    status only advances;
  - the tracking-number rule (carrier guide, or `DROPI-<id>` for an order that
    never got one) - so an order loaded by API and the same order loaded from an
    uploaded export into the same connection are ONE guide, not two;
  - contact data encrypted into core.shipment and hashed in raw.source_row;
  - idempotence by content hash: re-reading an unchanged window is a no-op.

Fields the API does not document are left blank rather than guessed; a blank
money column does not overwrite a value an export already loaded.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# Verbatim headers of Dropi's export: normalize_text() of each one must be a key
# of pipeline.profiles.DROPI_ORDERS_COLUMNS (tests/test_dropi_connector.py
# checks it), and the five signature headers must all be present.
EXPORT_HEADERS: tuple[str, ...] = (
    "ID",
    "NÚMERO GUIA",
    "ID DE ORDEN DE TIENDA",
    "FECHA",
    "FECHA DE ÚLTIMO MOVIMIENTO",
    "ESTATUS",
    "TRANSPORTADORA",
    "DEPARTAMENTO DESTINO",
    "CIUDAD DESTINO",
    "TIENDA",
    "NOMBRE CLIENTE",
    "TELÉFONO",
    "EMAIL",
    "NRO DE IDENTIFICACION",
    "DIRECCION",
    "VALOR DE COMPRA EN PRODUCTOS",
    "PRECIO FLETE",
    "COSTO DEVOLUCION FLETE",
    "TOTAL EN PRECIOS DE PROVEEDOR",
    "COMISION",
    "GANANCIA",
    "TIPO DE ENVIO",
    "CONTADOR DE INDEMNIZACIONES",
)


def order_to_row(order: dict[str, Any]) -> dict[str, str]:
    """One API order -> one export row. Field names from Dropi's docs [B] in
    connectors/dropi/client.py; the ones marked TODO are not in any document."""
    row = dict.fromkeys(EXPORT_HEADERS, "")

    row["ID"] = _text(order.get("id"))
    row["NÚMERO GUIA"] = _text(order.get("shipping_guide"))
    row["ID DE ORDEN DE TIENDA"] = _text(order.get("shop_order_id"))
    row["FECHA"] = _text(order.get("created_at"))
    row["FECHA DE ÚLTIMO MOVIMIENTO"] = _text(order.get("updated_at"))
    row["ESTATUS"] = _text(order.get("status"))
    row["TRANSPORTADORA"] = _carrier_name(order)
    row["DEPARTAMENTO DESTINO"] = _place(order.get("state"))
    row["CIUDAD DESTINO"] = _place(order.get("city"))
    row["TIENDA"] = _nested_name(order.get("shop"))  # TODO verificar con cuenta real
    row["NOMBRE CLIENTE"] = " ".join(
        part for part in (_text(order.get("name")), _text(order.get("surname"))) if part
    )
    row["TELÉFONO"] = _text(order.get("phone"))
    row["EMAIL"] = _text(order.get("client_email"))
    row["NRO DE IDENTIFICACION"] = _text(order.get("dni"))
    row["DIRECCION"] = _text(order.get("dir"))
    row["VALOR DE COMPRA EN PRODUCTOS"] = _money(order.get("total_order"))
    row["PRECIO FLETE"] = _money(order.get("shipping_amount"))
    row["TOTAL EN PRECIOS DE PROVEEDOR"] = _supplier_total(order.get("orderdetails"))
    row["GANANCIA"] = _money(order.get("amount_earned_dropshipper"))
    row["TIPO DE ENVIO"] = _text(order.get("rate_type"))
    # TODO verificar con cuenta real: la comisión, el costo del flete de
    # devolución y el contador de indemnizaciones del export no aparecen en la
    # documentación de la API. Quedan vacíos; el export subido los completa.
    return row


def build_orders_csv(orders: list[dict[str, Any]]) -> bytes:
    """Rows in a stable order (by Dropi id) so the same data hashes the same."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_HEADERS), lineterminator="\n")
    writer.writeheader()
    for order in sorted(orders, key=_sort_key):
        writer.writerow(order_to_row(order))
    return buffer.getvalue().encode("utf-8")


def csv_filename(country_code: str, date_from: date, date_to: date) -> str:
    return f"dropi_api_ordenes_{country_code.upper()}_{date_from:%Y%m%d}_{date_to:%Y%m%d}.csv"


# -- helpers -----------------------------------------------------------------
def _sort_key(order: dict[str, Any]) -> tuple[int, str]:
    raw = order.get("id")
    try:
        return (0, f"{int(str(raw)):020d}")
    except (TypeError, ValueError):
        return (1, str(raw))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return ""
    return " ".join(str(value).split())


def _nested_name(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


def _place(value: Any) -> str:
    # TODO verificar con cuenta real: [B] muestra `state`/`city` como texto; en
    # otras rutas Dropi los anida como objeto con `name`.
    return _nested_name(value)


def _carrier_name(order: dict[str, Any]) -> str:
    # TODO verificar con cuenta real: [B] documenta `shipping_company`; la
    # creación de órdenes usa `distribution_company` como objeto con id.
    for key in ("shipping_company", "distribution_company"):
        name = _nested_name(order.get(key))
        if name:
            return name
    return ""


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _money(value: Any) -> str:
    """A plain number the engine reads unambiguously.

    parse_decimal treats `1234.567` as thousands-separated (LATAM), so money is
    written with no separator and at most two decimals - never three.
    """
    amount = _decimal(value)
    if amount is None:
        return ""
    if amount == amount.to_integral_value():
        return str(int(amount))
    return f"{amount.quantize(Decimal('0.01')):f}"


def _supplier_total(details: Any) -> str:
    """Sum of supplier price x quantity over the order lines.

    TODO verificar con cuenta real: el nombre del precio de proveedor en
    `orderdetails` no está documentado. Se prueban los candidatos en orden y, si
    ninguna línea trae uno, la columna queda vacía en vez de inventar un costo.
    """
    if not isinstance(details, list):
        return ""
    total = Decimal(0)
    found = False
    for line in details:
        if not isinstance(line, dict):
            continue
        product = line.get("product") if isinstance(line.get("product"), dict) else {}
        price = None
        for source, key in (
            (line, "supplier_price"),
            (product, "sale_price"),
            (line, "price_supplier"),
        ):
            price = _decimal((source or {}).get(key))
            if price is not None:
                break
        if price is None:
            continue
        quantity = _decimal(line.get("quantity")) or Decimal(1)
        total += price * quantity
        found = True
    return _money(total) if found else ""
