"""KPI endpoints.

Every one of these is a thin filter over a mart view. There is no arithmetic in
this file on purpose: if a number is wrong, it is wrong in SQL, in one place,
where a human can reproduce it with psql.

THE DATE RANGE
Two shapes reach the same place. Four mart views carry a date in their grain
(`v_daily_contribution`, `v_cohort_maturation`, `v_cs_confirmation`,
`v_cpa_roas`), so a WHERE clause is enough. The rest are aggregates that summed
the date away, and migration 018 gave each of them a `mart.f_*(date, date)`
function that takes the range and computes from the base rows - which is what
keeps `percentile_cont` exact instead of reassembled. Either way the range never
turns into arithmetic here.

WHICH DATE, AND SAYING SO
`date_field` picks between the three dates a guide has - `creacion` (default),
`despacho`, `entrega` - and migration 020 resolves the name to a column in one
place. `date_basis` on every response reports the one that was actually APPLIED,
which is not always the one that was asked for: four endpoints have a fixed
basis and say so rather than pretending (see the header of migration 020).

An endpoint that cannot filter at all reports `date_basis: null` instead of
accepting the parameters and quietly ignoring them, which is the bug this whole
change exists to remove. `excluded_no_date` is the other half of that honesty:
`delivered_at` is null on 989 of Ecuador's 1,649 guides, so filtering by
`entrega` drops them, and the number says how many.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.db import fetch_all, fetch_one
from api.deps import DbDep
from api.errors import InvalidDateField, InvalidDateRange
from api.schemas import (
    DATE_FIELDS,
    AgingRow,
    CarrierRow,
    CashCycleRow,
    CohortRow,
    ContributionSplitRow,
    CpaRow,
    CsRow,
    DailyContributionRow,
    DateField,
    DropshippingMarginRow,
    FreightRow,
    FulfillmentRow,
    GeoRow,
    GlobalRow,
    KpiResponse,
    LayoutResponse,
    LayoutWidget,
    OfficeRescueRow,
    ProblemRateRow,
    ProductRow,
)

router = APIRouter(prefix="/kpis", tags=["kpis"])

CountryQuery = Annotated[str, Query(min_length=2, max_length=2, description="Código ISO del país")]
OptionalDate = Annotated[date | None, Query(description="Formato ISO: yyyy-mm-dd")]

RowT = TypeVar("RowT", bound=BaseModel)

# The default basis, and the one the operator should have to think about least:
# the day the guide was created. See migration 018 for why it beats delivery or
# settlement date.
BY_CREATION: DateField = "creacion"

# Declared as a plain string with the enum published in the schema, rather than
# a Literal, so that a wrong value produces the message below - which names the
# three valid options - instead of Pydantic's generic "campos inválidos". The
# frontend still reads the enum out of /openapi.json to build its selector.
DateFieldQuery = Annotated[
    str,
    Query(
        description="Sobre qué fecha filtrar: creacion, despacho o entrega.",
        json_schema_extra={"enum": list(DATE_FIELDS)},
    ),
]


def _check_field(date_field: str) -> DateField:
    """Reject a date this platform does not have, by name."""
    if date_field not in DATE_FIELDS:
        raise InvalidDateField(
            f"'{date_field}' no es una fecha que podamos filtrar. "
            f"Usa una de estas: {', '.join(DATE_FIELDS)}."
        )
    return date_field  # type: ignore[return-value]


def _excluded_no_date(
    conn: DbDep, country: str | None, date_field: DateField,
    date_from: date | None, date_to: date | None,
) -> int | None:
    """How many guides the chosen date leaves out of every possible range.

    Only meaningful once a range is applied: with no range nothing is filtered,
    so nothing is excluded, and the honest answer is "no aplica" rather than a
    count of guides that are all present anyway.
    """
    if date_from is None and date_to is None:
        return None

    row = fetch_one(
        conn,
        "SELECT mart.f_excluded_no_date(%(country)s, %(date_field)s) AS excluded",
        {"country": country.upper() if country else None, "date_field": date_field},
    )
    return int(row["excluded"]) if row else None


def _check_range(date_from: date | None, date_to: date | None) -> None:
    """An inverted range is a mistake, not an empty result.

    Returning zero rows for `date_from > date_to` would look exactly like "no
    hubo operación esa semana", which is the wrong thing to tell someone who
    typed the two dates the wrong way round.
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRange(
            f"El rango de fechas está invertido: 'desde' ({date_from:%Y-%m-%d}) es "
            f"posterior a 'hasta' ({date_to:%Y-%m-%d}). Intercambia las dos fechas."
        )


