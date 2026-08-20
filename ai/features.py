"""The three AI features: daily brief, alerts, and ask-your-data.

What the model is allowed to do here is deliberately small:

* BRIEF - it writes prose from aggregates. It never sees a row, never sees a
  customer hash, never sees a tracking number.
* ALERTS - the detections are SQL (`mart.v_alert_signals`). The model phrases
  them and nothing else. A model that decides what counts as a problem invents
  problems.
* ASK - it writes SQL, which is then parsed, vetted, and executed as a read-only
  role. Then it writes prose from the result.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from ai.client import (
    AiUnavailable,
    call_llm,
    check_budget,
    context_hash,
    read_cache,
    record_usage,
    write_cache,
)
from ai.nl2sql import (
    MAX_ROWS,
    REJECTION_SUGGESTIONS,
    STATEMENT_TIMEOUT_MS,
    SYSTEM_PROMPT,
    validate_sql,
)
from api.db import fetch_all, fetch_one, get_readonly_pool
from api.settings import Settings

logger = logging.getLogger(__name__)

BRIEF_TTL_HOURS = 24
ALERTS_TTL_HOURS = 2
ASK_TTL_HOURS = 1

ANALYST_SYSTEM_PROMPT = """Eres un analista de operaciones de ecommerce contraentrega (COD)
en Latinoamérica. Escribes para el dueño de la operación, que no es técnico y tiene 30 segundos.

CÓMO ESCRIBES:
- Entre 3 y 5 frases. Nada de listas, nada de encabezados, nada de emojis.
- Cada afirmación va con su cifra. "La efectividad cayó" no sirve; "la efectividad cayó de
  71% a 63%" sí.
- Cuando puedas, traduce el problema a dinero.
- Máximo UNA recomendación, al final, concreta y accionable.
- Español latinoamericano, tono sobrio y directo. Sin entusiasmo, sin felicitaciones.

VOCABULARIO OBLIGATORIO:
- Nunca digas "ventas". En contraentrega una venta no es dinero hasta que se entrega.
  Di "despachos", "entregas", "recaudo" o "contribución".
- "Devolución" es una pérdida real: se pagó flete de ida y de vuelta sin recaudar nada.

