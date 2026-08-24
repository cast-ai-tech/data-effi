"""Real-time recommendations: what changed, what it costs, what to do about it.

THE MODEL DOES NOT DECIDE WHAT IS A PROBLEM. Every detection below is SQL over
the mart views, exactly like `mart.v_alert_signals`. The thresholds are written
here in Python where they can be read, argued with and changed. The money impact
is arithmetic on numbers the database returned.

The model's only job, and only when it is switched on and inside budget, is to
write one paragraph over the findings it was handed. Turn the model off and this
module keeps working - that is the whole design. An operator whose recommendations
disappear because an API key expired has no recommendations.

WHY THIS RUNS AFTER INGESTION. A recommendation about last week's data is not a
recommendation, it is a history lesson. `refresh_after_batch` is called when a
batch finishes so the thresholds the copilot reasons from reflect what just
landed. It performs NO network call: the ingestion path must never be able to
fail because a model was unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from api.db import fetch_all
from api.settings import Settings

logger = logging.getLogger(__name__)

MAX_RECOMMENDATIONS = 12

# -----------------------------------------------------------------------------
# Thresholds. Every one of these is a judgement call, so it is written down
# rather than buried in a query, and each says what it is protecting against.
# -----------------------------------------------------------------------------

# Below this, one unlucky week looks like a trend. These are minimum volumes,
# not targets.
MIN_SHIPMENTS_PRODUCT = 20      # a product is a pattern, not three orders
MIN_UNITS_CATALOGUE = 30        # a cost drift needs units behind it to be real
MIN_SHIPMENTS_CARRIER = 50      # freight per kilo is noisy on small samples
MIN_SHIPMENTS_PREP = 25         # preparation time varies wildly guide to guide
MIN_SHIPMENTS_OFFICE = 5        # a phone call is cheap; five parcels justify it

# A carrier is "materially" more expensive when it charges a fifth more per kilo
# than everyone else moving the same weight. Under that, it is negotiation noise.
FREIGHT_PREMIUM_RATIO = 1.20

# Cash in transit is EXPECTED to be roughly (daily collected revenue x days to
# cash). Half again as much means guides are being collected and not settled.
CASH_IN_TRANSIT_RATIO = 1.50


@dataclass(slots=True)
class Recommendation:
    code: str
    severity: str                   # 'critical' | 'warning' | 'info'
    country_code: str | None
    title: str
    finding: str
    action: str                     # exactly one, and concrete
    impact_amount: float | None
    impact_currency: str | None
    deep_link: str
    detected_at: datetime


# =============================================================================
# Detection
# =============================================================================


def detect(
    conn: psycopg.Connection, country: str | None = None, *, limit: int = MAX_RECOMMENDATIONS
) -> list[dict[str, Any]]:
    """Every deterministic signal, worst money impact first.

    `conn` must already be scoped to a tenant: the mart views filter by
    `core.current_tenant_id()`, so with no GUC set this returns nothing rather
    than someone else's problems.
    """
    country_code = country.upper() if country else None

    found: list[Recommendation] = []
    for detector in (
        _detect_catalogue_cost_drift,
        _detect_product_below_breakeven,
        _detect_prep_above_own_p90,
        _detect_office_queue_recoverable,
        _detect_carrier_freight_premium,
        _detect_cash_in_transit_high,
    ):
        try:
            found.extend(detector(conn, country_code))
        except Exception:
            # One view missing or one query failing must not blank the whole
            # panel. The operator still gets the five signals that did work.
            logger.warning("recommendation detector %s failed", detector.__name__, exc_info=True)

    found.sort(key=_ranking, reverse=True)
    return [asdict(item) for item in found[:limit]]


def _ranking(item: Recommendation) -> tuple[int, float]:
    severity_rank = {"critical": 2, "warning": 1, "info": 0}.get(item.severity, 0)
    return (severity_rank, float(item.impact_amount or 0))


def _country_filter(column: str, country_code: str | None) -> tuple[str, tuple]:
    """`WHERE` fragment plus params. No country means every country.

    The fragment is interpolated into the queries below, so it never contains a
    value: `column` is a literal written in this file, and the country code is
    returned as a bound parameter. Nothing user-supplied is ever formatted into
    SQL here.
    """
    if country_code is None:
        return "TRUE", ()
    return f"{column} = %s", (country_code,)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _money(amount: Any, currency: str | None) -> str:
    if amount is None:
        return "un monto no calculable"
    return f"{float(amount):,.0f} {currency or ''}".strip()


def _link(country_code: str | None, tab: str) -> str:
    if not country_code:
        return "/global"
    return f"/{country_code.lower()}?tab={tab}"


# -----------------------------------------------------------------------------
# 1. A product whose real cost drifted away from the catalogue
# -----------------------------------------------------------------------------


def _detect_catalogue_cost_drift(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """The catalogue says one cost, the guides say another.

    `v_product_catalogue` has no country (the catalogue belongs to the business,
    not to a market), so the country comes from the margin view via product_id.
    """
    where, params = _country_filter("m.country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        SELECT m.country_code,
               c.product_name,
               c.sku,
               c.unit_cost,
               c.observed_unit_cost,
               sum(m.units)       AS units,
               min(m.currency_code) AS currency_code
        FROM mart.v_product_catalogue c
        JOIN mart.v_dropshipping_margin m ON m.product_id = c.product_id
        WHERE c.catalogue_status = 'costo_desactualizado'
          AND c.unit_cost IS NOT NULL
          AND c.observed_unit_cost IS NOT NULL
          AND {where}
        GROUP BY m.country_code, c.product_name, c.sku, c.unit_cost, c.observed_unit_cost
        HAVING sum(m.units) >= %s
        """,
        (*params, MIN_UNITS_CATALOGUE),
    )

    out: list[Recommendation] = []
    for row in rows:
        catalogue = _num(row["unit_cost"]) or 0.0
        observed = _num(row["observed_unit_cost"]) or 0.0
        units = int(row["units"] or 0)
        if catalogue <= 0:
            continue

        drift_pct = (observed - catalogue) / catalogue * 100
        impact = abs(observed - catalogue) * units
        direction = "más caro" if observed > catalogue else "más barato"
        currency = row["currency_code"]

        out.append(
            Recommendation(
                code="catalogue_cost_drift",
                severity="warning",
                country_code=row["country_code"],
                title=f"El costo de {row['product_name']} ya no es el del catálogo",
                finding=(
                    f"El catálogo dice {_money(catalogue, currency)} por unidad, pero las "
                    f"guías dicen {_money(observed, currency)}: {abs(drift_pct):.0f}% "
                    f"{direction} sobre {units:,} unidades. Toda la contribución que "
                    f"calculas para este producto está corrida en "
                    f"{_money(impact, currency)}."
                ),
                action=(
                    f"Abre {row['product_name']} en el catálogo y pon el costo real "
                    f"({_money(observed, currency)}) o confirma que el proveedor cambió el precio."
                ),
                impact_amount=round(impact, 2),
                impact_currency=currency,
                deep_link=_link(row["country_code"], "efectividad"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# -----------------------------------------------------------------------------
# 2. A product below its own break-even delivery rate
# -----------------------------------------------------------------------------


def _detect_product_below_breakeven(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """Freight and stock are paid on every dispatch. Below break-even, each one loses."""
    where, params = _country_filter("country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        SELECT country_code, product_name, shipments, delivered,
               delivery_rate_pct, breakeven_delivery_pct, net_contribution,
               contribution_per_shipment, currency_code
        FROM mart.v_dropshipping_margin
        WHERE breakeven_delivery_pct IS NOT NULL
          AND delivery_rate_pct IS NOT NULL
          AND delivery_rate_pct < breakeven_delivery_pct
          AND shipments >= %s
          AND {where}
        """,
        (MIN_SHIPMENTS_PRODUCT, *params),
    )

    out: list[Recommendation] = []
    for row in rows:
        actual = _num(row["delivery_rate_pct"]) or 0.0
        breakeven = _num(row["breakeven_delivery_pct"]) or 0.0
        contribution = _num(row["net_contribution"])
        per_shipment = _num(row["contribution_per_shipment"])
        gap = breakeven - actual
        currency = row["currency_code"]

        # The loss already booked. Positive contribution below break-even means
        # the two numbers were computed over different periods - not a crisis.
        impact = abs(contribution) if contribution is not None and contribution < 0 else None

        out.append(
            Recommendation(
                code="product_below_breakeven",
                severity="critical" if impact else "warning",
                country_code=row["country_code"],
                title=f"{row['product_name']} entrega por debajo de su punto de equilibrio",
                finding=(
                    f"{row['product_name']} necesita entregar {breakeven:.0f}% para no perder "
                    f"plata y está entregando {actual:.0f}%: {gap:.0f} puntos por debajo sobre "
                    f"{int(row['shipments']):,} despachos."
                    + (
                        f" Lleva {_money(impact, currency)} de contribución negativa"
                        f"{f' ({_money(per_shipment, currency)} por guía)' if per_shipment else ''}."
                        if impact
                        else " Todavía no arrastra pérdida acumulada, pero cada despacho "
                        "adicional la acerca."
                    )
                ),
                action=(
                    f"Exige confirmación telefónica antes de despachar {row['product_name']}, "
                    f"o súbele el precio hasta que el equilibrio baje de {actual:.0f}%."
                ),
                impact_amount=round(impact, 2) if impact else None,
                impact_currency=currency,
                deep_link=_link(row["country_code"], "efectividad"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# -----------------------------------------------------------------------------
# 3. Preparation slower than the operation's own p90
# -----------------------------------------------------------------------------


def _detect_prep_above_own_p90(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """The merchant's own half of the delivery clock, measured against itself.

    Not against a sector benchmark - against this operation's own 90th
    percentile. "Slow" here means slow for you.
    """
    where, params = _country_filter("country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        WITH per_row AS (
            SELECT country_code, carrier_name, service_level, shipments,
                   p50_prep_days, p90_prep_days
            FROM mart.v_fulfillment_sla
            WHERE p50_prep_days IS NOT NULL
              AND p90_prep_days IS NOT NULL
              AND shipments >= %s
              AND {where}
        ),
        own_p90 AS (
            SELECT country_code,
                   round(sum(p90_prep_days * shipments) / NULLIF(sum(shipments), 0), 1)
                       AS p90_days
            FROM per_row GROUP BY country_code
        ),
        per_guide_value AS (
            SELECT country_code,
                   sum(revenue) / NULLIF(sum(shipments), 0) AS revenue_per_shipment,
                   min(currency_code)                       AS currency_code
            FROM mart.v_daily_contribution
            WHERE day >= CURRENT_DATE - 90
            GROUP BY country_code
        )
        SELECT r.country_code, r.carrier_name, r.service_level, r.shipments,
               r.p50_prep_days, o.p90_days, v.revenue_per_shipment, v.currency_code
        FROM per_row r
        JOIN own_p90 o ON o.country_code = r.country_code
        LEFT JOIN per_guide_value v ON v.country_code = r.country_code
        WHERE o.p90_days IS NOT NULL AND r.p50_prep_days > o.p90_days
        """,
        (MIN_SHIPMENTS_PREP, *params),
    )

    out: list[Recommendation] = []
    for row in rows:
        p50 = _num(row["p50_prep_days"]) or 0.0
        own = _num(row["p90_days"]) or 0.0
        shipments = int(row["shipments"] or 0)
        per_guide = _num(row["revenue_per_shipment"])
        currency = row["currency_code"]

        # Money impact is not a loss, it is a DELAY: this much recaudo moves
        # later because these guides left later. Said that way in the finding.
        impact = per_guide * shipments if per_guide else None
        extra_days = p50 - own

        out.append(
            Recommendation(
                code="prep_above_own_p90",
                severity="warning",
                country_code=row["country_code"],
                title=f"Alistamiento lento en {row['carrier_name']} ({row['service_level']})",
                finding=(
                    f"Estas guías tardan {p50:.1f} días en salir, contra tu propio p90 de "
                    f"{own:.1f} días: {extra_days:.1f} días extra sobre {shipments:,} guías. "
                    f"Esa demora es tuya, no de la transportadora."
                    + (
                        f" Aplaza {_money(impact, currency)} de recaudo."
                        if impact
                        else ""
                    )
                ),
                action=(
                    f"Revisa por qué {row['carrier_name']} recoge tarde en el servicio "
                    f"{row['service_level']}: casi siempre es una relación de despacho que "
                    f"se cierra un día después de crear las guías."
                ),
                impact_amount=round(impact, 2) if impact else None,
                impact_currency=currency,
                deep_link=_link(row["country_code"], "logistica"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# -----------------------------------------------------------------------------
# 4. Value stuck in the office queue, still recoverable
# -----------------------------------------------------------------------------


def _detect_office_queue_recoverable(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """The 8-21 day band: old enough to be forgotten, young enough to rescue.

    Under eight days it is still normal. Past twenty-one the parcel usually goes
    back on its own and the call is wasted.
    """
    where, params = _country_filter("country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        WITH bands AS (
            SELECT country_code, carrier_name, city_name,
                   (aging_8_14 + urgent_15_21)     AS shipments_band,
                   value_still_recoverable,
                   currency_code
            FROM mart.v_office_rescue
            WHERE value_still_recoverable IS NOT NULL
              AND value_still_recoverable > 0
              AND {where}
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                       PARTITION BY country_code, carrier_name
                       ORDER BY value_still_recoverable DESC) AS rn
            FROM bands
        )
        SELECT country_code,
               carrier_name,
               sum(shipments_band)                         AS shipments_band,
               sum(value_still_recoverable)                AS value_still_recoverable,
               min(currency_code)                          AS currency_code,
               min(city_name) FILTER (WHERE rn = 1)        AS top_city
        FROM ranked
        GROUP BY country_code, carrier_name
        HAVING sum(shipments_band) >= %s
        """,
        (*params, MIN_SHIPMENTS_OFFICE),
    )

    out: list[Recommendation] = []
    for row in rows:
        value = _num(row["value_still_recoverable"])
        shipments = int(row["shipments_band"] or 0)
        currency = row["currency_code"]

        out.append(
            Recommendation(
                code="office_queue_recoverable",
                severity="warning",
                country_code=row["country_code"],
                title=f"{shipments:,} guías esperando en oficinas de {row['carrier_name']}",
                finding=(
                    f"Hay {_money(value, currency)} en {shipments:,} guías que llevan entre 8 y "
                    f"21 días en oficina de {row['carrier_name']}"
                    + (f", concentradas en {row['top_city']}" if row["top_city"] else "")
                    + ". Ni entregadas ni devueltas: pasados 21 días se devuelven solas y "
                    "pagas el flete de vuelta."
                ),
                action=(
                    f"Llama hoy a esos {shipments:,} clientes con la dirección de la oficina "
                    f"y una fecha límite. Es el recaudo más barato que tienes disponible."
                ),
                impact_amount=round(value, 2) if value else None,
                impact_currency=currency,
                deep_link=_link(row["country_code"], "logistica"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# -----------------------------------------------------------------------------
# 5. A carrier charging materially more per kilo than the rest
# -----------------------------------------------------------------------------


def _detect_carrier_freight_premium(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """Per kilo, so a carrier is not "expensive" for carrying heavier parcels.

    The benchmark deliberately EXCLUDES the carrier being judged: comparing a
    carrier against an average it dominates just compares it to itself.
    """
    where, params = _country_filter("country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        WITH per_carrier AS (
            SELECT country_code, carrier_name,
                   sum(shipments)       AS shipments,
                   sum(freight_total)   AS freight_total,
                   sum(total_weight_kg) AS weight_kg,
                   min(currency_code)   AS currency_code
            FROM mart.v_freight_analysis
            WHERE total_weight_kg IS NOT NULL AND freight_total IS NOT NULL
              AND {where}
            GROUP BY country_code, carrier_name
            HAVING sum(total_weight_kg) > 0 AND sum(shipments) >= %s
        ),
        country_total AS (
            SELECT country_code,
                   sum(freight_total) AS freight_total,
                   sum(weight_kg)     AS weight_kg
            FROM per_carrier GROUP BY country_code
        )
        SELECT c.country_code, c.carrier_name, c.shipments, c.weight_kg, c.currency_code,
               round(c.freight_total / NULLIF(c.weight_kg, 0), 2)  AS freight_per_kg,
               round((t.freight_total - c.freight_total)
                     / NULLIF(t.weight_kg - c.weight_kg, 0), 2)    AS peers_per_kg
        FROM per_carrier c
        JOIN country_total t ON t.country_code = c.country_code
        WHERE t.weight_kg > c.weight_kg
        """,
        (*params, MIN_SHIPMENTS_CARRIER),
    )

    out: list[Recommendation] = []
    for row in rows:
        mine = _num(row["freight_per_kg"])
        peers = _num(row["peers_per_kg"])
        weight = _num(row["weight_kg"]) or 0.0
        currency = row["currency_code"]

        if not mine or not peers or peers <= 0 or mine <= peers * FREIGHT_PREMIUM_RATIO:
            continue

        premium_pct = (mine / peers - 1) * 100
        impact = (mine - peers) * weight

        out.append(
            Recommendation(
                code="carrier_freight_premium",
                severity="warning",
                country_code=row["country_code"],
                title=f"{row['carrier_name']} te cobra más caro el kilo",
                finding=(
                    f"{row['carrier_name']} cobra {_money(mine, currency)} por kilo contra "
                    f"{_money(peers, currency)} de las demás: {premium_pct:.0f}% más sobre "
                    f"{weight:,.0f} kg movidos. La diferencia son "
                    f"{_money(impact, currency)} de flete."
                ),
                action=(
                    f"Lleva ese número a la negociación con {row['carrier_name']}: pide igualar "
                    f"{_money(peers, currency)} por kilo o mueve el volumen a la que ya lo cobra."
                ),
                impact_amount=round(impact, 2),
                impact_currency=currency,
                deep_link=_link(row["country_code"], "logistica"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# -----------------------------------------------------------------------------
# 6. Cash in transit above what this operation's own cycle predicts
# -----------------------------------------------------------------------------


def _detect_cash_in_transit_high(
    conn: psycopg.Connection, country_code: str | None
) -> list[Recommendation]:
    """Delivered and collected by the carrier, but not settled to the merchant.

    "Normal" is this operation's own arithmetic: daily collected revenue times
    its own median days-to-cash. Anything much above that is money the carrier
    is holding longer than it usually does.
    """
    where, params = _country_filter("cc.country_code", country_code)
    rows = fetch_all(
        conn,
        f"""
        WITH daily AS (
            SELECT country_code,
                   sum(revenue)          AS revenue,
                   count(DISTINCT day)   AS days
            FROM mart.v_daily_contribution
            WHERE day >= CURRENT_DATE - 90
            GROUP BY country_code
        )
        SELECT cc.country_code, cc.cash_in_transit, cc.delivered_unsettled,
               cc.p50_days_to_cash, cc.currency_code,
               round(d.revenue / NULLIF(d.days, 0) * cc.p50_days_to_cash, 2)
                   AS expected_in_transit
        FROM mart.v_cash_cycle cc
        JOIN daily d ON d.country_code = cc.country_code
        WHERE cc.cash_in_transit IS NOT NULL
          AND cc.p50_days_to_cash IS NOT NULL
          AND {where}
        """,
        params,
    )

    out: list[Recommendation] = []
    for row in rows:
        actual = _num(row["cash_in_transit"]) or 0.0
        expected = _num(row["expected_in_transit"])
        p50 = _num(row["p50_days_to_cash"]) or 0.0
        pending = int(row["delivered_unsettled"] or 0)
        currency = row["currency_code"]

        if not expected or expected <= 0 or actual <= expected * CASH_IN_TRANSIT_RATIO:
            continue

        excess = actual - expected
        out.append(
            Recommendation(
                code="cash_in_transit_high",
                severity="warning",
                country_code=row["country_code"],
                title="Tienes más plata retenida de lo normal",
                finding=(
                    f"Hay {_money(actual, currency)} entregados y sin consignar en "
                    f"{pending:,} guías. Con tu ciclo normal de {p50:.0f} días deberían ser "
                    f"unos {_money(expected, currency)}: hay {_money(excess, currency)} de más "
                    f"esperando."
                ),
                action=(
                    "Pide a la transportadora la relación de liquidaciones pendientes y "
                    "concilia contra tus guías entregadas de esta semana."
                ),
                impact_amount=round(excess, 2),
                impact_currency=currency,
                deep_link=_link(row["country_code"], "finanzas"),
                detected_at=datetime.now(UTC),
            )
        )
    return out


# =============================================================================
# After ingestion
# =============================================================================


def refresh_after_batch(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str | None
) -> dict[str, Any]:
    """Re-derive what is normal, and re-detect, right after a batch lands.

    NO NETWORK CALL HAPPENS HERE. The ingestion path is allowed to depend on the
    database and on nothing else - a file upload that fails because a model was
    unreachable is a broken product.

    Never raises: this runs as a side effect of a successful ingestion, and a
    successful ingestion must stay successful.
    """
    from ai.alerts import persist_findings
    from ai.memory import infer_thresholds

    result: dict[str, Any] = {"thresholds": 0, "recommendations": 0, "notified": 0}
    try:
        if country_code:
            result["thresholds"] = len(infer_thresholds(conn, tenant_id, country_code))
        found = detect(conn, country_code)
        result["recommendations"] = len(found)
        if found:
            logger.info(
                "post-ingestion: %d recommendations for tenant %s (%s): %s",
                len(found), tenant_id, country_code or "todos",
                ", ".join(sorted({item["code"] for item in found})),
            )
        # Only the critical ones go out right now; warnings wait for the 7 am
        # digest. The fingerprint window keeps a repeated load from repeating
        # the notification.
        critical = [item for item in found if item["severity"] == "critical"]
        if critical:
            result["notified"] = len(
                persist_findings(conn, tenant_id, country_code, critical, kind="urgent")
            )
    except Exception:
        logger.warning(
            "post-ingestion recommendation refresh failed for tenant %s", tenant_id,
            exc_info=True,
        )
    return result


# =============================================================================
# Optional narrative. Everything above works without any of this.
# =============================================================================

NARRATOR_SYSTEM_PROMPT = """Eres un analista de operaciones de ecommerce contraentrega (COD)
en Latinoamérica. Te entrego hallazgos que YA fueron detectados y cuantificados por consultas
SQL deterministas. Tu único trabajo es hilarlos en un párrafo para el dueño de la operación.

REGLAS:
- Un solo párrafo, entre 3 y 5 frases. Sin listas, sin encabezados, sin emojis.
- NO inventes hallazgos, NO cambies las cifras, NO agregues problemas que no estén abajo.
- Si dos hallazgos son la misma historia, dilo: por ejemplo, alistamiento lento y plata
  retenida suelen ser el mismo problema visto dos veces.
- Di por cuál empezar y por qué, en dinero.
- Español latinoamericano, sobrio y directo. Sin entusiasmo, sin felicitaciones.
- Nunca digas "ventas": en contraentrega una venta no es dinero hasta que se entrega.
  Di despachos, entregas, recaudo o contribución.
"""


def narrate(
    conn: psycopg.Connection,
    settings: Settings,
    tenant_id: UUID,
    country_code: str | None,
    found: list[dict[str, Any]],
) -> str | None:
    """One paragraph over findings the SQL already made. Cached and budgeted.

    Returns None when there is nothing to narrate. Raises `AiUnavailable` when
    the model cannot answer - the caller shows the recommendations anyway,
    because the recommendations never needed the model.
    """
    import json

    from ai.client import (
        call_llm,
        check_budget,
        context_hash,
        read_cache,
        record_usage,
        write_cache,
    )
    from ai.memory import recall_block

    if not found:
        return None

    # Hash only the parts that change the story: same findings, same paragraph.
    fingerprint = [
        {"code": item["code"], "title": item["title"], "impact": item["impact_amount"]}
        for item in found
    ]
    cache_key = context_hash("recommendations", tenant_id, country_code, fingerprint)
    cached = read_cache(conn, cache_key)
    if cached:
        return cached.get("narrative")

    check_budget(conn, tenant_id, settings)

    memory = recall_block(conn, tenant_id, country_code)
    system = f"{NARRATOR_SYSTEM_PROMPT}\n\n{memory}" if memory else NARRATOR_SYSTEM_PROMPT

    payload = json.dumps(
        [
            {
                "hallazgo": item["finding"],
                "accion": item["action"],
                "impacto": item["impact_amount"],
                "moneda": item["impact_currency"],
                "severidad": item["severity"],
            }
            for item in found
        ],
        ensure_ascii=False,
        indent=1,
        default=str,
    )

    response = call_llm(
        settings,
        system=system,
        user_message=f"Hallazgos detectados por SQL:\n{payload}\n\nEscribe el párrafo.",
        max_tokens=500,
    )
    record_usage(conn, tenant_id, "recommendations", response)

    write_cache(
        conn,
        cache_key=cache_key,
        tenant_id=tenant_id,
        feature="recommendations",
        country=country_code,
        payload={"narrative": response.text},
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        ttl_hours=2,
    )
    return response.text
