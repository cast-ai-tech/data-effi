"""KPI endpoints.

Every one of these is a thin filter over a mart view. There is no arithmetic in
this file on purpose: if a number is wrong, it is wrong in SQL, in one place,
where a human can reproduce it with psql.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from api.db import fetch_all
from api.deps import DbDep
from api.schemas import (
    AgingRow,
    CarrierRow,
    CashCycleRow,
    CohortRow,
    CpaRow,
    CsRow,
    DailyContributionRow,
    DropshippingMarginRow,
    FreightRow,
    FulfillmentRow,
    GeoRow,
    GlobalRow,
    LayoutResponse,
    LayoutWidget,
    OfficeRescueRow,
    ProblemRateRow,
    ProductRow,
)

router = APIRouter(prefix="/kpis", tags=["kpis"])

CountryQuery = Annotated[str, Query(min_length=2, max_length=2, description="Código ISO del país")]
OptionalDate = Annotated[date | None, Query(description="Formato ISO: yyyy-mm-dd")]


def _range_filter(
    date_column: str,
    country: str | None,
    date_from: date | None,
    date_to: date | None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if country:
        clauses.append("country_code = %(country)s")
        params["country"] = country.upper()
    if date_from:
        clauses.append(f"{date_column} >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        clauses.append(f"{date_column} <= %(date_to)s")
        params["date_to"] = date_to

    for key, value in (extra or {}).items():
        if value is not None:
            clauses.append(f"{key} = %({key})s")
            params[key] = value

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


@router.get(
    "/daily-contribution",
    response_model=list[DailyContributionRow],
    summary="Contribución diaria por cohorte de despacho",
)
def daily_contribution(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    store_id: Annotated[str | None, Query()] = None,
) -> list[DailyContributionRow]:
    where, params = _range_filter("day", country, date_from, date_to)
    if store_id:
        where += (" AND " if where else "WHERE ") + "store_id = %(store_id)s"
        params["store_id"] = store_id

    rows = fetch_all(
        conn, f"SELECT * FROM mart.v_daily_contribution {where} ORDER BY day", params
    )
    return [DailyContributionRow(**row) for row in rows]


@router.get("/carriers", response_model=list[CarrierRow], summary="Efectividad por transportadora")
def carriers(conn: DbDep, country: CountryQuery) -> list[CarrierRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_carrier_effectiveness WHERE country_code = %(country)s "
        "ORDER BY shipments DESC",
        {"country": country.upper()},
    )
    return [CarrierRow(**row) for row in rows]


@router.get("/geo", response_model=list[GeoRow], summary="Semáforo geográfico")
def geo(
    conn: DbDep,
    country: CountryQuery,
    level1: Annotated[str | None, Query(description="Filtrar por departamento/estado")] = None,
    min_shipments: Annotated[int, Query(ge=0)] = 0,
) -> list[GeoRow]:
    clauses = ["country_code = %(country)s", "shipments >= %(min_shipments)s"]
    params: dict[str, Any] = {"country": country.upper(), "min_shipments": min_shipments}
    if level1:
        clauses.append("level1_name = %(level1)s")
        params["level1"] = level1

    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_geo_performance WHERE {' AND '.join(clauses)} "
        "ORDER BY shipments DESC",
        params,
    )
    return [GeoRow(**row) for row in rows]


@router.get("/products", response_model=list[ProductRow], summary="Desempeño por producto")
def products(conn: DbDep, country: CountryQuery) -> list[ProductRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_product_performance WHERE country_code = %(country)s "
        "ORDER BY contribution DESC NULLS LAST",
        {"country": country.upper()},
    )
    return [ProductRow(**row) for row in rows]


@router.get("/cohorts", response_model=list[CohortRow], summary="Maduración de cohortes")
def cohorts(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    only_observable: Annotated[bool, Query()] = True,
) -> list[CohortRow]:
    where, params = _range_filter("cohort_date", country, date_from, date_to)
    if only_observable:
        where += (" AND " if where else "WHERE ") + "is_observable"

    rows = fetch_all(
        conn,
        f"SELECT * FROM mart.v_cohort_maturation {where} ORDER BY cohort_date, days_since",
        params,
    )
    return [CohortRow(**row) for row in rows]


@router.get("/aging", response_model=list[AgingRow], summary="Antigüedad de guías abiertas")
def aging(conn: DbDep, country: CountryQuery) -> list[AgingRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_aging WHERE country_code = %(country)s ORDER BY bucket_order",
        {"country": country.upper()},
    )
    return [AgingRow(**row) for row in rows]


@router.get("/cs", response_model=list[CsRow], summary="Confirmación de servicio al cliente")
def customer_service(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
) -> list[CsRow]:
    where, params = _range_filter("day", country, date_from, date_to)
    rows = fetch_all(conn, f"SELECT * FROM mart.v_cs_confirmation {where} ORDER BY day", params)
    return [CsRow(**row) for row in rows]


@router.get("/cpa", response_model=list[CpaRow], summary="CPA y ROAS (requiere conexión de pauta)")
def cpa(
    conn: DbDep,
    country: CountryQuery,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
) -> list[CpaRow]:
    where, params = _range_filter("day", country, date_from, date_to)
    rows = fetch_all(conn, f"SELECT * FROM mart.v_cpa_roas {where} ORDER BY day", params)
    return [CpaRow(**row) for row in rows]


@router.get(
    "/dropshipping-margin",
    response_model=list[DropshippingMarginRow],
    summary="Cadena de márgenes por producto",
)
def dropshipping_margin(conn: DbDep, country: CountryQuery) -> list[DropshippingMarginRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_dropshipping_margin WHERE country_code = %(country)s "
        "ORDER BY net_contribution DESC NULLS LAST",
        {"country": country.upper()},
    )
    return [DropshippingMarginRow(**row) for row in rows]


@router.get(
    "/fulfillment",
    response_model=list[FulfillmentRow],
    summary="Alistamiento y cumplimiento de promesa",
)
def fulfillment(conn: DbDep, country: CountryQuery) -> list[FulfillmentRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_fulfillment_sla WHERE country_code = %(country)s "
        "ORDER BY shipments DESC",
        {"country": country.upper()},
    )
    return [FulfillmentRow(**row) for row in rows]


@router.get(
    "/office-rescue",
    response_model=list[OfficeRescueRow],
    summary="Guías esperando en oficina",
)
def office_rescue(conn: DbDep, country: CountryQuery) -> list[OfficeRescueRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_office_rescue WHERE country_code = %(country)s "
        "ORDER BY value_waiting DESC NULLS LAST",
        {"country": country.upper()},
    )
    return [OfficeRescueRow(**row) for row in rows]


@router.get("/freight", response_model=list[FreightRow], summary="Análisis de flete")
def freight(conn: DbDep, country: CountryQuery) -> list[FreightRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_freight_analysis WHERE country_code = %(country)s "
        "ORDER BY shipments DESC",
        {"country": country.upper()},
    )
    return [FreightRow(**row) for row in rows]


@router.get("/cash-cycle", response_model=list[CashCycleRow], summary="Ciclo de caja")
def cash_cycle(conn: DbDep, country: CountryQuery) -> list[CashCycleRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_cash_cycle WHERE country_code = %(country)s ORDER BY country_code",
        {"country": country.upper()},
    )
    return [CashCycleRow(**row) for row in rows]


@router.get(
    "/problem-rate",
    response_model=list[ProblemRateRow],
    summary="Novedad, oficina y devolución como un solo número",
)
def problem_rate(conn: DbDep, country: CountryQuery) -> list[ProblemRateRow]:
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_problem_rate WHERE country_code = %(country)s "
        "ORDER BY problem_rate_pct DESC NULLS LAST",
        {"country": country.upper()},
    )
    return [ProblemRateRow(**row) for row in rows]


@router.get("/global", response_model=list[GlobalRow], summary="Consolidado multi-país")
def global_summary(conn: DbDep) -> list[GlobalRow]:
    rows = fetch_all(
        conn, "SELECT * FROM mart.v_global_summary ORDER BY contribution DESC NULLS LAST"
    )
    return [GlobalRow(**row) for row in rows]


@router.get(
    "/layout",
    response_model=LayoutResponse,
    summary="Qué widgets están disponibles, degradados o bloqueados",
)
def layout(conn: DbDep, country: CountryQuery) -> LayoutResponse:
    """The frontend renders exactly this. It never decides availability itself."""
    rows = fetch_all(
        conn,
        "SELECT * FROM mart.v_country_dashboard_layout WHERE country_code = %(country)s "
        "ORDER BY tab, sort_order",
        {"country": country.upper()},
    )
    return LayoutResponse(
        country_code=country.upper(),
        widgets=[LayoutWidget(**row) for row in rows],
    )