def _filters(
    country: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """WHERE over the dimensions. The date range never comes through here."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if country:
        clauses.append("country_code = %(country)s")
        params["country"] = country.upper()

    for key, value in (extra or {}).items():
        if value is not None:
            clauses.append(f"{key} = %({key})s")
            params[key] = value

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _range_filter(
    date_column: str,
    country: str | None,
    date_from: date | None,
    date_to: date | None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """WHERE for the four views that already carry a date in their grain."""
    where, params = _filters(country, extra)
    clauses = [where[len("WHERE ") :]] if where else []

    if date_from:
        clauses.append(f"{date_column} >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append(f"{date_column} <= %(date_to)s")
        params["date_to"] = date_to

    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _ranged(
    conn: DbDep,
    function: str,
    row_model: type[RowT],
    *,
    country: str | None,
    date_from: date | None,
    date_to: date | None,
    date_field: str,
    order_by: str,
    extra: dict[str, Any] | None = None,
) -> KpiResponse[RowT]:
    """Read one of the range-aware functions from migrations 018 and 020.

    The range and the chosen date go to the function, which recomputes from the
    base rows; the country and any other dimension filter stays a plain WHERE
    over its result.
    """
    basis = _check_field(date_field)
    _check_range(date_from, date_to)

    where, params = _filters(country, extra)
    params["date_from"] = date_from
    params["date_to"] = date_to
    params["date_field"] = basis

    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.{function}"
        "(%(date_from)s, %(date_to)s, %(date_field)s) "
        f"{where} ORDER BY {order_by}",
        params,
    )
    return KpiResponse[row_model](  # type: ignore[valid-type]
        rows=[row_model(**row) for row in rows],
        date_basis=basis,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(conn, country, basis, date_from, date_to),
    )


@router.get(
    "/daily-contribution",
    response_model=KpiResponse[DailyContributionRow],
    summary="Contribución diaria por cohorte de despacho",
)
def daily_contribution(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    store_id: Annotated[str | None, Query()] = None,
) -> KpiResponse[DailyContributionRow]:
    """`date_field` is accepted and NOT honoured here, and the response says so.

    This chart's `day` axis carries the ad spend of that same day. Re-axing it
    onto dispatch or delivery would subtract Monday's media cost from guides
    created the week before - `contribution` would move, plausibly and wrongly.
    So the basis stays `creacion` whatever was asked for, and `date_basis`
    reports `creacion` so the interface can label the card instead of letting
    the operator assume the picker reached it.
    """
    _check_field(date_field)
    _check_range(date_from, date_to)
    where, params = _range_filter(
        "day", country, date_from, date_to, extra={"store_id": store_id}
    )

    rows = fetch_all(
        conn, f"SELECT * FROM mart.v_daily_contribution {where} ORDER BY day", params
    )
    return KpiResponse[DailyContributionRow](
        rows=[DailyContributionRow(**row) for row in rows],
        date_basis=BY_CREATION,
        date_from=date_from,
        date_to=date_to,
        # created_date is NOT NULL, so a creation-date filter excludes nobody.
        excluded_no_date=_excluded_no_date(
            conn, country, BY_CREATION, date_from, date_to
        ),
    )


@router.get(
    "/contribution-split",
    response_model=KpiResponse[ContributionSplitRow],
    summary="Contribución cerrada vs. capital en la calle",
)
def contribution_split(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[ContributionSplitRow]:
    """The headline number, taken apart.

    A single net contribution turns negative whenever a young cohort is large -
    those guides already paid freight, product and fee and have collected
    nothing, because they have not arrived yet. That is a statement about
    timing, not about profitability, and the split says so: what closed on one
    side, what is still moving on the other.
    """
    return _ranged(
        conn,
        "f_contribution_split",
        ContributionSplitRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="country_code",
    )


@router.get(
    "/carriers",
    response_model=KpiResponse[CarrierRow],
    summary="Efectividad por transportadora",
)
def carriers(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[CarrierRow]:
    return _ranged(
        conn,
        "f_carrier_effectiveness",
        CarrierRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="shipments DESC",
    )


@router.get("/geo", response_model=KpiResponse[GeoRow], summary="Semáforo geográfico")
def geo(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    level1: Annotated[str | None, Query(description="Filtrar por departamento/estado")] = None,
    min_shipments: Annotated[int, Query(ge=0)] = 0,
) -> KpiResponse[GeoRow]:
    """`min_shipments` counts guides INSIDE the range.

    That is deliberate. A city with 40 guides over the year and 3 in the week
    being looked at has 3 in that week, and a threshold that ignored the range
    would let it back onto a screen whose numbers are built from three parcels.
    """
    basis = _check_field(date_field)
    _check_range(date_from, date_to)

    where, params = _filters(country, extra={"level1_name": level1})
    where += (" AND " if where else "WHERE ") + "shipments >= %(min_shipments)s"
    params["min_shipments"] = min_shipments
    params["date_from"] = date_from
    params["date_to"] = date_to
    params["date_field"] = basis

    rows = fetch_all(
        conn,
        "SELECT * FROM mart.f_geo_performance"
        "(%(date_from)s, %(date_to)s, %(date_field)s) "
        f"{where} ORDER BY shipments DESC",
        params,
    )
    return KpiResponse[GeoRow](
        rows=[GeoRow(**row) for row in rows],
        date_basis=basis,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(conn, country, basis, date_from, date_to),
    )


@router.get(
    "/products", response_model=KpiResponse[ProductRow], summary="Desempeño por producto"
)
def products(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[ProductRow]:
    return _ranged(
        conn,
        "f_product_performance",
        ProductRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="contribution DESC NULLS LAST",
    )


@router.get(
    "/cohorts", response_model=KpiResponse[CohortRow], summary="Maduración de cohortes"
)
def cohorts(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    only_observable: Annotated[bool, Query()] = True,
) -> KpiResponse[CohortRow]:
    """A cohort IS the creation cohort, so `date_field` cannot apply here.

    `days_since` measures the distance from creation to delivery. Group the
    same guides by their delivery date and every one of them arrives on day 0
    of its own cohort - the curve flattens into a straight line that means
    nothing. The parameter is accepted so the frontend can send one range and
    one field to every widget; `date_basis` comes back `creacion`.
    """
    _check_field(date_field)
    _check_range(date_from, date_to)
    where, params = _range_filter("cohort_date", country, date_from, date_to)
    if only_observable:
        where += (" AND " if where else "WHERE ") + "is_observable"

    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_cohort_maturation {where} ORDER BY cohort_date, days_since",
        params,
    )
    return KpiResponse[CohortRow](
        rows=[CohortRow(**row) for row in rows],
        date_basis=BY_CREATION,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(
            conn, country, BY_CREATION, date_from, date_to
        ),
    )


@router.get(
    "/aging", response_model=KpiResponse[AgingRow], summary="Antigüedad de guías abiertas"
)
def aging(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[AgingRow]:
    """The range picks which open guides; the age is still measured against today.

    A guide created in January and still open in August has been open since
    January. Clipping its age to the end of the range would report it as fresh.
    """
    return _ranged(
        conn,
        "f_aging",
        AgingRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="bucket_order",
    )


@router.get(
    "/cs", response_model=KpiResponse[CsRow], summary="Confirmación de servicio al cliente"
)
def customer_service(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[CsRow]:
    """Filters by the day customer service worked, not by the guide's cohort.

    Most of these interactions are not linked to a guide at all - a call that
    ends in 'rejected' never became one - so filtering by creation date would
    delete the rejections and report a confirmation rate close to 100%.

    `date_field` is accepted and ignored for that same reason, and
    `excluded_no_date` stays null: it counts guides, which is the wrong
    universe for a row that may have none.
    """
    _check_field(date_field)
    _check_range(date_from, date_to)
    where, params = _range_filter("day", country, date_from, date_to)
    rows = fetch_all(conn, f"SELECT * FROM mart.v_cs_confirmation {where} ORDER BY day", params)
    return KpiResponse[CsRow](
        rows=[CsRow(**row) for row in rows],
        date_basis="interaccion",
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/cpa",
    response_model=KpiResponse[CpaRow],
    summary="CPA y ROAS (requiere conexión de pauta)",
)
def cpa(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[CpaRow]:
    """Filters by the day the money was spent on ads.

    The view is driven by the ad-spend side and joins guides created on the same
    day, so it is one calendar axis - but the rows exist because spend exists,
    and naming the basis after the spend is the honest label.

    `date_field` is accepted and ignored: a spend row carries one date and no
    guide. `excluded_no_date` stays null for the same reason.
    """
    _check_field(date_field)
    _check_range(date_from, date_to)
    where, params = _range_filter("day", country, date_from, date_to)
    rows = fetch_all(conn, f"SELECT * FROM mart.v_cpa_roas {where} ORDER BY day", params)
    return KpiResponse[CpaRow](
        rows=[CpaRow(**row) for row in rows],
        date_basis="pauta",
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/dropshipping-margin",
    response_model=KpiResponse[DropshippingMarginRow],
    summary="Cadena de márgenes por producto",
)
def dropshipping_margin(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[DropshippingMarginRow]:
    return _ranged(
        conn,
        "f_dropshipping_margin",
        DropshippingMarginRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="net_contribution DESC NULLS LAST",
    )


@router.get(
    "/fulfillment",
    response_model=KpiResponse[FulfillmentRow],
    summary="Alistamiento y cumplimiento de promesa",
)
def fulfillment(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[FulfillmentRow]:
    return _ranged(
        conn,
        "f_fulfillment_sla",
        FulfillmentRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="shipments DESC",
    )


@router.get(
    "/office-rescue",
    response_model=KpiResponse[OfficeRescueRow],
    summary="Guías esperando en oficina",
)
def office_rescue(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[OfficeRescueRow]:
    """Same reading as `/aging`: guides waiting TODAY, created inside the range."""
    return _ranged(
        conn,
        "f_office_rescue",
        OfficeRescueRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="value_waiting DESC NULLS LAST",
    )


@router.get("/freight", response_model=KpiResponse[FreightRow], summary="Análisis de flete")
def freight(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[FreightRow]:
    return _ranged(
        conn,
        "f_freight_analysis",
        FreightRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="shipments DESC",
    )


@router.get(
    "/cash-cycle", response_model=KpiResponse[CashCycleRow], summary="Ciclo de caja"
)
def cash_cycle(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[CashCycleRow]:
    """Filters by creation date, NOT by settlement date.

    `delivered_unsettled` and `cash_in_transit` count the guides whose money has
    not arrived - the ones with no settlement date at all. Filtering on that
    column would drop every one of them and report that nothing is pending.
    """
    return _ranged(
        conn,
        "f_cash_cycle",
        CashCycleRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="country_code",
    )


@router.get(
    "/problem-rate",
    response_model=KpiResponse[ProblemRateRow],
    summary="Novedad, oficina y devolución como un solo número",
)
def problem_rate(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[ProblemRateRow]:
    return _ranged(
        conn,
        "f_problem_rate",
        ProblemRateRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="problem_rate_pct DESC NULLS LAST",
    )


@router.get(
    "/global", response_model=KpiResponse[GlobalRow], summary="Consolidado multi-país"
)
def global_summary(
    conn: DbDep,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
) -> KpiResponse[GlobalRow]:
    """No country filter: this endpoint IS the comparison between countries.

    The FX rate is not filtered by the range. It stays the latest one known,
    because "what is this worth to me" is a question about today.
    """
    return _ranged(
        conn,
        "f_global_summary",
        GlobalRow,
        country=None,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        order_by="contribution DESC NULLS LAST",
    )


@router.get(
    "/layout",
    response_model=LayoutResponse,
    summary="Qué widgets están disponibles, degradados o bloqueados",
)
def layout(conn: DbDep, country: CountryQuery) -> LayoutResponse:
    """The frontend renders exactly this. It never decides availability itself.

    No date range, and `date_basis` is null to say so. Which widgets exist for a
    country depends on which connections are wired up, not on which week is
    being looked at - narrowing it by date would blank the dashboard for any
    range with no data in it, which is precisely when the operator needs to see
    the widget and its "sin datos" message.
    """
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_country_dashboard_layout WHERE country_code = %(country)s "
        "ORDER BY tab, sort_order",
        {"country": country.upper()},
    )
    return LayoutResponse(
        country_code=country.upper(),
        widgets=[LayoutWidget(**row) for row in rows],
        date_basis=None,
    )
