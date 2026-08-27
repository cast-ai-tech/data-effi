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

WHICH PLATFORM, AND SAYING SO
`platform` (migrations 040/041) narrows every range-aware function to the
guides one platform loaded - Effi, Dropi, the manual upload - and `platform`
on the response reports the one that was APPLIED. The four view-backed
endpoints and the two that carry their own window cannot separate platforms;
they accept the parameter for uniformity and answer `platform: null`, so the
card can say "mezcla todas" instead of letting the reader assume. `/platforms`
ignores it on purpose: it IS the comparison between platforms.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.db import execute, fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, tenant_of
from api.errors import InvalidDateField, InvalidDateRange, InvalidPlatform
from api.schemas import (
    DATE_FIELDS,
    AgingRow,
    CarrierRow,
    CarrierZoneRow,
    CashCycleRow,
    CohortRow,
    ContributionSplitRow,
    CpaRow,
    CsRow,
    DailyContributionRow,
    DailyStatusRow,
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
    PlatformSummaryRow,
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


# Lower-case catalogue codes: `effi`, `dropi`, `manual_xlsx`. The shape is
# checked here; whether the code exists is checked against core.platform, so a
# typo is refused instead of quietly widening to "todas".
PlatformQuery = Annotated[
    str | None,
    Query(
        max_length=40,
        pattern=r"^[a-z0-9_]+$",
        description="Plataforma que cargó las guías: effi, dropi, manual_xlsx... Vacío = todas.",
    ),
]


def _check_platform(conn: DbDep, platform: str | None) -> str | None:
    """Reject a platform the catalogue does not know, by name."""
    if platform is None:
        return None
    code = platform.lower()
    row = fetch_one(conn, "SELECT 1 FROM core.platform WHERE code = %(code)s", {"code": code})
    if row is None:
        known = fetch_all(conn, "SELECT code FROM core.platform ORDER BY sort_order, code")
        raise InvalidPlatform(
            f"'{platform}' no es una plataforma del catálogo. "
            f"Usa una de estas: {', '.join(r['code'] for r in known)}."
        )
    return code


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
    platform: str | None = None,
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
        "SELECT mart.f_excluded_no_date(%(country)s, %(date_field)s, %(platform)s) AS excluded",
        {
            "country": country.upper() if country else None,
            "date_field": date_field,
            "platform": platform,
        },
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
    platform: str | None = None,
) -> KpiResponse[RowT]:
    """Read one of the range-aware functions from migrations 018, 020 and 041.

    The range, the chosen date and the platform go to the function, which
    recomputes from the base rows; the country and any other dimension filter
    stays a plain WHERE over its result.
    """
    basis = _check_field(date_field)
    _check_range(date_from, date_to)
    applied_platform = _check_platform(conn, platform)

    where, params = _filters(country, extra)
    params["date_from"] = date_from
    params["date_to"] = date_to
    params["date_field"] = basis
    params["platform"] = applied_platform

    # El filtro de estados NO viaja aquí: se fija una vez por petición en
    # api/deps.py y lo aplica stg.v_shipment_economics, la vista de la que estas
    # funciones leen. Ver `_apply_status_filter`.
    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.{function}"
        "(%(date_from)s, %(date_to)s, %(date_field)s, %(platform)s) "
        f"{where} ORDER BY {order_by}",
        params,
    )
    return KpiResponse[row_model](  # type: ignore[valid-type]
        rows=[row_model(**row) for row in rows],
        date_basis=basis,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(
            conn, country, basis, date_from, date_to, applied_platform
        ),
        platform=applied_platform,
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
    platform: PlatformQuery = None,
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
    # Validated and NOT applied: the view has no platform in its grain, and
    # the response says `platform: null` rather than pretending.
    _check_platform(conn, platform)
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
    platform: PlatformQuery = None,
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
        platform=platform,
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
    platform: PlatformQuery = None,
) -> KpiResponse[CarrierRow]:
    return _ranged(
        conn,
        "f_carrier_effectiveness",
        CarrierRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
        order_by="shipments DESC",
    )


