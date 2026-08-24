"""Decisions, not dashboards: keep or cut, switch or stay, call or wait.

The KPI screens say what happened. This module says what to DO, in the four
places the operator decides every day:

    products   keep / cut / watch      against the product's own break-even
    carriers   switch / ok             the best carrier in each city, by data
    office     call / hold             which parcels a phone call still rescues
    cash       watch / ok              money in transit against the usual cycle

EVERY VERDICT IS ARITHMETIC over a mart view, the same way `ai/recommendations`
detects. There is no model in this file. The thresholds it compares against
come from `ai/memory` - the operation's own normals, inferred or set by hand -
and the margins (five points, eight points, twenty percent) are written here
where they can be argued with.

The optional paragraph on top is the only place a model appears, and it is
handed the verdicts after they are made. Switch it off and nothing below
changes.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from ai.recommendations import _link, _money
from api.db import fetch_all

logger = logging.getLogger(__name__)

SCOPES = ("products", "carriers", "office", "cash")

# Products: within this many points of break-even a product is "on the line",
# and one lucky week would flip the verdict. Outside it, the verdict is stable.
BREAKEVEN_BAND_PTS = 5.0
MIN_SHIPMENTS_PRODUCT = 20

# Carriers: a city needs this many resolved guides per carrier before its
# delivery rate means anything, and the gap must be this wide to justify moving
# volume - switching carriers has a cost that a two-point difference never pays.
MIN_TERMINAL_ZONE = 30
SWITCH_GAP_PTS = 8.0

# Cash: p50 more than a fifth above the operation's own typical cycle means the
# carrier is settling slower than it normally does.
CASH_SLOW_RATIO = 1.20

MAX_ITEMS = 50


def build_decisions(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str, scope: str
) -> dict[str, Any]:
    """Every verdict for one scope. `conn` must already be scoped to the tenant."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    country = country_code.upper()
    thresholds = _thresholds(conn, tenant_id, country)

    builder = {
        "products": _products,
        "carriers": _carriers,
        "office": _office,
        "cash": _cash,
    }[scope]
    items = builder(conn, country, thresholds)
    return {"items": items[:MAX_ITEMS], "thresholds": thresholds}


# -----------------------------------------------------------------------------
# Thresholds: the operation's own normals, as numbers
# -----------------------------------------------------------------------------


def _thresholds(
    conn: psycopg.Connection, tenant_id: UUID, country: str
) -> dict[str, dict[str, Any]]:
    """`{key: {value, number, source}}` for the four known keys.

    A threshold is stored as a sentence ("Tu ciclo de caja normal en CO es 12
    días...") so the copilot can quote it. The decisions need the number, so it
    is pulled out here; a user-set value is usually just the number.
    """
    from ai.memory import recall

    out: dict[str, dict[str, Any]] = {}
    for fact in recall(conn, tenant_id, country, limit=40):
        if fact["kind"] != "threshold" or fact["country_code"] != country:
            continue
        out[fact["key"]] = {
            "value": fact["value"],
            "number": threshold_number(fact["value"]),
            "source": fact["source"],
        }
    return out


_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,](\d+))?")


def threshold_number(text: str | None) -> float | None:
    """The first number in a threshold sentence, or None.

    Prefers the number right after "es " because that is how the inferred
    sentences are written ("... es 62.3% ..."). Thousands separators in either
    convention are dropped; a trailing decimal part in either is kept.
    """
    if not text:
        return None
    candidates = [text.split(" es ", 1)[1]] if " es " in text else []
    candidates.append(text)
    for chunk in candidates:
        match = _NUMBER.search(chunk)
        if not match:
            continue
        whole, fraction = match.group(1), match.group(2)
        whole = re.sub(r"[.,]", "", whole)
        try:
            return float(f"{whole}.{fraction}" if fraction else whole)
        except ValueError:
            continue
    return None


# -----------------------------------------------------------------------------
# products: keep / cut / watch
# -----------------------------------------------------------------------------


