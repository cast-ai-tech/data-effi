"""Product catalogue: the commercial truth the reports cannot know.

A product gets into `core.product` two ways. Ingestion sees a name in a report
and creates the row - that product has `reviewed_at IS NULL` and a cost that is
whatever one guide happened to charge. Or a person adds it here and fills in the
real cost, the list price, the weight.

Nothing in this file may assume it created a product. Most rows were written by
a file, and the whole point of `mart.v_product_catalogue` is to say which ones
still need a human.

Deletion is always soft: shipments reference products, and a delivered guide
whose product vanished is a report that no longer adds up.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from api.db import fetch_all, fetch_one, fetch_required
from api.deps import CurrentUser, CurrentUserDep, DbDep, require_role, tenant_of
from api.errors import ApiError, Conflict, NotFound
from api.schemas import (
    CatalogueStatus,
    ProductCatalogueRow,
    ProductCostHistoryRow,
    ProductCreateRequest,
    ProductDetail,
    ProductUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])

AnalystDep = Annotated[CurrentUser, Depends(require_role("analyst"))]

COST_HISTORY_LIMIT = 20

# PATCH field -> column. This dict is the ONLY thing that can put a column name
# into the UPDATE statement; it is written here, never taken from the request.
# `supplier_name` is absent on purpose: it resolves to supplier_id separately.
_UPDATABLE_COLUMNS: dict[str, str] = {
    "name": "name",
    "sku": "sku",
    "category": "category",
    "unit_cost": "unit_cost",
    "list_price": "list_price",
    "target_margin_pct": "target_margin_pct",
    "weight_kg": "weight_kg",
    "currency_code": "currency_code",
    "notes": "notes",
    "is_active": "is_active",
}

# Columns the schema declares NOT NULL. Sending them as an explicit null is a
# request error, not a database error.
_NOT_NULL_FIELDS = ("name", "is_active")


@router.get("", response_model=list[ProductCatalogueRow], summary="Catálogo de productos")
def list_products(
    conn: DbDep,
    catalogue_status: Annotated[
        CatalogueStatus | None,
        Query(alias="status", description="sin_costo | sin_revisar | costo_desactualizado | ok"),
    ] = None,
    active: Annotated[bool | None, Query(description="Solo activos o solo inactivos")] = None,
    search: Annotated[
        str | None, Query(min_length=1, max_length=120, description="Busca en nombre o SKU")
    ] = None,
) -> list[ProductCatalogueRow]:
    """The catalogue as the view reports it, including what the guides observed."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if catalogue_status:
        clauses.append("catalogue_status = %(status)s")
        params["status"] = catalogue_status
    if active is not None:
        clauses.append("is_active = %(active)s")
        params["active"] = active
    if search:
        clauses.append("(product_name ILIKE %(search)s OR sku ILIKE %(search)s)")
        params["search"] = f"%{search}%"

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_product_catalogue {where} "
        "ORDER BY shipments DESC, product_name",
        params,
    )
    return [ProductCatalogueRow(**row) for row in rows]


@router.get(
    "/{product_id}",
    response_model=ProductDetail,
    summary="Un producto y su historial de costos",
)
def get_product(product_id: UUID, conn: DbDep, user: CurrentUserDep) -> ProductDetail:
    product = _catalogue_row(conn, product_id, missing="Ese producto no existe en tu workspace")

    history = fetch_all(
        conn,
        """
        SELECT id, unit_cost, currency_code, source, changed_by, changed_at
        FROM core.product_cost_history
        WHERE product_id = %s AND tenant_id = %s
        ORDER BY changed_at DESC, id DESC
        LIMIT %s
        """,
        (product_id, user.tenant_id, COST_HISTORY_LIMIT),
    )
    return ProductDetail(
        product=product,
        cost_history=[ProductCostHistoryRow(**row) for row in history],
    )


