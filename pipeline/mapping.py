"""Column and status dictionaries.

Two things live here, and both are mirrors of what migration 002 seeds into the
database:

* COLUMN_ALIASES - the many header spellings a field arrives under.
* STATUS_CANON / STATUS_ALIASES - the canonical status ladder.

They are duplicated in Python on purpose: the ingestion engine must be able to
parse and merge a file with no database connection at all (MemoryStore), which is
what makes its rules testable. `tests/test_ingest_e2e.py` asserts that the two
copies agree with the SQL seed whenever a database is available.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.models import BatchKind
from pipeline.normalize import normalize_text


@dataclass(frozen=True, slots=True)
class CanonStatus:
    code: str
    label: str
    sort_order: int
    is_terminal: bool
    is_delivered: bool
    is_returned: bool
    bucket: str


# Mirror of core.status_canon (migration 002).
STATUS_CANON: dict[str, CanonStatus] = {
    s.code: s
    for s in (
        CanonStatus("created", "Generada", 10, False, False, False, "pipeline"),
        CanonStatus("confirmed", "Confirmada", 20, False, False, False, "pipeline"),
        CanonStatus("picked_up", "Recogida", 30, False, False, False, "pipeline"),
        CanonStatus("in_transit", "En tránsito", 40, False, False, False, "pipeline"),
        CanonStatus("out_for_delivery", "En reparto", 50, False, False, False, "pipeline"),
        # The parcel reached the carrier's office and the customer has not come
        # for it. Neither delivered nor returned, and the most recoverable
        # failure mode there is - 17% of a real Ecuadorian export sat here.
        CanonStatus("in_office", "En oficina", 52, False, False, False, "pipeline"),
        CanonStatus("delivery_issue", "Novedad", 55, False, False, False, "pipeline"),
        CanonStatus("delivered", "Entregada", 60, True, True, False, "delivered"),
        CanonStatus("returning", "En devolución", 70, False, False, False, "returned"),
        CanonStatus("returned", "Devuelta", 80, True, False, True, "returned"),
        CanonStatus("cancelled", "Cancelada", 90, True, False, False, "dead"),
        CanonStatus("lost", "Extraviada", 95, True, False, False, "dead"),
    )
}

# Mirror of core.status_alias (migration 002). Keys are already normalized.
STATUS_ALIASES: dict[str, str] = {
    "generada": "created",
    "guia generada": "created",
    "pendiente": "created",
    "preparando": "confirmed",
    "confirmado": "confirmed",
    "confirmada": "confirmed",
    "recogido": "picked_up",
    "recogida": "picked_up",
    "en transito": "in_transit",
    "en ruta": "in_transit",
    "en reparto": "out_for_delivery",
    "en distribucion": "out_for_delivery",
    "novedad": "delivery_issue",
    "con novedad": "delivery_issue",
    "entregado": "delivered",
    "entregada": "delivered",
    "en devolucion": "returning",
    "devolucion": "returning",
    "devolucion en transito": "returning",
    "devuelto": "returned",
    "devuelta": "returned",
    "cancelado": "cancelled",
    "cancelada": "cancelled",
    "anulada": "cancelled",
    "extraviado": "lost",
    "perdida": "lost",
    # --- Effi vocabulary, verbatim from real exports (mirrors migration 008) ---
    "entregada a destino": "delivered",
    "disponible para retiro en oficina": "in_office",
    "devolucion a origen": "returning",
    "cancelada por transportadora": "cancelled",
    "generada effi": "created",
    "ingresando en agencia": "in_transit",
    "ingresando operativo": "in_transit",
    "en ruta a concesion": "in_transit",
    "en distribucion a cliente": "out_for_delivery",
    "reportado entregado en agencia": "in_office",
    "reportado entregado en app": "delivered",
    "entregado en agencia": "in_office",
    "retirado en oficina": "delivered",
    "devuelto a origen": "returned",
    "devolucion entregada": "returned",
    "anulada por transportadora": "cancelled",
    "siniestro": "lost",
    "extravio": "lost",
}

DEFAULT_STATUS = "created"


def resolve_status(raw_value: object) -> tuple[str, bool]:
    """Map a raw status string to a canonical code.

    Returns (code, recognized). Unknown text falls back to 'created' and is
    flagged so the batch report can tell the user which words it did not know -
    silently guessing a status would corrupt every delivery-rate KPI downstream.
    """
    key = normalize_text(raw_value)
    if not key:
        return DEFAULT_STATUS, False
    code = STATUS_ALIASES.get(key)
    if code:
        return code, True
    # Tolerate decorated values like "ENTREGADO - OK" or "novedad (cliente)".
    for alias, mapped in STATUS_ALIASES.items():
        if key.startswith(alias + " ") or key.startswith(alias + "-") or key.startswith(alias + "("):
            return mapped, True
    return DEFAULT_STATUS, False


# Mirror of core.movement_type (migration 002).
MOVEMENT_TYPE_ALIASES: dict[str, str] = {
    "recaudo": "cod_collected",
    "recaudo contraentrega": "cod_collected",
    "cobro": "cod_collected",
    "valor recaudado": "cod_collected",
    "abono": "cod_collected",
    "flete": "freight_out",
    "flete envio": "freight_out",
    "costo envio": "freight_out",
    "flete devolucion": "freight_return",
    "costo devolucion": "freight_return",
    "costo producto": "product_cost",
    "costo proveedor": "product_cost",
    "comision": "platform_fee",
    "comision plataforma": "platform_fee",
    "seguro": "insurance",
    "ajuste": "adjustment_in",
    "ajuste positivo": "adjustment_in",
    "ajuste negativo": "adjustment_out",
    "descuento": "adjustment_out",
}

MOVEMENT_TYPE_SIGN: dict[str, int] = {
    "cod_collected": 1,
    "freight_out": -1,
    "freight_return": -1,
    "product_cost": -1,
    "platform_fee": -1,
    "insurance": -1,
    "adjustment_in": 1,
    "adjustment_out": -1,
}


def resolve_movement_type(raw_value: object) -> tuple[str | None, bool]:
    key = normalize_text(raw_value)
    if not key:
        return None, False
    code = MOVEMENT_TYPE_ALIASES.get(key)
    if code:
        return code, True
    if key in MOVEMENT_TYPE_SIGN:      # already canonical
        return key, True
    return None, False


# -----------------------------------------------------------------------------
# Column aliases. Keys are canonical field names on ShipmentInput / MovementInput.
# Values are normalized header spellings (accent-free, lowercase).
# -----------------------------------------------------------------------------
SHIPMENT_COLUMNS: dict[str, tuple[str, ...]] = {
    "tracking_number": (
        "guia", "numero guia", "numero de guia", "no guia", "nro guia",
        "tracking", "tracking number", "codigo guia", "guia numero",
    ),
    "external_order_id": (
        "orden", "id orden", "pedido", "numero pedido", "no pedido",
        "order id", "referencia", "id externo",
    ),
    "created_date": (
        "fecha", "fecha creacion", "fecha de creacion", "fecha guia",
        "fecha generacion", "creado", "fecha de generacion",
    ),
    "dispatched_at": ("fecha despacho", "fecha de despacho", "despachado", "fecha envio"),
    "delivered_at": ("fecha entrega", "fecha de entrega", "entregado el", "fecha entregado"),
    "returned_at": ("fecha devolucion", "fecha de devolucion", "devuelto el"),
    "last_status_at": ("ultima actualizacion", "fecha estado", "fecha ultimo estado"),
    "status_raw": ("estado", "estatus", "status", "estado guia", "estado actual"),
    "carrier_name": ("transportadora", "transportador", "carrier", "operador logistico", "empresa envio"),
    "city_name": ("ciudad", "ciudad destino", "municipio", "ciudad de entrega"),
    "geo_level1": ("departamento", "depto", "estado destino", "provincia", "region"),
    "product_name": ("producto", "nombre producto", "articulo", "item", "descripcion producto"),
    "supplier_name": ("proveedor", "supplier", "bodega"),
    "store_name": ("tienda", "store", "cuenta", "marca"),
    "quantity": ("cantidad", "unidades", "qty", "cant"),
    "customer_identifier": (
        "telefono", "celular", "telefono cliente", "documento", "cedula",
        "identificacion", "nit", "movil",
    ),
    "declared_value": (
        "valor", "valor recaudo", "valor a recaudar", "valor declarado",
        "total", "total pedido", "monto", "precio venta", "valor total",
    ),
    "cod_collected": ("recaudado", "valor recaudado", "monto recaudado", "cobrado"),
    "freight_cost": ("flete", "costo flete", "valor flete", "envio", "costo envio"),
    "return_freight_cost": ("flete devolucion", "costo devolucion", "valor devolucion"),
    "product_cost": ("costo producto", "costo", "costo proveedor", "cogs"),
    "platform_fee": ("comision", "comision plataforma", "fee"),
    "currency_code": ("moneda", "divisa", "currency"),
}

MOVEMENT_COLUMNS: dict[str, tuple[str, ...]] = {
    "tracking_number_raw": (
        "guia", "numero guia", "numero de guia", "tracking", "referencia guia",
    ),
    "movement_type_raw": ("tipo", "concepto", "tipo movimiento", "descripcion tipo", "rubro"),
    "movement_date": ("fecha", "fecha movimiento", "fecha transaccion", "fecha pago"),
    "amount": ("valor", "monto", "importe", "total", "valor movimiento"),
    "currency_code": ("moneda", "divisa", "currency"),
    "external_ref": ("referencia", "id movimiento", "consecutivo", "id transaccion"),
    "description": ("descripcion", "detalle", "observacion", "nota"),
}

COLUMNS_BY_KIND: dict[BatchKind, dict[str, tuple[str, ...]]] = {
    BatchKind.SHIPMENTS: SHIPMENT_COLUMNS,
    BatchKind.MOVEMENTS: MOVEMENT_COLUMNS,
}

# Without these a row cannot be identified at all, and the file is rejected.
REQUIRED_COLUMNS: dict[BatchKind, tuple[str, ...]] = {
    BatchKind.SHIPMENTS: ("tracking_number",),
    BatchKind.MOVEMENTS: ("amount",),
}


def build_header_map(headers: list[str], kind: BatchKind) -> tuple[dict[int, str], list[str]]:
    """Map column positions to canonical field names.

    Returns (position -> field, unmapped headers). Unmapped headers are reported
    back to the user rather than dropped in silence: a column named `Valor Neto`
    that Data Effi ignored is exactly the kind of thing that makes a dashboard lie.
    """
    aliases = COLUMNS_BY_KIND[kind]
    lookup: dict[str, str] = {}
    for field_name, spellings in aliases.items():
        for spelling in spellings:
            lookup.setdefault(spelling, field_name)

    mapped: dict[int, str] = {}
    unmapped: list[str] = []
    taken: set[str] = set()

    for position, header in enumerate(headers):
        key = normalize_text(header)
        if not key:
            continue
        field_name = lookup.get(key)
        if field_name and field_name not in taken:
            mapped[position] = field_name
            taken.add(field_name)
        else:
            unmapped.append(str(header).strip())

    return mapped, unmapped