QUÉ NO HACER:
- No inventes cifras que no estén en los datos que te doy.
- Si los datos son escasos, dilo en una frase en vez de rellenar.
- No repitas los números en el mismo orden en que te los di: cuenta qué significan.
"""


def _decimal_to_float(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _decimal_to_float(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decimal_to_float(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# =============================================================================
# Daily brief
# =============================================================================


def build_brief_context(conn: psycopg.Connection, country: str) -> dict[str, Any]:
    """Aggregates only. No rows, no identifiers, no PII - by construction."""
    recent = fetch_all(
        conn,
        """
        SELECT day, shipments, delivered, returned, in_transit, revenue, contribution,
               delivery_rate_terminal_pct, ad_spend, ad_spend_missing, currency_code
        FROM mart.v_daily_contribution
        WHERE country_code = %s AND day >= CURRENT_DATE - 30
        ORDER BY day DESC LIMIT 30
        """,
        (country,),
    )
    carriers = fetch_all(
        conn,
        """
        SELECT carrier_name, shipments, delivery_rate_pct, return_rate_pct,
               avg_days_to_deliver, contribution
        FROM mart.v_carrier_effectiveness
        WHERE country_code = %s AND shipments >= 10
        ORDER BY shipments DESC LIMIT 6
        """,
        (country,),
    )
    products = fetch_all(
        conn,
        """
        SELECT product_name, shipments, delivery_rate_pct, margin_pct,
               contribution, contribution_per_shipment
        FROM mart.v_product_performance
        WHERE country_code = %s AND shipments >= 10
        ORDER BY contribution DESC NULLS LAST LIMIT 6
        """,
        (country,),
    )
    geo = fetch_all(
        conn,
        """
        SELECT level1_name, city_name, shipments, delivery_rate_pct, traffic_light
        FROM mart.v_geo_performance
        WHERE country_code = %s AND shipments >= 15
        ORDER BY shipments DESC LIMIT 8
        """,
        (country,),
    )
    aging = fetch_all(
        conn,
        "SELECT aging_bucket, shipments, value_at_risk FROM mart.v_aging "
        "WHERE country_code = %s ORDER BY bucket_order",
        (country,),
    )
    country_info = fetch_one(
        conn,
        "SELECT name, currency_code, currency_symbol, decimal_places FROM core.country "
        "WHERE code = %s",
        (country,),
    )

    return _decimal_to_float(
        {
            "country": country_info or {"name": country},
            "last_30_days": recent,
            "carriers": carriers,
            "products": products,
            "geography": geo,
            "aging": aging,
        }
    )


def generate_brief(
    conn: psycopg.Connection, settings: Settings, tenant_id: UUID, country: str
) -> dict[str, Any]:
    context = build_brief_context(conn, country)

    if not context["last_30_days"]:
        return {
            "summary": (
                "Todavía no hay guías cargadas en este país en los últimos 30 días. "
                "Sube tu primer reporte en la pantalla de Cargar datos y el resumen "
                "aparecerá mañana."
            ),
            "cached": False,
            "generated_at": datetime.now(UTC),
            "degraded": True,
            "degraded_reason": "sin_datos",
        }

    cache_key = context_hash("brief", tenant_id, country, context)
    cached = read_cache(conn, cache_key)
    if cached:
        return {**cached, "cached": True}

    check_budget(conn, tenant_id, settings)

    response = call_llm(
        settings,
        system=ANALYST_SYSTEM_PROMPT,
        user_message=(
            f"Estos son los datos de {context['country'].get('name', country)} "
            f"de los últimos 30 días. Escribe el resumen del día.\n\n"
            f"{_as_readable(context)}"
        ),
        max_tokens=600,
    )
    record_usage(conn, tenant_id, "brief", response)

    payload = {
        "summary": response.text,
        "generated_at": datetime.now(UTC).isoformat(),
        "degraded": False,
        "degraded_reason": None,
    }
    write_cache(
        conn, cache_key=cache_key, tenant_id=tenant_id, feature="brief", country=country,
        payload=payload, model=response.model, input_tokens=response.input_tokens,
        output_tokens=response.output_tokens, ttl_hours=BRIEF_TTL_HOURS,
    )
    return {**payload, "cached": False}


# =============================================================================
# Alerts
# =============================================================================

ALERT_TEMPLATES: dict[str, dict[str, str]] = {
    "carrier_delivery_drop": {
        "title": "Caída de efectividad en {subject}",
        "finding": (
            "{subject} entregó {current_value}% en los últimos 7 días, contra "
            "{baseline_value}% en su promedio de 30 días: {delta_points} puntos menos "
            "sobre {volume} guías resueltas."
        ),
        "action": "Revisa las guías con novedad de {subject} y considera redistribuir volumen.",
    },
    "product_negative_contribution": {
        "title": "{subject} está perdiendo plata",
        "finding": (
            "{subject} deja {current_value} de contribución por guía despachada "
            "sobre {volume} guías. Cada despacho adicional aumenta la pérdida."
        ),
        "action": "Sube el precio, renegocia el costo, o deja de pautarlo.",
    },
    "geo_red_zone": {
        "title": "{subject} en rojo",
        "finding": (
            "{subject} entrega {current_value}% sobre {volume} guías, "
            "{delta_points} puntos por debajo del umbral de 65%."
        ),
        "action": "Exige confirmación previa en esa zona o deja de despachar ahí.",
    },
    "aging_13_plus": {
        "title": "Guías estancadas de 13+ días",
        "finding": (
            "Hay {volume} guías abiertas con más de 13 días, con {impact_amount} "
            "en valor declarado en riesgo."
        ),
        "action": "Pide gestión de novedades sobre esas guías antes de que se devuelvan.",
    },
    "connection_stale": {
        "title": "Conexión sin sincronizar: {subject}",
        "finding": (
            "{subject} lleva {current_value} horas sin sincronizar. Los números que ves "
            "pueden estar desactualizados."
        ),
        "action": "Revisa la conexión en Configuración y vuelve a autorizarla si hace falta.",
    },
}


def collect_alerts(
    conn: psycopg.Connection, country: str | None = None
) -> list[dict[str, Any]]:
    """Deterministic detection. No model involved in deciding what is a problem."""
    query = "SELECT * FROM mart.v_alert_signals"
    params: tuple = ()
    if country:
        query += " WHERE country_code = %s"
        params = (country.upper(),)
    query += " ORDER BY severity DESC, impact_amount DESC NULLS LAST LIMIT 20"

    signals = fetch_all(conn, query, params)
    currencies = {
        row["country_code"]: row["currency_code"]
        for row in fetch_all(conn, "SELECT code AS country_code, currency_code FROM core.country")
    }

    alerts: list[dict[str, Any]] = []
    for signal in signals:
        template = ALERT_TEMPLATES.get(signal["code"])
        if template is None:
            continue
        values = _decimal_to_float(dict(signal))
        values["impact_amount"] = _format_amount(
            signal["impact_amount"], currencies.get(signal["country_code"], "")
        )
        alerts.append(
            {
                "code": signal["code"],
                "severity": signal["severity"],
                "title": template["title"].format(**values),
                "finding": template["finding"].format(**values),
                "impact_amount": signal["impact_amount"],
                "impact_currency": currencies.get(signal["country_code"]),
                "action": template["action"].format(**values),
                "deep_link": signal["deep_link"],
                "detected_at": datetime.now(UTC),
            }
        )
    return alerts


def _format_amount(amount: Any, currency: str) -> str:
    if amount is None:
        return "un monto no calculable"
    return f"{float(amount):,.0f} {currency}".strip()


# =============================================================================
# Ask your data
# =============================================================================


def ask_data(
    conn: psycopg.Connection,
    settings: Settings,
    tenant_id: UUID,
    question: str,
    country: str | None = None,
) -> dict[str, Any]:
    """question -> SQL -> validator -> read-only execution -> prose."""
    check_budget(conn, tenant_id, settings)

    hint = f"\n\nEl usuario está mirando el país '{country}'." if country else ""
    sql_response = call_llm(
        settings,
        system=SYSTEM_PROMPT,
        user_message=f"Pregunta: {question}{hint}",
        max_tokens=700,
        temperature=0.0,
    )
    record_usage(conn, tenant_id, "ask", sql_response)

    validation = validate_sql(sql_response.text)
    if validation.rejected:
        logger.info("nl2sql rejection for question %r: %s", question[:120], validation.reason)
        return {
            "answer": (
                "No puedo consultar eso. "
                f"{validation.reason}. "
                "Puedo responder sobre contribución, transportadoras, productos, geografía, "
                "cohortes, antigüedad de guías, servicio al cliente y pauta."
            ),
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "rejected": True,
            "rejection_reason": validation.reason,
            "suggestions": REJECTION_SUGGESTIONS,
        }

    rows, columns = _execute_readonly(validation.sql, tenant_id)

    if not rows:
        return {
            "answer": "La consulta corrió bien pero no devolvió filas para ese filtro.",
            "sql": validation.sql,
            "columns": columns,
            "rows": [],
            "row_count": 0,
            "rejected": False,
            "suggestions": [],
        }

    prose = call_llm(
        settings,
        system=ANALYST_SYSTEM_PROMPT,
        user_message=(
            f"Pregunta original: {question}\n\n"
            f"Resultado de la consulta ({len(rows)} filas):\n{_as_readable(rows[:40])}\n\n"
            f"Responde la pregunta en 2 a 4 frases usando estas cifras. No inventes nada "
            f"que no esté aquí."
        ),
        max_tokens=500,
    )
    record_usage(conn, tenant_id, "ask", prose)

    return {
        "answer": prose.text,
        "sql": validation.sql,
        "columns": columns,
        "rows": _decimal_to_float(rows),
        "row_count": len(rows),
        "rejected": False,
        "suggestions": [],
    }


def _execute_readonly(sql: str, tenant_id: UUID) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the vetted SQL as the read-only role, scoped to this tenant.

    The role can only SELECT from mart, its search_path is mart, and the
    statement timeout means a pathological query dies in five seconds instead of
    holding a connection.
    """
    pool = get_readonly_pool()
    if pool is None:
        raise AiUnavailable(
            "El rol de solo lectura para consultas no está configurado "
            "(DATABASE_URL_READONLY).",
            reason="not_configured",
        )

    from psycopg.rows import dict_row

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute("SET search_path = mart")
            cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(tenant_id),))

        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql)
                rows = cur.fetchmany(MAX_ROWS)
                columns = [desc.name for desc in (cur.description or [])]
        except Exception as exc:
            logger.warning("read-only execution failed: %s", type(exc).__name__)
            raise AiUnavailable(
                "La consulta generada no se pudo ejecutar. Reformula la pregunta.",
                reason="execution_failed",
            ) from exc

    return rows, columns


def _as_readable(payload: Any) -> str:
    import json

    return json.dumps(_decimal_to_float(payload), ensure_ascii=False, indent=1, default=str)
