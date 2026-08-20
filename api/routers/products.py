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

from api.db import fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, require_role
from api.errors import Conflict, NotFound
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

AnalystDep = Annotated[object, Depends(require_role("analyst"))]

COST_HISTORY_LIMIT = 20


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

    supplier_id = _resolve_supplier(conn, user.tenant_id, payload.supplier_name)

    # A cost somebody typed is a cost somebody confirmed. Setting reviewed_by in
    # the same statement is also what makes the history trigger record it as
    # 'manual' instead of 'import'.
    reviewed_at = datetime.now(UTC) if payload.unit_cost is not None else None
    reviewed_by = user.id if payload.unit_cost is not None else None

    created = fetch_one(
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
    existing = fetch_one(
        conn,
        "SELECT id FROM core.product WHERE id = %s AND tenant_id = %s",
        (product_id, user.tenant_id),
    )
    if existing is None:
        raise NotFound("Ese producto no existe en tu workspace")

    if payload.name:
        clash = fetch_one(
            conn,
            "SELECT id FROM core.product "
            "WHERE tenant_id = %s AND name_norm = core.normalize_text(%s) AND id <> %s",
            (user.tenant_id, payload.name, product_id),
        )
        if clash is not None:
            raise Conflict(f"Ya existe otro producto llamado '{payload.name}'")

    supplier_id = _resolve_supplier(conn, user.tenant_id, payload.supplier_name)
    touched_cost = payload.unit_cost is not None

    fetch_one(
        conn,
        """
        UPDATE core.product SET
            name              = COALESCE(%(name)s, name),
            name_norm         = COALESCE(core.normalize_text(%(name)s), name_norm),
            sku               = COALESCE(%(sku)s, sku),
            category          = COALESCE(%(category)s, category),
            supplier_id       = COALESCE(%(supplier_id)s, supplier_id),
            unit_cost         = COALESCE(%(unit_cost)s, unit_cost),
            list_price        = COALESCE(%(list_price)s, list_price),
            target_margin_pct = COALESCE(%(target_margin_pct)s, target_margin_pct),
            weight_kg         = COALESCE(%(weight_kg)s, weight_kg),
            currency_code     = COALESCE(%(currency_code)s, currency_code),
            notes             = COALESCE(%(notes)s, notes),
            -- Typing a cost is a person vouching for it. That is exactly what
            -- mart.v_product_catalogue reports as the difference between a
            -- product a report invented and one the operator confirmed.
            reviewed_at       = CASE WHEN %(touched_cost)s::boolean
                                     THEN %(reviewed_at)s::timestamptz ELSE reviewed_at END,
            reviewed_by       = CASE WHEN %(touched_cost)s::boolean
                                     THEN %(reviewed_by)s::uuid ELSE reviewed_by END
        WHERE id = %(product_id)s AND tenant_id = %(tenant_id)s
        RETURNING id
        """,
        {
            "name": payload.name,
            "sku": payload.sku,
            "category": payload.category,
            "supplier_id": supplier_id,
            "unit_cost": payload.unit_cost,
            "list_price": payload.list_price,
            "target_margin_pct": payload.target_margin_pct,
            "weight_kg": payload.weight_kg,
            "currency_code": payload.currency_code,
            "notes": payload.notes,
            "touched_cost": touched_cost,
            "reviewed_at": datetime.now(UTC) if touched_cost else None,
            "reviewed_by": user.id if touched_cost else None,
            "product_id": product_id,
            "tenant_id": user.tenant_id,
        },
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

    created = fetch_one(
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