def _products(
    conn: psycopg.Connection, country: str, thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT product_id, product_name, sku, shipments, delivered, delivery_rate_pct,
               breakeven_delivery_pct, net_contribution, contribution_per_shipment,
               currency_code
        FROM mart.v_dropshipping_margin
        WHERE country_code = %s
          AND shipments >= %s
          AND breakeven_delivery_pct IS NOT NULL
          AND delivery_rate_pct IS NOT NULL
        ORDER BY net_contribution ASC NULLS LAST
        """,
        (country, MIN_SHIPMENTS_PRODUCT),
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        rate = _num(row["delivery_rate_pct"]) or 0.0
        breakeven = _num(row["breakeven_delivery_pct"]) or 0.0
        contribution = _num(row["net_contribution"])
        currency = row["currency_code"]
        gap = rate - breakeven
        name = row["product_name"]

        if gap < -BREAKEVEN_BAND_PTS:
            verdict = "cut"
            headline = (
                f"{name} entrega {rate:.0f}% y necesita {breakeven:.0f}%: pierde plata en "
                f"cada despacho. Exige confirmación o súbele el precio."
            )
        elif gap > BREAKEVEN_BAND_PTS:
            verdict = "keep"
            headline = (
                f"{name} entrega {rate:.0f}%, {gap:.0f} puntos sobre su equilibrio de "
                f"{breakeven:.0f}%. Sigue."
            )
        else:
            verdict = "watch"
            headline = (
                f"{name} está sobre la línea: entrega {rate:.0f}% contra {breakeven:.0f}% "
                f"de equilibrio. Una semana mala lo pone a perder."
            )

        # The loss already booked. Positive contribution below break-even means
        # the two numbers cover different periods, so it is not an impact.
        impact = abs(contribution) if contribution is not None and contribution < 0 else None

        items.append(
            _item(
                key=str(row["product_id"]) if row["product_id"] else f"name:{name}",
                label=name,
                verdict=verdict,
                headline=headline,
                numbers={
                    "product_id": str(row["product_id"]) if row["product_id"] else None,
                    "sku": row["sku"],
                    "shipments": int(row["shipments"] or 0),
                    "delivered": int(row["delivered"] or 0),
                    "delivery_rate_pct": rate,
                    "breakeven_delivery_pct": breakeven,
                    "gap_pts": round(gap, 1),
                    "net_contribution": contribution,
                    "contribution_per_shipment": _num(row["contribution_per_shipment"]),
                    "currency_code": currency,
                },
                impact=impact,
                currency=currency,
                deep_link=f"/{country.lower()}/products",
            )
        )

    order = {"cut": 0, "watch": 1, "keep": 2}
    items.sort(key=lambda it: (order[it["verdict"]], -(it["impact_amount"] or 0)))
    return items


# -----------------------------------------------------------------------------
# carriers: switch / ok, per zone
# -----------------------------------------------------------------------------


def _carriers(
    conn: psycopg.Connection, country: str, thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT level1_name, city_name, carrier_id, carrier_name, shipments, terminal,
               delivery_rate_pct, avg_days_to_deliver, avg_freight, delivered_value,
               currency_code
        FROM mart.v_carrier_by_zone
        WHERE country_code = %s
        ORDER BY level1_name, city_name, shipments DESC
        """,
        (country,),
    )

    zones: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        zones[(row["level1_name"], row["city_name"])].append(row)

    items: list[dict[str, Any]] = []
    for (level1, city), carriers in zones.items():
        # The carrier that carries the most is the one the operator is using.
        current = max(carriers, key=lambda r: int(r["shipments"] or 0))
        eligible = [
            r for r in carriers
            if int(r["terminal"] or 0) >= MIN_TERMINAL_ZONE and r["delivery_rate_pct"] is not None
        ]
        if not eligible:
            continue
        best = max(
            eligible,
            key=lambda r: (_num(r["delivery_rate_pct"]) or 0.0, int(r["terminal"] or 0)),
        )

        best_rate = _num(best["delivery_rate_pct"]) or 0.0
        current_rate = _num(current["delivery_rate_pct"])
        freight_delta = _delta(_num(best["avg_freight"]), _num(current["avg_freight"]))
        currency = current["currency_code"] or best["currency_code"]

        is_switch = (
            best["carrier_name"] != current["carrier_name"]
            and current_rate is not None
            and best_rate - current_rate >= SWITCH_GAP_PTS
        )

        impact = None
        if is_switch and current_rate is not None:
            # Extra deliveries the better carrier would have made on the volume
            # the current one moved, valued at what a delivered guide is worth.
            terminal = int(current["terminal"] or 0)
            delivered = int(round(terminal * current_rate / 100))
            value = _num(current["delivered_value"])
            per_guide = value / delivered if value and delivered else None
            extra = terminal * (best_rate - current_rate) / 100
            impact = round(extra * per_guide, 2) if per_guide else None

        if is_switch:
            verdict = "switch"
            headline = (
                f"En {city} {best['carrier_name']} entrega {best_rate:.0f}% y "
                f"{current['carrier_name']} {current_rate:.0f}%: mueve el volumen."
                + (
                    f" El flete cambia {_money(freight_delta, currency)} por guía."
                    if freight_delta
                    else ""
                )
            )
        else:
            verdict = "ok"
            headline = (
                f"En {city} la mejor es {best['carrier_name']} ({best_rate:.0f}%)"
                + (
                    " y ya es la que más usas."
                    if best["carrier_name"] == current["carrier_name"]
                    else f"; {current['carrier_name']} no está lejos."
                )
            )

        items.append(
            _item(
                key=f"{level1}|{city}",
                label=f"{city}, {level1}" if level1 and level1 != "Sin dato" else city,
                verdict=verdict,
                headline=headline,
                numbers={
                    "level1_name": level1,
                    "city_name": city,
                    "best_carrier": best["carrier_name"],
                    "current_carrier": current["carrier_name"],
                    "best_rate": best_rate,
                    "current_rate": current_rate,
                    "freight_delta": freight_delta,
                    "best_terminal": int(best["terminal"] or 0),
                    "current_terminal": int(current["terminal"] or 0),
                    "shipments": sum(int(r["shipments"] or 0) for r in carriers),
                    "carriers": len(carriers),
                    "currency_code": currency,
                },
                impact=impact,
                currency=currency,
                deep_link=_link(country, "logistica"),
            )
        )

    items.sort(
        key=lambda it: (
            0 if it["verdict"] == "switch" else 1,
            -(it["impact_amount"] or 0),
            -it["numbers"]["shipments"],
        )
    )
    return items


