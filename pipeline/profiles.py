"""Source profiles: exact column maps for reports Data Effi knows by name.

WHY THIS EXISTS ALONGSIDE THE ALIAS MATCHER. `mapping.py` guesses: it takes a
header like "Valor" and hopes it means the declared value. That is the right
behaviour for a spreadsheet somebody made by hand, and the wrong one for a
machine-generated export with 87 columns, four of which contain the word
"valor" and three of which contain the word "total".

A profile is recognised by its exact header set, and then every column is mapped
by its exact name. No guessing, no ambiguity, and a report that changes shape
fails loudly instead of quietly mapping "Total venta proveedor" onto revenue.

Adding a platform means adding a profile here, not touching the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.models import BatchKind
from pipeline.normalize import clean_text, normalize_text, parse_decimal


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """One recognised report shape."""

    code: str
    platform_code: str
    kind: BatchKind
    label: str
    # Headers that must ALL be present for this profile to match. Normalized.
    signature: tuple[str, ...]
    # Normalized header -> canonical field name.
    columns: dict[str, str]
    # Fields the profile promises to deliver even though no single column holds
    # them (they come out of a transform).
    derived: tuple[str, ...] = field(default=())
    # Columns that identify a person. Hashed before storage in the raw archive,
    # always, whatever else happens to them downstream.
    pii_columns: tuple[str, ...] = field(default=())
    # Column holding the country this report is about, so the upload does not
    # have to be told.
    country_column: str | None = None

    @property
    def pii_columns_norm(self) -> frozenset[str]:
        return frozenset(filter(None, (normalize_text(c) for c in self.pii_columns)))

    def matches(self, normalized_headers: set[str]) -> bool:
        return all(header in normalized_headers for header in self.signature)


def _norm_keys(mapping: dict[str, str]) -> dict[str, str]:
    """Normalize the header side of a column map once, at import time."""
    out: dict[str, str] = {}
    for header, field_name in mapping.items():
        key = normalize_text(header)
        if key:
            out[key] = field_name
    return out


# =============================================================================
# Effi - guides report ("Reporte de Guías de transporte YYYY-MM-DD.xlsx")
#
# 87 columns. Only the ones Data Effi can act on are mapped; the rest are reported
# back to the user as ignored, which is how they find out Data Effi skipped
# something they cared about.
#
# THE FOUR CONTACT COLUMNS are mapped, and what happens to them afterwards
# depends on where the value is going:
#
#   raw.source_row   hashed, always. `redact_row` reads `pii_columns`, not this
#                    map, so mapping a column here cannot make the archive keep
#                    it in the clear. The archive is for auditing a file, and
#                    auditing never needs to know whose file it was.
#   core.shipment    encrypted (pipeline/crypto.py), because the orders table
#                    has to render a name and a phone for the operator who is
#                    about to call that customer back.
#
# The phone is mapped as `customer_identifier` because it is also what
# `customer_hash` is derived from: one column, two jobs - identity and display.
# =============================================================================

EFFI_GUIDES_COLUMNS = _norm_keys(
    {
        # --- identity ---
        "Guía transportadora": "carrier_tracking_number",
        "Prefijo ID guía": "external_order_id",
        "Documento de venta": "sale_document",
        # --- dates ---
        "Fecha de envío": "created_date",
        "Fecha de entrega esperada": "expected_delivery_date",
        "Fecha de estado final": "final_status_date",
        # NOT dispatched_at. This is when the ERP created the guide; the goods
        # leave on "Fecha relación de despacho", typically days later. See
        # migration 019 - mapping it here understated preparation twelvefold.
        "Fecha de creación": "created_at_source",
        "Fecha de anulación": "cancelled_at",
        "Fecha liquidación con recaudo": "settled_at",
        # --- status ---
        "Estado global guía inicial": "status_raw",
        "Estado guía inicial": "status_detail",
        "Estado global guía devolución": "return_status_raw",
        # --- who and where ---
        "Nombre transportadora Efficommerce": "carrier_name",
        "Departamento destinatario": "geo_level1",
        "Ciudad destinatario": "city_name",
        "País destinatario": "country_name",
        "Sucursal": "store_name",
        # --- the customer, for the orders table ---
        # Encrypted into core.shipment, hashed into raw.source_row. See the
        # block comment above for why the same column goes two ways.
        "Destinatario": "customer_name",
        "Teléfonos destinatario": "customer_identifier",
        "ID. destinatario": "customer_document",
        "Dirección destinatario": "customer_address",
        "Nombre Proveedor": "supplier_name",
        "Servicio": "service_level",
        # --- product ---
        "Contenido": "content_raw",
        "Cantidad de paquetes": "package_count",
        # --- money ---
        # `Valor recaudo` is what the carrier must collect at the door; that is
        # the amount the business lives on. `Valor declarado` is the INSURED
        # value and is frequently a flat 20.00 unrelated to the sale, so mapping
        # it onto declared_value made 124 of 1,649 real guides look like they
        # over-collected by 80%.
        "Valor recaudo": "declared_value",
        "Valor declarado": "insured_value",
        "Precio flete total a cliente": "freight_cost",
        "Precio manejo (seguro) a cliente": "insurance_cost",
        "Precio recaudo a cliente": "collection_fee",
        "Costo documento de venta": "product_cost",
        "Total documento de venta": "sale_total",
        "Precio flete a cliente": "freight_base",
        "% Descuento": "discount_pct",
        "Peso (Kg)": "weight_kg",
        # --- the dropshipping chain ---
        "Nombre Distribuidor": "distributor_name",
        "Total venta distribuidor": "distributor_sale_total",
        "Total compra distribuidor": "distributor_cost_total",
        "Total venta proveedor": "supplier_sale_total",
        # --- dispatch ---
        "Relación de despacho": "dispatch_batch_ref",
        "Fecha relación de despacho": "dispatched_batch_at",
        # --- settlement, all six kinds ---
        "Liquidación con recaudo": "settled_with_collection",
        "Liquidación (de cualquier tipo)": "settled_any",
        "Liquidación devolución": "settled_return",
        "Fecha liquidación (de cualquier tipo)": "settled_any_at",
        "Fecha liquidación devolución": "settled_return_at",
        # --- return leg (the status column is already mapped above) ---
        "Guía devolución transportadora": "return_tracking_number",
    }
)

# Columns that identify a human being. In the raw archive they are stored ONLY
# as a SHA-256. Ecuador's LOPDP and Colombia's Ley 1581 both make keeping these
# in the clear a liability, and no metric needs them readable: a hash still
# answers "same person?" and "same address?".
#
# The four recipient columns additionally reach core.shipment as Fernet
# ciphertext. Encrypted is not "in the clear": the key is an environment
# variable, so a stolen dump still reveals nothing.
EFFI_GUIDES_PII = (
    "Destinatario",
    "ID. destinatario",
    "Dirección destinatario",
    "Teléfonos destinatario",
    "Remitente",
    "ID. remitente",
    "Dirección remitente",
    "Teléfonos remitente",
)

EFFI_GUIDES = SourceProfile(
    code="effi_guias",
    platform_code="effi",
    kind=BatchKind.SHIPMENTS,
    label="Effi · Reporte de guías de transporte",
    signature=(
        "guia transportadora",
        "estado global guia inicial",
        "nombre transportadora efficommerce",
        "valor recaudo",
    ),
    columns=EFFI_GUIDES_COLUMNS,
    derived=("tracking_number", "product_name", "quantity", "delivered_at", "returned_at"),
    pii_columns=EFFI_GUIDES_PII,
    country_column="País destinatario",
)


# =============================================================================
# Effi - wallet movements ("Reporte de movimientos de dinero Effi ....xls")
#
# Exported as an HTML table despite the .xls name. 56 columns, one row per
# wallet entry. `Guía inicial` holds the CARRIER's tracking number, which is why
# migration 008 teaches relink_orphan_movements to match on that column too.
# =============================================================================

EFFI_MOVEMENTS_COLUMNS = _norm_keys(
    {
        "ID movimiento": "external_ref",
        "Tipo de movimiento": "movement_type_raw",
        # These two are the other way round from what the names suggest, and it
        # matters: `Fecha origen` is the date of the GUIDE the movement belongs
        # to (identical to the guide's creation date in 707 of 707 collections),
        # while `Fecha creación` is when the money actually moved - it matches
        # the guide's settlement date in 698 of 707.
        #
        # Mapped the intuitive way, every cash event landed on the day the guide
        # was created: the order card showed the cash-on-delivery collection
        # FOUR DAYS BEFORE the delivery it was collected at, and the whole money
        # series sat 4.8 days early.
        "Fecha creación": "movement_date",
        "Fecha origen": "shipment_created_ref",
        "Valor movimiento": "amount",
        "Detalle": "description",
        "Guía inicial": "tracking_number_raw",
        "Transportadora": "carrier_name",
        "Sucursal": "store_name",
        "Departamento destinatario Guía inicial": "geo_level1",
        "Ciudad destinatario Guía inicial": "city_name",
        "Contenido": "content_raw",
        # --- wallet identity, for bank reconciliation ---
        "ID Wallet": "wallet_id",
        "Medio de pago Wallet": "wallet_method",
        "Cuenta bancaria": "bank_account_ref",
        "Responsable": "operator_name",
        # --- the guide this movement belongs to ---
        "Guía adicional": "secondary_tracking_number",
        "ID venta referencia": "sale_reference",
        # --- freight breakdown, repeated here per movement ---
        "Precio flete a cliente": "freight_base",
        "Precio manejo (seguro) a cliente": "insurance_cost",
        "Precio recaudo a cliente": "collection_fee",
        "Precio flete total a cliente": "freight_cost",
        "Valor recaudo": "declared_value",
        # --- the dropshipping chain, repeated here per movement ---
        "Distribuidor": "distributor_name",
        "Total venta distribuidor": "distributor_sale_total",
        "Total compra distribuidor": "distributor_cost_total",
        "Proveedor": "supplier_name",
        "Total venta proveedor": "supplier_sale_total",
    }
)

EFFI_MOVEMENTS_PII = (
    "Titular Wallet",
    "Identificación titular",
    "Nombre remitente Guía inicial",
    "Teléfono remitente Guía inicial",
    "Dirección remitente Guía inicial",
    "ID remitente Guía inicial",
    "Nombre destinatario Guía inicial",
    "Teléfono destinatario Guía inicial",
    "Dirección destinatario Guía inicial",
    "ID destinatario Guía inicial",
)

EFFI_MOVEMENTS = SourceProfile(
    code="effi_movimientos",
    platform_code="effi",
    kind=BatchKind.MOVEMENTS,
    label="Effi · Reporte de movimientos de dinero",
    signature=(
        "id movimiento",
        "tipo de movimiento",
        "valor movimiento",
        "guia inicial",
    ),
    columns=EFFI_MOVEMENTS_COLUMNS,
    pii_columns=EFFI_MOVEMENTS_PII,
    country_column="País destinatario Guía inicial",
)


# =============================================================================
# Dropi - orders export ("ordenes_YYYYMMDD_HHMMSS.xlsx")
#
# 63 columns, one row per order, read from a real Guatemalan export (658 rows,
# 2026-08-24). The last twelve columns (FE ...) are electronic-invoicing
# fields that were empty in every row; they are left unmapped and reported as
# ignored.
#
# WHAT THE MONEY COLUMNS MEAN. `VALOR DE COMPRA EN PRODUCTOS` is what the
# customer pays at the door; `TOTAL EN PRECIOS DE PROVEEDOR` is what Dropi
# charges for the product; `PRECIO FLETE` and `COSTO DEVOLUCION FLETE` the
# two freights; `COMISION` the platform's cut. `GANANCIA` is exactly VALOR -
# PROVEEDOR - FLETE - COMISION - DEVOLUCION in 470 of 470 delivered rows, so it
# is derived, never stored: the engine computes the same thing.
#
# NO DELIVERY DATE. The export carries the order date, the guide-generation
# date and the date of the last movement, but not when the parcel was
# delivered. Delivered guides therefore have no `delivered_at`; a filter by
# delivery date leaves them out and `excluded_no_date` says how many.
# =============================================================================

DROPI_ORDERS_COLUMNS = _norm_keys(
    {
        # --- identity ---
        "ID": "external_order_id",
        "NÚMERO GUIA": "carrier_tracking_number",
        "ID DE ORDEN DE TIENDA": "store_order_id",
        "NUMERO DE PEDIDO DE TIENDA": "store_order_number",
        # --- dates ---
        "FECHA": "created_date",
        "FECHA GENERACION DE GUIA": "guide_generated_date",
        "FECHA DE ÚLTIMO MOVIMIENTO": "last_status_at",
        "FECHA DE NOVEDAD": "issue_date",
        # --- status ---
        "ESTATUS": "status_raw",
        "ÚLTIMO MOVIMIENTO": "status_detail",
        "NOVEDAD": "issue_note",
        "FUE SOLUCIONADA LA NOVEDAD": "issue_solved_raw",
        # --- who and where ---
        "TRANSPORTADORA": "carrier_name",
        "DEPARTAMENTO DESTINO": "geo_level1",
        "CIUDAD DESTINO": "city_name",
        "TIENDA": "store_name",
        "TIPO DE TIENDA": "store_type",
        "CATEGORÍAS": "product_category",
        # --- the customer, for the orders table (encrypted + hashed) ---
        "NOMBRE CLIENTE": "customer_name",
        "TELÉFONO": "customer_identifier",
        "NRO DE IDENTIFICACION": "customer_document",
        "DIRECCION": "customer_address",
        # --- money ---
        "VALOR DE COMPRA EN PRODUCTOS": "declared_value",
        "PRECIO FLETE": "freight_cost",
        "COSTO DEVOLUCION FLETE": "return_freight_cost",
        "TOTAL EN PRECIOS DE PROVEEDOR": "product_cost",
        "COMISION": "platform_fee",
        "GANANCIA": "reported_profit",
        "TIPO DE ENVIO": "shipping_kind",
        # --- indemnity ---
        "CONTADOR DE INDEMNIZACIONES": "compensation_count",
        "CONCEPTO ÚLTIMA INDENMIZACIÓN": "compensation_concept",
    }
)

DROPI_ORDERS_PII = (
    "NOMBRE CLIENTE",
    "TELÉFONO",
    "EMAIL",
    "NRO DE IDENTIFICACION",
    "DIRECCION",
    "VENDEDOR",
    "USUARIO GENERACION DE GUIA",
    "USUARIO QUE SOLUCIONA LA NOVEDAD",
)

DROPI_ORDERS = SourceProfile(
    code="dropi_ordenes",
    platform_code="dropi",
    kind=BatchKind.SHIPMENTS,
    label="Dropi · Reporte de órdenes",
    signature=(
        "numero guia",
        "estatus",
        "transportadora",
        "valor de compra en productos",
        "total en precios de proveedor",
    ),
    columns=DROPI_ORDERS_COLUMNS,
    derived=("tracking_number",),
    pii_columns=DROPI_ORDERS_PII,
    # The export says nothing about its country: the upload declares it
    # (migration 042). The department names are the only hint, and a hint is
    # not a fact.
    country_column=None,
)


# =============================================================================
# Dropi - wallet history ("historial de cartera-DD-MM-YYYY HH_MM.xlsx")
#
# 9 columns. `TIPO` is ENTRADA or SALIDA and `MONTO` is always a positive
# magnitude; what the money IS lives in the free text of `DESCRIPCIÓN`, whose
# first words are one of five fixed phrases. See migration 047 for why the two
# settlement phrases become `transfer` types that no KPI adds up.
# =============================================================================

DROPI_WALLET_COLUMNS = _norm_keys(
    {
        "ID": "external_ref",
        "FECHA": "movement_date",
        "TIPO": "direction_raw",
        "MONTO": "amount",
        "MONTO PREVIO": "balance_before",
        "ORDEN ID": "order_ref",
        "NUMERO DE GUIA": "tracking_number_raw",
        "DESCRIPCIÓN": "description",
        "CONCEPTO DE RETIRO": "withdrawal_concept",
    }
)

DROPI_WALLET = SourceProfile(
    code="dropi_cartera",
    platform_code="dropi",
    kind=BatchKind.MOVEMENTS,
    label="Dropi · Historial de cartera",
    signature=("monto previo", "numero de guia", "descripcion", "tipo"),
    columns=DROPI_WALLET_COLUMNS,
    country_column=None,
)


# The five phrases the wallet writes, by their first words. Mirrors migration
# 047: the two settlements are `transfer` types, ignored by every KPI.
DROPI_MOVEMENT_TYPES: dict[str, str] = {
    "entrada por ganancia en la orden como dropshipper": "settlement_in",
    "salida de cobro de devolucion por entrega no efectiva": "settlement_out",
    "salida por nueva orden": "settlement_out",
    "salida por peticion de retiro de saldo en cartera": "withdrawal",
    "devolucion de dinero por garantia": "adjustment_in",
}


def resolve_dropi_movement_type(description: Any) -> tuple[str | None, bool]:
    """Map a Dropi wallet description to a movement type. Returns (code, recognized)."""
    key = normalize_text(description)
    if not key:
        return None, False
    for phrase, code in DROPI_MOVEMENT_TYPES.items():
        if key.startswith(phrase):
            return code, True
    return None, False


PROFILES: tuple[SourceProfile, ...] = (EFFI_GUIDES, EFFI_MOVEMENTS, DROPI_ORDERS, DROPI_WALLET)


def detect_profile(headers: list[str], kind: BatchKind | None = None) -> SourceProfile | None:
    """Identify the report from its headers. None means "use the alias matcher"."""
    normalized = {normalize_text(h) or "" for h in headers}
    for profile in PROFILES:
        if kind is not None and profile.kind is not kind:
            continue
        if profile.matches(normalized):
            return profile
    return None


def build_profile_header_map(
    headers: list[str], profile: SourceProfile
) -> tuple[dict[int, str], list[str]]:
    """Map column positions to canonical fields using the profile's exact names."""
    mapped: dict[int, str] = {}
    unmapped: list[str] = []

    for position, header in enumerate(headers):
        key = normalize_text(header)
        if not key:
            continue
        field_name = profile.columns.get(key)
        if field_name:
            mapped[position] = field_name
        else:
            unmapped.append(str(header).strip())

    return mapped, unmapped


