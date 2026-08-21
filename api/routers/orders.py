"""Orders: one guide, as a person would read it.

Every other endpoint in this API answers a question about an aggregate. This one
answers a question about a parcel - "where is the order for the lady who called
this morning" - which is the question an operator actually has at 9am, and the
one the dashboard could not answer at all before migration 015.

That makes it the only router that hands out a real person's name and phone
number, and everything unusual in this file follows from that:

  * the response is paginated with a hard ceiling, because an endpoint that can
    return every row is an export of the customer list with extra steps;
  * contact fields are decrypted here, in Python, gated by role - `api/pii.py`
    holds that decision so it is made once;
  * every response that carried contact data leaves a row in `raw.pii_access`.

The SQL stays what it is everywhere else: a filter over a mart view. Nothing in
this file computes a number.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.db import fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, client_ip_inet
from api.errors import NotFound
from api.pii import (
    ORDER_CONTACT,
    ORDER_DETAIL_CONTACT,
    carried_contact,
    contact_visible,
    log_access,
    reveal,
)
from api.schemas import (
    CustomerGrade,
    OrderDetail,
    OrderDetailRow,
    OrderRow,
    OrderSort,
    OrdersPage,
    OrderTimelineEvent,
)

router = APIRouter(prefix="/orders", tags=["orders"])

ClientIpDep = Annotated[str | None, Depends(client_ip_inet)]

# The hard ceiling. 200 rows is a table a person scrolls; 50.000 is a data
# dump, and the difference between the two is one query parameter nobody
# validated. FastAPI refuses anything above this before the router runs.
MAX_PAGE_SIZE = 200

# ORDER BY is chosen from this dict and never built from the request. Each one
# ends in a unique-per-row tiebreak: without it PostgreSQL is free to return the
# same row on page 1 and page 2 when the sort key ties, which is exactly what
# happens with `days_open` over a day's worth of guides.
_ORDER_SORTS: dict[str, str] = {
    "recent": "o.created_date DESC, o.tracking_number DESC",
    "contribution": "o.contribution DESC NULLS LAST, o.tracking_number DESC",
    "days_open": "o.days_open DESC NULLS LAST, o.tracking_number DESC",
}

# Columns every order row needs. `customer_ref` is derived in SQL from the same
# hash `mart.v_customer_metrics` uses, so the label on a row in the orders table
# and the label on the customers page are the same string.
_ORDER_COLUMNS = """
    o.shipment_id,
    o.tracking_number,
    o.carrier_tracking_number,
    o.created_date,
    o.delivered_at,
    o.status_code,
    o.status_label,
    o.status_bucket,
    o.is_terminal,
    o.customer_hash,
    CASE WHEN o.customer_hash IS NOT NULL
         THEN '#' || upper(left(o.customer_hash, 6)) END        AS customer_ref,
    o.customer_name_enc,
    o.customer_phone_enc,
    o.city_name,
    o.province_name,
    o.product_name,
    o.quantity,
    o.carrier_name,
    o.revenue_amount,
    o.freight_amount,
    o.cogs_amount,
    o.fee_amount,
    o.contribution,
    o.currency_code,
    o.days_open,
    o.movement_count