@router.post(
    "",
    response_model=ProductCatalogueRow,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un producto a mano",
)
def create_product(
    payload: ProductCreateRequest, conn: DbDep, user: AnalystDep
) -> ProductCatalogueRow:
    existing = fetch_one(
        conn,
        "SELECT id FROM core.product WHERE tenant_id = %s AND name_norm = core.normalize_text(%s)",
        (user.tenant_id, payload.name),
    )
    if existing is not None:
        raise Conflict(
            f"Ya existe un producto llamado '{payload.name}'. "
            f"Puede haberlo creado una carga de archivos: edítalo en vez de crearlo de nuevo."
        )

    supplier_id = _resolve_supplier(conn, tenant_of(user), payload.supplier_name)

    # A cost somebody typed is a cost somebody confirmed. Setting reviewed_by in
    # the same statement is also what makes the history trigger record it as
    # 'manual' instead of 'import'.
    reviewed_at = datetime.now(UTC) if payload.unit_cost is not None else None
    reviewed_by = user.id if payload.unit_cost is not None else None

    created = fetch_required(
        conn,
        """
        INSERT INTO core.product
            (tenant_id, name, name_norm, sku, category, supplier_id, unit_cost,
             list_price, target_margin_pct, weight_kg, currency_code, notes,
             reviewed_at, reviewed_by)
        VALUES (%s, %s, core.normalize_text(%s), %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s)
        RETURNING id
        """,
        (
            user.tenant_id, payload.name, payload.name, payload.sku, payload.category,
            supplier_id, payload.unit_cost, payload.list_price, payload.target_margin_pct,
            payload.weight_kg, payload.currency_code, payload.notes,
            reviewed_at, reviewed_by,
        ),
    )

    logger.info("product created tenant=%s product=%s", user.tenant_id, created["id"])
    return _catalogue_row(conn, created["id"], missing="No se pudo leer el producto creado")