# -----------------------------------------------------------------------------
# office: call / hold
# -----------------------------------------------------------------------------


def _office(
    conn: psycopg.Connection, country: str, thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT carrier_name, level1_name, city_name, shipments, value_waiting,
               avg_days_waiting, fresh_0_7, aging_8_14, urgent_15_21, probably_lost,
               value_still_recoverable, currency_code
        FROM mart.v_office_rescue
        WHERE country_code = %s AND shipments > 0
        ORDER BY value_still_recoverable DESC NULLS LAST, value_waiting DESC NULLS LAST
        """,
        (country,),
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        band = int(row["aging_8_14"] or 0) + int(row["urgent_15_21"] or 0)
        fresh = int(row["fresh_0_7"] or 0)
        lost = int(row["probably_lost"] or 0)
        recoverable = _num(row["value_still_recoverable"])
        currency = row["currency_code"]
        carrier, city = row["carrier_name"], row["city_name"]

        if band > 0:
            verdict = "call"
            headline = (
                f"Llama hoy: {band} guía{'s' if band != 1 else ''} de {carrier} en {city} "
                f"llevan entre 8 y 21 días en oficina"
                + (f", {_money(recoverable, currency)} todavía rescatables." if recoverable else ".")
            )
        else:
            verdict = "hold"
            headline = (
                f"{fresh} guía{'s' if fresh != 1 else ''} de {carrier} en {city} "
                f"llevan menos de una semana: todavía es normal, espera."
                if fresh
                else (
                    f"{lost} guía{'s' if lost != 1 else ''} de {carrier} en {city} pasaron "
                    f"de 21 días: casi seguro vuelven solas."
                )
            )

        items.append(
            _item(
                key=f"{carrier}|{city}",
                label=f"{carrier} · {city}",
                verdict=verdict,
                headline=headline,
                numbers={
                    "carrier_name": carrier,
                    "level1_name": row["level1_name"],
                    "city_name": city,
                    "shipments": int(row["shipments"] or 0),
                    "value_waiting": _num(row["value_waiting"]),
                    "value_still_recoverable": recoverable,
                    "avg_days_waiting": _num(row["avg_days_waiting"]),
                    "fresh_0_7": fresh,
                    "aging_8_14": int(row["aging_8_14"] or 0),
                    "urgent_15_21": int(row["urgent_15_21"] or 0),
                    "probably_lost": lost,
                    "currency_code": currency,
                },
                impact=recoverable if band > 0 else None,
                currency=currency,
                deep_link=_link(country, "logistica"),
            )
        )

    items.sort(
        key=lambda it: (0 if it["verdict"] == "call" else 1, -(it["impact_amount"] or 0))
    )
    return items


# -----------------------------------------------------------------------------
# cash: watch / ok
# -----------------------------------------------------------------------------


def _cash(
    conn: psycopg.Connection, country: str, thresholds: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT settled, delivered_unsettled, p50_days_to_cash, p90_days_to_cash,
               cash_in_transit, currency_code
        FROM mart.v_cash_cycle
        WHERE country_code = %s
        """,
        (country,),
    )
    if not rows:
        return []
    row = rows[0]

    in_transit = _num(row["cash_in_transit"]) or 0.0
    p50 = _num(row["p50_days_to_cash"])
    p90 = _num(row["p90_days_to_cash"])
    pending = int(row["delivered_unsettled"] or 0)
    currency = row["currency_code"]
    typical = (thresholds.get("dias_a_caja_tipicos") or {}).get("number")

    if p50 is None:
        verdict = "ok"
        headline = (
            f"Tienes {_money(in_transit, currency)} entregados y sin consignar en "
            f"{pending:,} guías. Todavía no hay suficientes liquidaciones para saber "
            f"cuándo llegan."
        )
    else:
        arrives = f"Te llegan ~{_money(in_transit, currency)} en {p50:.0f} días"
        if p90 is not None:
            arrives += f" (el 10% más lento, {p90:.0f})"
        if typical and p50 > typical * CASH_SLOW_RATIO:
            verdict = "watch"
            headline = (
                f"{arrives}. Tu ciclo normal es {typical:.0f}: la transportadora está "
                f"consignando más lento. Pide la relación de liquidaciones pendientes."
            )
        else:
            verdict = "ok"
            headline = f"{arrives}, dentro de tu ciclo normal."

    return [
        _item(
            key="cash",
            label="Caja",
            verdict=verdict,
            headline=headline,
            numbers={
                "cash_in_transit": in_transit,
                "delivered_unsettled": pending,
                "settled": int(row["settled"] or 0),
                "p50": p50,
                "p90": p90,
                "typical_days": typical,
                "currency_code": currency,
            },
            impact=in_transit or None,
            currency=currency,
            deep_link=_link(country, "finanzas"),
        )
    ]


