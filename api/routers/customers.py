"""Customers: the same people, seen once instead of once per guide.

A COD operation does not have a customer table. It has guides, and the same
person appears on five of them under three spellings of their name. What makes
them one customer is `customer_hash` - a salted SHA-256 of the phone number,
deterministic, so the same number is the same person across every upload and
every country. `mart.v_customer_metrics` does that grouping; this router reads
it.

The privacy rules are the ones in `orders.py`, for the same reason and through
the same module: this endpoint is a list of real people, so a viewer receives no
contact data at all, the page size has a hard ceiling, and every response that
carried a name or a phone leaves a row in `raw.pii_access`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from api.db import fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, client_ip_inet
from api.errors import NotFound
from api.pii import (
    CUSTOMER_CONTACT,
    CUSTOMER_DETAIL_CONTACT,
    ORDER_CONTACT,
    carried_contact,
    contact_visible,
    log_access,
    reveal,
)
from api.routers.kpis import (
    BY_CREATION,
    DateFieldQuery,
    OptionalDate,
    _check_field,
    _check_range,
    _excluded_no_date,
)
from api.routers.orders import (
    _ORDER_COLUMNS,
    MAX_PAGE_SIZE,
    _order_filters,
    _rows_with_contact,
)
from api.schemas import (
    CustomerDetail,
    CustomerDetailRow,
    CustomerGrade,
    CustomerRow,
    CustomerSort,
    CustomersPage,
    OrderRow,
)

router = APIRouter(prefix="/customers", tags=["customers"])

ClientIpDep = Annotated[str | None, Depends(client_ip_inet)]

# The hash is a char(64) of hex. Validating its shape in the route means a
# malformed id is a 422 before it reaches SQL, and means this path can never be
# used to probe the database with something that is not a hash.
CustomerHashPath = Annotated[
    str, Path(pattern="^[0-9a-fA-F]{64}$", description="Hash determinista del cliente")
]

# How many of a customer's guides the detail card carries. A COD customer has a
# handful; the ceiling exists so that one pathological row - a shared corporate
# phone number, say - cannot turn a detail card into an unbounded export.
CUSTOMER_ORDERS_LIMIT = 200

_CUSTOMER_SORTS: dict[str, str] = {
    "orders": "c.orders DESC, c.customer_hash",
    "contribution": "c.contribution DESC NULLS LAST, c.customer_hash",
    "revenue": "c.revenue DESC NULLS LAST, c.customer_hash",
    "recent": "c.last_order_date DESC, c.customer_hash",
}

_CUSTOMER_COLUMNS = """
    c.customer_hash,
    c.customer_ref,
    c.last_name_enc,
    c.last_phone_enc,
    c.orders,
    c.delivered,
    c.returned,
    c.open_orders,
    c.revenue,
    c.contribution,
    c.contribution_per_order,
    c.delivery_rate_pct,
    c.customer_grade,
    c.main_city,
    c.first_order_date,
    c.last_order_date,
    c.days_since_last_order,
    c.distinct_products,
    c.currency_code