@router.get("/geo", response_model=KpiResponse[GeoRow], summary="Semáforo geográfico")
def geo(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    platform: PlatformQuery = None,
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
    applied_platform = _check_platform(conn, platform)

    where, params = _filters(country, extra={"level1_name": level1})
    where += (" AND " if where else "WHERE ") + "shipments >= %(min_shipments)s"
    params["min_shipments"] = min_shipments
    params["date_from"] = date_from
    params["date_to"] = date_to
    params["date_field"] = basis
    params["platform"] = applied_platform

    rows = fetch_all(
        conn,
        "SELECT * FROM mart.f_geo_performance"
        "(%(date_from)s, %(date_to)s, %(date_field)s, %(platform)s) "
        f"{where} ORDER BY shipments DESC",
        params,
    )
    return KpiResponse[GeoRow](
        rows=[GeoRow(**row) for row in rows],
        date_basis=basis,
        date_from=date_from,
        date_to=date_to,
        excluded_no_date=_excluded_no_date(
            conn, country, basis, date_from, date_to, applied_platform
        ),
        platform=applied_platform,
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
    platform: PlatformQuery = None,
) -> KpiResponse[ProductRow]:
    return _ranged(
        conn,
        "f_product_performance",
        ProductRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
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
    platform: PlatformQuery = None,
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
    _check_platform(conn, platform)  # validated, not applied: see the docstring
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
    platform: PlatformQuery = None,
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
        platform=platform,
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
    platform: PlatformQuery = None,
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
    _check_platform(conn, platform)  # a CS sheet is not Effi nor Dropi
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
    platform: PlatformQuery = None,
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
    _check_platform(conn, platform)  # ad spend has no fulfilment platform
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
    platform: PlatformQuery = None,
) -> KpiResponse[DropshippingMarginRow]:
    return _ranged(
        conn,
        "f_dropshipping_margin",
        DropshippingMarginRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
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
    platform: PlatformQuery = None,
) -> KpiResponse[FulfillmentRow]:
    return _ranged(
        conn,
        "f_fulfillment_sla",
        FulfillmentRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
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
    platform: PlatformQuery = None,
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
        platform=platform,
        order_by="value_waiting DESC NULLS LAST",
    )


@router.get(
    "/carrier-by-zone",
    response_model=KpiResponse[CarrierZoneRow],
    summary="Efectividad por transportadora en cada ciudad",
)
def carrier_by_zone(
    conn: DbDep,
    country: CountryQuery,
    level1: Annotated[str | None, Query(max_length=120, description="Departamento / estado")] = None,
) -> KpiResponse[CarrierZoneRow]:
    """Last 90 days, fixed: the view carries its own window (migration 039).

    No date range on purpose - "who delivers best here" is a question about
    now, and a carrier's coverage a year ago is not an answer to it.
    """
    where, params = _filters(country, {"level1_name": level1})
    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_carrier_by_zone {where} "
        "ORDER BY level1_name, city_name, shipments DESC",
        params,
    )
    # `date_basis` null says "no range applies here", the same way /kpis/layout
    # does, so the picker can grey itself out instead of pretending to cut.
    return KpiResponse[CarrierZoneRow](
        rows=[CarrierZoneRow(**row) for row in rows], date_basis=None
    )


@router.get("/freight", response_model=KpiResponse[FreightRow], summary="Análisis de flete")
def freight(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    platform: PlatformQuery = None,
) -> KpiResponse[FreightRow]:
    return _ranged(
        conn,
        "f_freight_analysis",
        FreightRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
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
    platform: PlatformQuery = None,
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
        platform=platform,
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
    platform: PlatformQuery = None,
) -> KpiResponse[ProblemRateRow]:
    return _ranged(
        conn,
        "f_problem_rate",
        ProblemRateRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
        order_by="problem_rate_pct DESC NULLS LAST",
    )


@router.get(
    "/global", response_model=KpiResponse[GlobalRow], summary="Consolidado multi-país"
)
def global_summary(
    conn: DbDep,
    user: CurrentUserDep,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    platform: PlatformQuery = None,
) -> KpiResponse[GlobalRow]:
    """No country filter: this endpoint IS the comparison between countries.

    Which is exactly why a limited membership is filtered here instead of at the
    door: there is no `country` parameter to refuse, so a partner scoped to
    Guatemala gets the Guatemala row and nothing else.

    The FX rate is not filtered by the range. It stays the latest one known,
    because "what is this worth to me" is a question about today.

    Under a `platform` filter `ad_spend` is 0 and `contribution` is contribution
    BEFORE media: ad spend belongs to an ads connection, never to Effi or Dropi,
    so there is no honest share of it to subtract (migration 041).
    """
    response = _ranged(
        conn,
        "f_global_summary",
        GlobalRow,
        country=None,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
        order_by="contribution DESC NULLS LAST",
    )
    if user.countries is not None:
        response.rows = [
            row for row in response.rows if row.country_code.upper() in user.countries
        ]
    return response


@router.get(
    "/daily-status",
    response_model=KpiResponse[DailyStatusRow],
    summary="Resumen diario por estados y plataforma",
)
def daily_status(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    platform: PlatformQuery = None,
) -> KpiResponse[DailyStatusRow]:
    """The table in the operator's hand-made report: day x platform x status.

    `day` IS the chosen date. With `entrega` the table reads "entregadas por
    día de entrega", which is a different report and a valid one; the basis
    on the response says which it was.
    """
    return _ranged(
        conn,
        "f_daily_status",
        DailyStatusRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=platform,
        order_by="day, platform_code",
    )