# -----------------------------------------------------------------------------
# Shared
# -----------------------------------------------------------------------------


def _item(
    *,
    key: str,
    label: str,
    verdict: str,
    headline: str,
    numbers: dict[str, Any],
    impact: float | None,
    currency: str | None,
    deep_link: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "verdict": verdict,
        "headline": headline,
        "numbers": numbers,
        "impact_amount": round(impact, 2) if impact is not None else None,
        "impact_currency": currency if impact is not None else None,
        "deep_link": deep_link,
    }


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 2)


# The verdicts as the narrator's findings: it expects the shape `detect()`
# produces, so each decision is rephrased into one.
_VERDICT_SEVERITY = {
    "cut": "critical", "call": "critical", "switch": "warning", "watch": "warning",
    "hold": "info", "keep": "info", "ok": "info",
}
_VERDICT_ACTION = {
    "cut": "Deja de despacharlo sin confirmación o súbele el precio.",
    "keep": "Sigue como va.",
    "watch": "Revísalo la próxima semana antes de decidir.",
    "switch": "Mueve el volumen de esta zona a la mejor transportadora.",
    "call": "Llama a estos clientes hoy.",
    "hold": "Espera; todavía es normal.",
    "ok": "Nada que hacer aquí.",
}


def as_findings(scope: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": f"decision_{scope}_{item['verdict']}",
            "severity": _VERDICT_SEVERITY[item["verdict"]],
            "title": item["label"],
            "finding": item["headline"],
            "action": _VERDICT_ACTION[item["verdict"]],
            "impact_amount": item["impact_amount"],
            "impact_currency": item["impact_currency"],
        }
        for item in items
    ]