"""


@router.get("", response_model=CustomersPage, summary="Tabla de clientes")
def list_customers(
    conn: DbDep,
    user: CurrentUserDep,
    ip: ClientIpDep,
    country: Annotated[str, Query(min_length=2, max_length=2, description="Código ISO del país")],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    grade: Annotated[CustomerGrade | None, Query(description="Calidad del cliente")] = None,
    min_orders: Annotated[int, Query(ge=0, description="Mínimo de pedidos")] = 0,
    sort: CustomerSort = "orders",
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> CustomersPage:
    """The customers of a period, not the customers of all time.

    With a range these rows describe behaviour INSIDE it: `orders` counts orders
    in the window, `first_order_date` is the first one in the window, and
    `customer_grade` grades what happened there. That is what an operator asking
    "¿quiénes me compraron en enero?" means, and `mart.f_customer_metrics`
    regroups from the guides rather than clipping a precomputed total - which is
    the only way `main_city`, a mode(), stays correct.

    `date_field` works here as it does on the KPIs. With `entrega` the page
    lists the customers whose orders ARRIVED in the window, and
    `excluded_no_date` reports how many guides could not be considered because
    they have no delivery date yet.
    """
    basis = _check_field(date_field)
    _check_range(date_from, date_to)
    where, params = _customer_filters(country=country, grade=grade, min_orders=min_orders)
    params["date_from"] = date_from
    params["date_to"] = date_to
    params["date_field"] = basis
    source = "mart.f_customer_metrics(%(date_from)s, %(date_to)s, %(date_field)s) c"

    counted = fetch_one(conn, f"SELECT count(*) AS total FROM {source} {where}", params)
    total = int(counted["total"]) if counted else 0

    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    raw_rows = fetch_all(
        conn,
        f"SELECT {_CUSTOMER_COLUMNS} FROM {source} {where} "
        f"ORDER BY {_CUSTOMER_SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )

    visible = contact_visible(user)
    rows, disclosed = _rows_with_contact(
        raw_rows, CUSTOMER_CONTACT, CustomerRow, visible=visible
    )
    log_access(conn, user, endpoint="GET /customers", record_count=disclosed, ip=ip)

    return CustomersPage(
        rows=rows,
        page=page,
        page_size=page_size,
        total=total,
        pii_visible=visible,
        date_basis=basis,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(conn, country, basis, date_from, date_to),
    )


@router.get(
    "/{customer_hash}",
    response_model=CustomerDetail,
    summary="Un cliente y sus pedidos",
)
def get_customer(
    customer_hash: CustomerHashPath,
    conn: DbDep,
    user: CurrentUserDep,
    ip: ClientIpDep,
    country: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="Sólo si el cliente compra en varios países"),
    ] = None,
) -> CustomerDetail:
    """One customer's history, with the contact data a parcel needs.

    NO DATE RANGE HERE, unlike the table. A detail card is opened to answer
    "who is this person", and the answer is their whole record: when they first
    bought, how many times they came back, whether they take delivery. Clipping
    that to the week the dashboard happens to be showing would grade a
    three-year customer as 'nuevo'. The table filters because it answers a
    different question - who bought IN this period.

    ONE ROW PER COUNTRY, and that is the whole reason `country` exists here.
    The hash is global - the same phone number is the same person in Ecuador
    and in Colombia - but each country has its own currency, so a customer
    summed across both would report USD added to COP: a number that is not
    wrong by a little, it means nothing at all. The view never sums across
    countries, and this endpoint never asks it to. With `country` the caller
    picks; without it, the country where they order most is shown, because
    that is the one the operator meant.

    This is also the only place the address and the document are decrypted.
    The operator opening one customer is usually about to send them a parcel,
    and a parcel needs an address - decrypting one is proportionate. The
    customers TABLE cannot return them at all: different columns, different
    mapping, different response model.
    """
    normalised = customer_hash.lower()

    clauses = ["c.customer_hash = %(customer_hash)s"]
    params: dict[str, Any] = {"customer_hash": normalised}
    if country:
        clauses.append("c.country_code = %(country)s")
        params["country"] = country.upper()
    # Without `country` the door guard has nothing to check, and "the country
    # where they order most" could be one this membership may not read. The
    # scope is applied here too, so the answer is 404 - never another
    # country's row.
    if user.countries is not None:
        clauses.append("upper(c.country_code) = ANY(%(scope)s)")
        params["scope"] = list(user.countries)

    row = fetch_one(
        conn,
        f"SELECT {_CUSTOMER_COLUMNS}, c.country_code, "
        "       c.last_document_enc, c.last_address_enc, "
        "       c.last_city_name AS customer_city, "
        "       c.last_province_name AS customer_province "
        "FROM mart.v_customer_metrics c "
        f"WHERE {' AND '.join(clauses)} ORDER BY c.orders DESC LIMIT 1",
        params,
    )
    if row is None:
        raise NotFound("Ese cliente no existe en tu workspace")

    visible = contact_visible(user)
    contact = reveal(row, CUSTOMER_DETAIL_CONTACT, visible=visible)
    customer = CustomerDetailRow(**{**row, **contact})
    disclosed = 1 if carried_contact(contact) else 0

    # The customer's own guides, read through exactly the same filter builder
    # the orders table uses, so a row means the same thing on both screens. The
    # country comes from the row that was just found, not from the request:
    # picking a different one here would list guides the metrics above exclude.
    order_where, order_params = _order_filters(
        country=row["country_code"], customer_hash=normalised
    )
    order_params["limit"] = CUSTOMER_ORDERS_LIMIT
    raw_orders = fetch_all(
        conn,
        f"SELECT {_ORDER_COLUMNS} FROM mart.v_orders o {order_where} "
        "ORDER BY o.created_date DESC, o.tracking_number DESC LIMIT %(limit)s",
        order_params,
    )
    orders, order_disclosed = _rows_with_contact(
        raw_orders, ORDER_CONTACT, OrderRow, visible=visible
    )

    log_access(
        conn,
        user,
        endpoint="GET /customers/{customer_hash}",
        # Records decrypted, not people: the customer row plus each of their
        # guides that carried contact data. It is always ONE person - the
        # endpoint takes a single hash - so a reviewer reading this count as a
        # headcount would overcount. It is the volume that left, which is the
        # question an incident actually asks, and it is now bigger than it was
        # before this card decrypted an address.
        record_count=disclosed + order_disclosed,
        ip=ip,
    )
    return CustomerDetail(customer=customer, orders=orders, pii_visible=visible)


# =============================================================================
# Helpers
# =============================================================================


def _customer_filters(
    *, country: str, grade: CustomerGrade | None, min_orders: int
) -> tuple[str, dict[str, Any]]:
    clauses = ["c.country_code = %(country)s"]
    params: dict[str, Any] = {"country": country.upper()}

    if grade:
        clauses.append("c.customer_grade = %(grade)s")
        params["grade"] = grade
    if min_orders:
        clauses.append("c.orders >= %(min_orders)s")
        params["min_orders"] = min_orders

    return f"WHERE {' AND '.join(clauses)}", params