@router.patch(
    "/{product_id}", response_model=ProductCatalogueRow, summary="Editar un producto"
)
def update_product(
    product_id: UUID, payload: ProductUpdateRequest, conn: DbDep, user: AnalystDep
) -> ProductCatalogueRow:
    """Update only the fields the caller actually sent.

    PATCH has to tell three things apart, and a fixed list of COALESCE cannot:

        field absent          -> leave it exactly as it is
        field sent with value -> set it
        field sent as null    -> CLEAR it

    COALESCE collapses the first and third into one, so clearing a field does
    nothing and the operator sees the old value reappear on its own. Pydantic
    already knows which keys arrived in the body - `model_fields_set` - so the
    SET clause is built from that and a column whitelist below.
    """
    existing = fetch_one(
        conn,
        "SELECT id FROM core.product WHERE id = %s AND tenant_id = %s",
        (product_id, user.tenant_id),
    )
    if existing is None:
        raise NotFound("Ese producto no existe en tu workspace")

    sent = payload.model_fields_set

    # NOT NULL columns. An explicit null here would hit a database constraint,
    # so it is refused with the same envelope as any other invalid field rather
    # than surfacing as a 500.
    for field in _NOT_NULL_FIELDS:
        if field in sent and getattr(payload, field) is None:
            raise ApiError(
                "validation_error",
                f"'{field}' no puede quedar vacío.",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"fields": [{"field": field, "reason": "no acepta null"}]},
            )

    if payload.name:
        clash = fetch_one(
            conn,
            "SELECT id FROM core.product "
            "WHERE tenant_id = %s AND name_norm = core.normalize_text(%s) AND id <> %s",
            (user.tenant_id, payload.name, product_id),
        )
        if clash is not None:
            raise Conflict(f"Ya existe otro producto llamado '{payload.name}'")

    # Only columns named in _UPDATABLE_COLUMNS can reach the SQL text, and every
    # value is bound as a parameter.
    assignments: list[str] = []
    params: dict[str, Any] = {"product_id": product_id, "tenant_id": user.tenant_id}

    for field, column in _UPDATABLE_COLUMNS.items():
        if field in sent:
            assignments.append(f"{column} = %({field})s")
            params[field] = getattr(payload, field)

    if "name" in sent:
        assignments.append("name_norm = core.normalize_text(%(name)s)")

    if "supplier_name" in sent:
        # Null and empty both mean "no supplier" - _resolve_supplier returns
        # None for either - and None clears the column because it only appears
        # in the SET when the caller sent the field at all.
        assignments.append("supplier_id = %(supplier_id)s")
        params["supplier_id"] = _resolve_supplier(conn, tenant_of(user), payload.supplier_name)

    if "unit_cost" in sent:
        # Typing a cost is a person vouching for it; clearing one withdraws
        # that. Leaving reviewed_at behind would keep v_product_catalogue and
        # v_dropshipping_margin reporting the product as confirmed by the
        # operator when it no longer has a cost at all.
        confirmed = payload.unit_cost is not None
        assignments.append("reviewed_at = %(reviewed_at)s")
        assignments.append("reviewed_by = %(reviewed_by)s")
        params["reviewed_at"] = datetime.now(UTC) if confirmed else None
        params["reviewed_by"] = user.id if confirmed else None

    # An empty body is a no-op, not an UPDATE with nothing to set.
    if not assignments:
        return _catalogue_row(conn, product_id, missing="No se pudo leer el producto")

    fetch_one(
        conn,
        f"UPDATE core.product SET {', '.join(assignments)} "
        "WHERE id = %(product_id)s AND tenant_id = %(tenant_id)s RETURNING id",
        params,
    )
    return _catalogue_row(conn, product_id, missing="No se pudo leer el producto actualizado")


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Desactivar un producto",
)
def delete_product(product_id: UUID, conn: DbDep, user: AnalystDep) -> None:
    """Soft delete, always.

    Guides point at products. Deleting the row would leave every shipment that
    ever carried it without a name, so the product is deactivated instead: it
    disappears from the pickers and every historical number stays intact.
    """
    row = fetch_one(
        conn,
        "UPDATE core.product SET is_active = false WHERE id = %s AND tenant_id = %s RETURNING id",
        (product_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Ese producto no existe en tu workspace")
    logger.info("product deactivated: %s", product_id)


# =============================================================================
# Helpers
# =============================================================================


def _resolve_supplier(conn, tenant_id: UUID, name: str | None) -> UUID | None:
    """Get-or-create a supplier, exactly as pipeline/store_pg.py does it.

    Same normalisation and same conflict target on purpose: a supplier typed
    here and the same supplier read out of a report have to be one row, or the
    margin views split one company into two.

    INSERT ... ON CONFLICT DO NOTHING followed by a SELECT is what makes this
    safe when a load is running at the same time - if the ingestion worker
    created it first, we read its id instead of raising.
    """
    if name is None or not name.strip():
        return None
    cleaned = name.strip()

    created = fetch_required(
        conn,
        """
        INSERT INTO core.supplier (tenant_id, name, name_norm)
        VALUES (%s, %s, core.normalize_text(%s))
        ON CONFLICT (tenant_id, name_norm) DO NOTHING
        RETURNING id
        """,
        (tenant_id, cleaned, cleaned),
    )
    if created is not None:
        return created["id"]

    existing = fetch_one(
        conn,
        "SELECT id FROM core.supplier WHERE tenant_id = %s AND name_norm = core.normalize_text(%s)",
        (tenant_id, cleaned),
    )
    if existing is None:      # pragma: no cover - only if the tenant GUC is unset
        raise Conflict(f"No se pudo resolver el proveedor '{cleaned}'")
    return existing["id"]


def _catalogue_row(conn, product_id: UUID, *, missing: str) -> ProductCatalogueRow:
    row = fetch_one(
        conn, "SELECT * FROM mart.v_product_catalogue WHERE product_id = %s", (product_id,)
    )
    if row is None:
        raise NotFound(missing)
    return ProductCatalogueRow(**row)