"""


@router.get("", response_model=OrdersPage, summary="Tabla de órdenes")
def list_orders(
    conn: DbDep,
    user: CurrentUserDep,
    ip: ClientIpDep,
    country: Annotated[str, Query(min_length=2, max_length=2, description="Código ISO del país")],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    search: Annotated[
        str | None,
        Query(min_length=1, max_length=120, description="Número de guía, propio o del courier"),
    ] = None,
    status: Annotated[
        str | None, Query(min_length=1, max_length=40, description="Código canónico de estado")
    ] = None,
    grade: Annotated[CustomerGrade | None, Query(description="Calidad del cliente")] = None,
    from_date: Annotated[date | None, Query(description="Desde (fecha de creación de la guía)")] = None,
    to_date: Annotated[date | None, Query(description="Hasta (fecha de creación de la guía)")] = None,
    sort: OrderSort = "recent",
    only_open: Annotated[bool, Query(description="Solo guías que aún no cierran")] = False,
) -> OrdersPage:
    """One page of guides, filtered the way the operator is looking at them."""
    where, params = _order_filters(
        country=country,
        search=search,
        status=status,
        grade=grade,
        from_date=from_date,
        to_date=to_date,
        only_open=only_open,
    )

    # COUNT before LIMIT: the frontend needs to know how many pages exist, and
    # `len(rows)` only ever reports the size of the page it already has.
    counted = fetch_one(conn, f"SELECT count(*) AS total FROM mart.v_orders o {where}", params)
    total = int(counted["total"]) if counted else 0

    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    raw_rows = fetch_all(
        conn,
        f"SELECT {_ORDER_COLUMNS} FROM mart.v_orders o {where} "
        f"ORDER BY {_ORDER_SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )

    visible = contact_visible(user)
    rows, disclosed = _rows_with_contact(raw_rows, ORDER_CONTACT, OrderRow, visible=visible)
    log_access(conn, user, endpoint="GET /orders", record_count=disclosed, ip=ip)

    return OrdersPage(
        rows=rows, page=page, page_size=page_size, total=total, pii_visible=visible
    )


@router.get(
    "/{shipment_id}",
    response_model=OrderDetail,
    summary="Una guía con su trazabilidad",
)
def get_order(
    shipment_id: UUID, conn: DbDep, user: CurrentUserDep, ip: ClientIpDep
) -> OrderDetail:
    """The detail card: everything about one guide, plus how it got there.

    The timeline unions status and money events, which is why it is ordered by
    date and then by `event_order`: a delivery and the payout it triggered often
    share a date, and reading the payout first makes the card look like the
    money arrived before the parcel did.
    """
    row = fetch_one(
        conn,
        f"SELECT {_ORDER_COLUMNS}, o.customer_address_enc, o.customer_document_enc "
        "FROM mart.v_orders o WHERE o.shipment_id = %s",
        (shipment_id,),
    )
    if row is None:
        raise NotFound("Esa guía no existe en tu workspace")

    visible = contact_visible(user)
    contact = reveal(row, ORDER_DETAIL_CONTACT, visible=visible)
    order = OrderDetailRow(**{**row, **contact})

    events = fetch_all(
        conn,
        """
        SELECT event_kind, event_label, amount, currency_code, event_date, reference
        FROM mart.v_order_timeline
        WHERE shipment_id = %s
        ORDER BY event_date, event_order
        """,
        (shipment_id,),
    )

    log_access(
        conn,
        user,
        endpoint="GET /orders/{shipment_id}",
        record_count=1 if carried_contact(contact) else 0,
        ip=ip,
    )
    return OrderDetail(
        order=order,
        timeline=[OrderTimelineEvent(**event) for event in events],
        pii_visible=visible,
    )


# =============================================================================
# Shared by this router and the customers one
# =============================================================================


def _order_filters(
    *,
    country: str,
    search: str | None = None,
    status: str | None = None,
    grade: CustomerGrade | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    only_open: bool = False,
    customer_hash: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the WHERE clause. Every fragment below is a literal in this file.

    Nothing from the request is ever concatenated into the SQL text: the values
    are bound, and the only request-driven choice is which fixed fragment gets
    appended.
    """
    clauses = ["o.country_code = %(country)s"]
    params: dict[str, Any] = {"country": country.upper()}

    if search:
        # Both numbers, because the operator is reading whichever one the
        # customer quoted at them - the platform's guide or the courier's.
        clauses.append(
            "(o.tracking_number ILIKE %(search)s OR o.carrier_tracking_number ILIKE %(search)s)"
        )
        params["search"] = f"%{search}%"
    if status:
        clauses.append("o.status_code = %(status)s")
        params["status"] = status
    if grade:
        # The grade belongs to the customer, not the guide, so it is looked up
        # where it is computed. EXISTS rather than a JOIN: a customer with two
        # rows in the metrics view must not duplicate their orders.
        clauses.append(
            "EXISTS (SELECT 1 FROM mart.v_customer_metrics cm "
            "        WHERE cm.customer_hash = o.customer_hash "
            "          AND cm.country_code = o.country_code "
            "          AND cm.customer_grade = %(grade)s)"
        )
        params["grade"] = grade
    if from_date:
        clauses.append("o.created_date >= %(from_date)s")
        params["from_date"] = from_date
    if to_date:
        clauses.append("o.created_date <= %(to_date)s")
        params["to_date"] = to_date
    if only_open:
        clauses.append("NOT o.is_terminal")
    if customer_hash:
        clauses.append("o.customer_hash = %(customer_hash)s")
        params["customer_hash"] = customer_hash

    return f"WHERE {' AND '.join(clauses)}", params


def _rows_with_contact(
    raw_rows: list[dict[str, Any]],
    mapping: dict[str, str],
    model: type,
    *,
    visible: bool,
) -> tuple[list[Any], int]:
    """Decrypt what the caller is allowed to see, and count what was disclosed.

    The count is of records that actually carried something, not of rows
    returned: a page of 50 guides where 3 have a phone number on file disclosed
    three people's data, and logging 50 would make the trail useless for
    judging how bad an incident was.
    """
    rows: list[Any] = []
    disclosed = 0

    for raw in raw_rows:
        contact = reveal(raw, mapping, visible=visible)
        if carried_contact(contact):
            disclosed += 1
        rows.append(model(**{**raw, **contact}))

    return rows, disclosed