# =============================================================================
# Transforms the profiles need
# =============================================================================

# "3 * CLOROFILA , DETOX." -> (3, "CLOROFILA DETOX")
# Effi writes the quantity, an asterisk, the product, and a trailing period.
# Commas inside the name are decoration from the catalogue, not separators.


def parse_content(raw: Any) -> tuple[int | None, str | None, int]:
    """Parse Effi's `Contenido` field.

    Returns (quantity, product_name, extra_items). `extra_items` is how many
    additional products the cell held - a multi-product guide that Data Effi's
    one-product-per-shipment model cannot represent, and which the batch report
    surfaces rather than silently dropping.
    """
    text = clean_text(raw)
    if not text:
        return None, None, 0

    parts = [segment.strip() for segment in text.split(".") if segment.strip()]
    if not parts:
        return None, None, 0

    first = parts[0]
    extra = len(parts) - 1

    if "*" in first:
        quantity_text, _, name = first.partition("*")
        quantity_digits = "".join(ch for ch in quantity_text if ch.isdigit())
        quantity = int(quantity_digits) if quantity_digits else 1
    else:
        quantity, name = 1, first

    cleaned = " ".join(name.replace(",", " ").split())
    return quantity, (cleaned or None), extra


# Effi's wallet vocabulary, verbatim from a real export.
EFFI_MOVEMENT_TYPES: dict[str, str] = {
    "recaudo de venta": "cod_collected",
    "flete credito con recaudo": "freight_out",
    "flete credito sin recaudo": "freight_out",
    "flete credito devolucion": "freight_return",
    "flete contado": "freight_out",
    "compra de mercancia dropshipping a proveedor": "product_cost",
    "compra de mercancia a proveedor": "product_cost",
    "comision local por retiro de wallet": "withdrawal_fee",
    "comision por recaudo": "collection_fee",
    "retiro de dinero de cuenta": "withdrawal",
    "retencion en la fuente a favor": "tax_withholding",
    "retencion en la fuente": "tax_withholding",
    "indemnizacion": "adjustment_in",
    "ajuste manual": "adjustment_in",
}


