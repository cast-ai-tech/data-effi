"""Source profiles: exact column maps for reports Norte knows by name.

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
from pipeline.normalize import clean_text, normalize_text


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
# 87 columns. Only the ones Norte can act on are mapped; the rest are reported
# back to the user as ignored, which is how they find out Norte skipped
# something they cared about.
#
# NOT MAPPED ON PURPOSE - customer PII:
#   Destinatario, ID. destinatario, Dirección destinatario
# The phone is mapped only so it can be hashed; it is never stored as given.
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
        "Fecha de creación": "dispatched_at",
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
        "Teléfonos destinatario": "customer_identifier",
        "Sucursal": "store_name",
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
        # --- settlement ---
        "Liquidación con recaudo": "settled_with_collection",
    }
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
        "Fecha origen": "movement_date",
        "Fecha creación": "created_at_raw",
        "Valor movimiento": "amount",
        "Detalle": "description",
        "Guía inicial": "tracking_number_raw",
        "Transportadora": "carrier_name",
        "Sucursal": "store_name",
        "Departamento destinatario Guía inicial": "geo_level1",
        "Ciudad destinatario Guía inicial": "city_name",
        "Contenido": "content_raw",
    }
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
)


PROFILES: tuple[SourceProfile, ...] = (EFFI_GUIDES, EFFI_MOVEMENTS)


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
    additional products the cell held - a multi-product guide that Norte's
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


TRANSFORMS = {
    "effi_guias": transform_effi_guide,
    "effi_movimientos": transform_effi_movement,
}


def apply_transform(profile: SourceProfile, mapped: dict[str, Any]) -> dict[str, Any]:
    transform = TRANSFORMS.get(profile.code)
    return transform(mapped) if transform else mapped