@router.get(
    "/platforms",
    response_model=KpiResponse[PlatformSummaryRow],
    summary="Consolidado por plataforma (Effi, Dropi, carga manual)",
)
def platforms(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: DateFieldQuery = BY_CREATION,
    platform: PlatformQuery = None,
) -> KpiResponse[PlatformSummaryRow]:
    """No platform filter: this endpoint IS the comparison between platforms.

    `share_pct` only means something against every platform of the country,
    so `platform` is validated, ignored, and reported as null - the same way
    `/global` treats `country`.
    """
    _check_platform(conn, platform)
    return _ranged(
        conn,
        "f_platform_summary",
        PlatformSummaryRow,
        country=country,
        date_from=date_from,
        date_to=date_to,
        date_field=date_field,
        platform=None,
        order_by="shipments DESC, platform_code",
    )


@router.get(
    "/layout",
    response_model=LayoutResponse,
    summary="Qué widgets están disponibles, degradados o bloqueados",
)
def layout(
    conn: DbDep, user: CurrentUserDep, country: CountryQuery
) -> LayoutResponse:
    """The frontend renders exactly this. It never decides availability itself.

    No date range, and `date_basis` is null to say so. Which widgets exist for a
    country depends on which connections are wired up, not on which week is
    being looked at - narrowing it by date would blank the dashboard for any
    range with no data in it, which is precisely when the operator needs to see
    the widget and its "sin datos" message.

    QUÉ APORTA EL LEFT JOIN. El catálogo dice qué tarjetas EXISTEN y en qué
    estado están; las preferencias dicen cómo las quiere ESTA persona: en qué
    orden y de qué ancho. Se combinan aquí, no en el frontend, para que la
    pantalla siga renderizando una sola lista ya resuelta. Sin preferencias
    guardadas, `COALESCE` devuelve el orden del catálogo y ancho 1 - es decir,
    exactamente el tablero de siempre.
    """
    rows = fetch_all(
        conn,
        """
        SELECT l.tenant_id, l.country_code, l.widget_code, l.tab, l.title,
               l.description, l.required_domains, l.optional_domains,
               l.missing_required, l.missing_optional, l.awaiting_data,
               l.state, l.state_message,
               COALESCE(p.sort_order, l.sort_order) AS sort_order,
               COALESCE(p.width, 1)                 AS width,
               COALESCE(p.hidden, false)            AS hidden
          FROM mart.v_country_dashboard_layout l
          LEFT JOIN core.dashboard_widget_pref p
                 ON p.user_id = %(user_id)s
                AND p.country_code = l.country_code
                AND p.widget_code = l.widget_code
         WHERE l.country_code = %(country)s
         ORDER BY l.tab, COALESCE(p.sort_order, l.sort_order), l.widget_code
        """,
        {"country": country.upper(), "user_id": user.id},
    )
    return LayoutResponse(
        country_code=country.upper(),
        widgets=[LayoutWidget(**row) for row in rows],
        date_basis=None,
    )


class WidgetPlacement(BaseModel):
    """Dónde queda una tarjeta y qué ancho ocupa."""

    widget_code: str
    sort_order: int
    width: int = 1
    hidden: bool = False


class LayoutPreferences(BaseModel):
    placements: list[WidgetPlacement]


@router.put(
    "/layout",
    response_model=LayoutResponse,
    summary="Guardar el orden y el ancho de las tarjetas de esta persona",
)
def save_layout(
    conn: DbDep,
    user: CurrentUserDep,
    country: CountryQuery,
    preferences: LayoutPreferences,
) -> LayoutResponse:
    """Guarda la personalización del tablero y devuelve el layout ya resuelto.

    POR USUARIO, NO POR EMPRESA. Dos personas de la misma empresa miran cosas
    distintas - quien despacha vive en Logística y quien cobra en Dinero -, así
    que el tablero de una no puede reordenar el de la otra.

    Se acepta cualquier `widget_code`: el catálogo decide qué se RENDERIZA, así
    que una preferencia sobre una tarjeta que hoy no existe simplemente no se
    une a nada, y vuelve a aplicarse sola el día que esa tarjeta aparezca.
    """
    invalid = [p.widget_code for p in preferences.placements if p.width not in (1, 2)]
    if invalid:
        raise InvalidPlatform(
            "El ancho de una tarjeta solo puede ser 1 o 2 columnas. "
            f"Revisa: {', '.join(sorted(set(invalid)))}."
        )

    for placement in preferences.placements:
        execute(
            conn,
            """
            INSERT INTO core.dashboard_widget_pref
                (tenant_id, user_id, country_code, widget_code, sort_order, width, hidden)
            VALUES (%(tenant)s, %(user)s, %(country)s, %(code)s, %(order)s, %(width)s, %(hidden)s)
            ON CONFLICT (user_id, country_code, widget_code) DO UPDATE SET
                sort_order = EXCLUDED.sort_order,
                width      = EXCLUDED.width,
                hidden     = EXCLUDED.hidden,
                updated_at = now()
            """,
            {
                "tenant": tenant_of(user),
                "user": user.id,
                "country": country.upper(),
                "code": placement.widget_code,
                "order": placement.sort_order,
                "width": placement.width,
                "hidden": placement.hidden,
            },
        )

    return layout(conn, user, country)