def resolve_effi_movement_type(raw: Any) -> tuple[str | None, bool]:
    """Map an Effi wallet movement type. Returns (code, recognized)."""
    key = normalize_text(raw)
    if not key:
        return None, False
    code = EFFI_MOVEMENT_TYPES.get(key)
    if code:
        return code, True
    # Tolerate a suffix Effi may append, e.g. "Recaudo de venta (parcial)".
    for alias, mapped in EFFI_MOVEMENT_TYPES.items():
        if key.startswith(alias):
            return mapped, True
    return None, False


# Movement types that move money between the user's own accounts. They are
# recorded for the audit trail and excluded from every cost calculation.
TRANSFER_TYPES = frozenset({"withdrawal"})


def is_yes(value: Any) -> bool | None:
    """Effi writes 'Si' / 'No'. Anything else is unknown, not False."""
    text = normalize_text(value)
    if text in {"si", "sí", "yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    return None


# =============================================================================
# Profile transforms
#
# A profile maps columns; a transform turns those columns into the fields the
# engine actually stores. Effi needs both, because several of its fields carry
# more than one piece of information:
#
#   * `Contenido` holds quantity AND product name
#   * `Fecha de estado final` is the delivery date OR the return date, depending
#     on where the guide ended up
#   * status lives in two columns of different granularity
# =============================================================================

# Effi statuses that mean the guide came back rather than arrived.
_RETURNED_HINTS = ("devolucion", "devuelto", "devuelta")
_DELIVERED_HINTS = ("entregad", "retirado en oficina")


def transform_effi_guide(mapped: dict[str, Any]) -> dict[str, Any]:
    """Turn a mapped Effi guide row into engine fields.

    Returns a NEW dict; the caller keeps the original for the audit trail.
    """
    out = dict(mapped)

    # --- identity -------------------------------------------------------
    # The carrier's number is what the money report cites, so it is the primary
    # key. Effi's own id is the fallback for a guide the carrier has not
    # numbered yet, and is kept either way.
    carrier_number = clean_text(mapped.get("carrier_tracking_number"))
    effi_id = clean_text(mapped.get("external_order_id"))
    out["tracking_number"] = carrier_number or effi_id
    out["carrier_tracking_number"] = carrier_number

    # --- status ---------------------------------------------------------
    # `Estado global guía inicial` is the canonical one. When a return is under
    # way, the return status wins: a guide "delivered to origin" is a return,
    # not a delivery.
    return_status = normalize_text(mapped.get("return_status_raw"))
    global_status = normalize_text(mapped.get("status_raw"))
    detail_status = normalize_text(mapped.get("status_detail"))

    out["status_raw"] = (
        clean_text(mapped.get("return_status_raw"))
        or clean_text(mapped.get("status_raw"))
        or clean_text(mapped.get("status_detail"))
    )
    out["status_detail"] = clean_text(mapped.get("status_detail"))

    effective = return_status or global_status or detail_status or ""

    # --- final-state date -----------------------------------------------
    # One column, two meanings. Which one depends on the status.
    final_date = mapped.get("final_status_date")
    if final_date:
        if any(hint in effective for hint in _RETURNED_HINTS):
            out["returned_at"] = final_date
        elif any(hint in effective for hint in _DELIVERED_HINTS):
            out["delivered_at"] = final_date
        out["last_status_at"] = final_date

    # A cancelled guide gets its own timestamp column in the export.
    if mapped.get("cancelled_at") and not out.get("returned_at"):
        out["last_status_at"] = mapped["cancelled_at"]

    # --- product --------------------------------------------------------
    quantity, product_name, extra_items = parse_content(mapped.get("content_raw"))
    if product_name:
        out["product_name"] = product_name
    if quantity:
        out["quantity"] = quantity
    out["_extra_products"] = extra_items

    # --- settlement -----------------------------------------------------
    out["settled_with_collection"] = is_yes(mapped.get("settled_with_collection"))
    out["settled_return"] = is_yes(mapped.get("settled_return"))
    # A settlement date without a settlement flag is still a settlement.
    if not mapped.get("settled_at"):
        out["settled_at"] = None

    return out


def transform_effi_movement(mapped: dict[str, Any]) -> dict[str, Any]:
    """Turn a mapped Effi wallet row into engine fields."""
    out = dict(mapped)
    code, recognized = resolve_effi_movement_type(mapped.get("movement_type_raw"))
    out["_movement_type_code"] = code
    out["_movement_type_recognized"] = recognized
    return out


def transform_dropi_order(mapped: dict[str, Any]) -> dict[str, Any]:
    """Turn a mapped Dropi order row into engine fields.

    Returns a NEW dict; the caller keeps the original for the audit trail.
    """
    out = dict(mapped)

    # --- identity -------------------------------------------------------
    # The guide number is the carrier's own (Forza, Gintracom...) and is what
    # the wallet cites, so it is the primary key. A cancelled order may never
    # have received one: Dropi's order id stands in, prefixed so it can never
    # collide with a carrier number.
    carrier_number = clean_text(mapped.get("carrier_tracking_number"))
    order_id = clean_text(mapped.get("external_order_id"))
    out["tracking_number"] = carrier_number or (f"DROPI-{order_id}" if order_id else None)
    out["carrier_tracking_number"] = carrier_number

    # --- status ---------------------------------------------------------
    # An order the carrier lost and paid back shows it only in the indemnity
    # counter; ESTATUS keeps saying whatever it said before. The counter wins.
    counter = mapped.get("compensation_count")
    try:
        compensated = counter is not None and int(counter) > 0
    except (TypeError, ValueError):
        compensated = False
    if compensated:
        out["status_raw"] = "Indemnizada"
        out["status_detail"] = clean_text(mapped.get("compensation_concept")) or clean_text(
            mapped.get("status_detail")
        )
    else:
        out["status_raw"] = clean_text(mapped.get("status_raw"))
        out["status_detail"] = clean_text(mapped.get("status_detail"))

    # One product per order in this export, quantity unknown: one, not zero.
    out.setdefault("quantity", 1)
    return out


def transform_dropi_movement(mapped: dict[str, Any]) -> dict[str, Any]:
    """Turn a mapped Dropi wallet row into engine fields.

    `MONTO` is always a positive magnitude and `TIPO` says the direction. The
    engine reads direction off the sign of the amount (and reports a row whose
    sign contradicts its type), so a SALIDA is handed over negative - the same
    shape Effi's wallet already has.
    """
    out = dict(mapped)
    code, recognized = resolve_dropi_movement_type(mapped.get("description"))
    out["_movement_type_code"] = code
    out["_movement_type_recognized"] = recognized

    direction = normalize_text(mapped.get("direction_raw"))
    amount = parse_decimal(mapped.get("amount"))
    if amount is not None and direction == "salida" and amount > 0:
        out["amount"] = -amount
    return out


TRANSFORMS = {
    "effi_guias": transform_effi_guide,
    "effi_movimientos": transform_effi_movement,
    "dropi_ordenes": transform_dropi_order,
    "dropi_cartera": transform_dropi_movement,
}


def apply_transform(profile: SourceProfile, mapped: dict[str, Any]) -> dict[str, Any]:
    transform = TRANSFORMS.get(profile.code)
    return transform(mapped) if transform else mapped


# =============================================================================
# Raw retention and country detection
# =============================================================================

# Mirror of core.country_alias (migrations 010, 033, 034, 038), so a file can be
# inspected without a database - which is what the upload screen does before
# uploading. Keys are already normalized (lowercase, no accents): the raw value
# goes through normalize_text() first, so "Costa Rica", "COSTA RICA" and
# "República de Costa Rica" all land here as one of these keys.
#
# tests/test_store_pg.py checks this dict against core.country_alias in BOTH
# directions, so a country added by migration without a line here fails the
# suite by name instead of silently going undetected on upload.
COUNTRY_ALIASES: dict[str, str] = {
    "colombia": "CO", "co": "CO", "col": "CO", "republica de colombia": "CO",
    "ecuador": "EC", "ec": "EC", "ecu": "EC", "republica del ecuador": "EC",
    "mexico": "MX", "mx": "MX", "mex": "MX", "estados unidos mexicanos": "MX",
    "peru": "PE", "pe": "PE", "per": "PE",
    "chile": "CL", "cl": "CL", "chl": "CL",
    "panama": "PA", "pa": "PA", "pan": "PA",
    "guatemala": "GT", "gt": "GT", "gtm": "GT",
    "honduras": "HN", "hn": "HN", "hnd": "HN", "republica de honduras": "HN",
    "costa rica": "CR", "cr": "CR", "cri": "CR", "republica de costa rica": "CR",
    "republica dominicana": "DO", "dominicana": "DO", "santo domingo": "DO",
    "do": "DO", "dom": "DO", "rd": "DO",
    "venezuela": "VE", "ve": "VE", "ven": "VE",
    "republica bolivariana de venezuela": "VE",
}


def resolve_country(raw: Any) -> str | None:
    """Country name as a report writes it -> ISO code. None when unrecognised."""
    key = normalize_text(raw)
    return COUNTRY_ALIASES.get(key) if key else None


def detect_country(
    headers: list[str], rows: list[list[Any]], profile: SourceProfile, sample: int = 50
) -> tuple[str | None, str | None]:
    """Read the country out of the file itself.

    Returns (iso_code, raw_value_seen). Samples rows rather than reading all of
    them: a report covering two countries is a different problem, and the
    ingestion engine reports that separately.
    """
    if not profile.country_column:
        return None, None

    target = normalize_text(profile.country_column)
    position = next(
        (i for i, header in enumerate(headers) if normalize_text(header) == target), None
    )
    if position is None:
        return None, None

    seen: dict[str, int] = {}
    for row in rows[:sample]:
        if position >= len(row):
            continue
        value = clean_text(row[position])
        if value:
            seen[value] = seen.get(value, 0) + 1

    if not seen:
        return None, None

    most_common = max(seen, key=lambda k: seen[k])
    return resolve_country(most_common), most_common


def redact_row(
    raw: dict[str, Any], pii_headers: frozenset[str], salt: str
) -> tuple[dict[str, Any], list[str]]:
    """Prepare a raw row for storage, hashing anything that identifies a person.

    EVERY column is kept - that is the point of the archive. The four or ten
    that name, number, locate or phone a human being are replaced by a salted
    SHA-256, which still answers "is this the same person?" and "have we shipped
    to this address before?" while a stolen backup reveals nothing.

    Returns (payload, names of the columns that were hashed).
    """
    import hashlib

    payload: dict[str, Any] = {}
    redacted: list[str] = []

    for header, value in raw.items():
        if normalize_text(header) in pii_headers:
            text = clean_text(value)
            if text:
                digest = hashlib.sha256(f"{salt}:{text}".encode()).hexdigest()
                payload[header] = f"sha256:{digest[:32]}"
                redacted.append(header)
            else:
                payload[header] = None
        else:
            payload[header] = value

    return payload, redacted
