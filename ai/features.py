"""The AI features: daily brief, alerts, and ask-your-data with memory.

What the model is allowed to do here is deliberately small:

* BRIEF - it writes prose from aggregates. It never sees a row, never sees a
  customer hash, never sees a tracking number.
* ALERTS - the detections are SQL (`mart.v_alert_signals`). The model phrases
  them and nothing else. A model that decides what counts as a problem invents
  problems.
* ASK - it writes SQL, which is then parsed, vetted, and executed as a read-only
  role. Then it writes prose from the result.

WHAT MEMORY ADDS, AND WHAT IT DOES NOT.

Both the brief and the ask flow now carry a block of stored facts about this
operation (see `ai/memory.py`) and the last few turns of the conversation. That
is retrieval, not training: the model's weights are untouched and always will
be. What changes is that it knows 62% is this operation's own median before it
calls 38% "bad", and that "¿y en Guayas?" refers to whatever was just asked.

Conversation turns are persisted whatever the outcome - including rejections and
degraded answers - because a question that produced a bad answer is exactly the
one worth having on record.
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
from ai.memory import build_prompt_memory
from ai.nl2sql import (
    MAX_ROWS,
    REJECTION_SUGGESTIONS,
    STATEMENT_TIMEOUT_MS,
    SYSTEM_PROMPT,
    validate_sql,
)
from api.db import fetch_all, fetch_one, fetch_required, get_readonly_pool
from api.settings import Settings

logger = logging.getLogger(__name__)

BRIEF_TTL_HOURS = 24
ALERTS_TTL_HOURS = 2
ASK_TTL_HOURS = 1

# How many past turns are replayed as context. Six is three exchanges: enough
# for "¿y en Guayas?" to resolve, short enough that the conversation does not
# quietly consume the token budget that the actual numbers need.
CONTEXT_MESSAGES = 6

ANALYST_SYSTEM_PROMPT = """Eres un analista de negocio que le habla al dueño de una operación
de ecommerce contraentrega (COD) en Latinoamérica. No es técnico, no sabe de bases de datos
y tiene 30 segundos. Le hablas como un socio que ya miró los números por él.

CÓMO ESCRIBES:
- Entre 3 y 4 frases, salvo que pidan detalle. Nada de listas, encabezados ni emojis.
- Cada cifra con su contexto: "despachaste 513 guías y se entregó el 75%", no "75%" a secas.
- Traduce el problema a plata siempre que puedas, con la moneda del país (por ejemplo
  "USD 1.240", "COP 3.500.000", "GTQ 8.900"). Nunca un monto sin moneda.
- Máximo UNA recomendación, al final, concreta y accionable.
- Español latinoamericano, directo y simple. Sin entusiasmo, sin felicitaciones, sin
  jerga.

PROHIBIDO EL LENGUAJE TÉCNICO. El dueño no sabe qué es una consulta ni una tabla. Nunca
digas: "query", "consulta", "dataset", "registros", "filas", "columna", "campo", "tabla",
"vista", "SQL", "base de datos", "null", "porcentaje calculado sobre". Nunca escribas
nombres internos como mart.v_algo, delivery_rate_pct, capital_in_street o similares:
tradúcelos a palabras ("efectividad de entrega", "plata en la calle", "días para
cobrar"). Si el resultado trae códigos o nombres técnicos, di lo que significan, no
el código.

VOCABULARIO DEL NEGOCIO:
- Nunca digas "ventas". En contraentrega una venta no es plata hasta que se entrega.
  Di "despachos", "guías", "entregas", "recaudo" o "contribución".
- Estados de una guía: entregada, en tránsito, con novedad, devuelta, indemnizada.
- "Devolución" es una pérdida real: se pagó flete de ida y de vuelta sin recaudar nada.

CONTRIBUCIÓN NETA: NUNCA LA LLAMES PÉRDIDA.
En contraentrega, una guía cobra flete y producto el día que se despacha y no recauda
nada hasta que se entrega. Mientras haya guías abiertas, la contribución neta suma el
costo de guías jóvenes contra el recaudo de guías viejas y sale negativa aunque la
operación sea rentable. Decir "estás perdiendo dinero" en ese caso es falso y lleva al
dueño a recortar justo cuando debería reponer inventario.
- Si tienes la contribución separada, informa lo que ya cerró (contribución realizada) y
  lo que sigue en la calle (costo ya pagado de guías que aún no entregan) por separado, y
  di qué parte de las guías ya maduró.
- Si solo tienes la contribución neta y hay guías abiertas, di que es una cifra a mitad
  de camino y explica por qué, en vez de declarar una pérdida.

QUÉ NO HACER:
- No inventes cifras que no estén en los datos que te doy.
- Si los datos son escasos, dilo en una frase en vez de rellenar.
- No repitas los números en el mismo orden en que te los di: cuenta qué significan.
"""


def _country_currency(conn: psycopg.Connection, country: str | None) -> str | None:
    """The currency the answer must quote amounts in. None when no country is set."""
    if not country:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT currency_code FROM core.country WHERE code = %s", (country.upper(),))
        row = cur.fetchone()
    return row[0] if row else None


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
# Conversations
#
# A thread is what makes a follow-up a follow-up. Without one, "¿y en Guayas?"
# is an unanswerable fragment; with one, it is the previous question restricted
# to a province.
# =============================================================================


def ensure_conversation(
    conn: psycopg.Connection,
    tenant_id: UUID,
    *,
    conversation_id: UUID | None,
    user_id: UUID | None,
    country: str | None,
    title_hint: str,
) -> UUID:
    """Return an existing thread of THIS tenant, or open a new one.

    A conversation_id that belongs to somebody else - or to nobody - silently
    starts a fresh thread instead of erroring. There is nothing useful to tell
    the user, and confirming that an id exists is a small oracle nobody needs.
    """
    if conversation_id is not None:
        row = fetch_one(
            conn,
            "SELECT id FROM raw.ai_conversation WHERE id = %s AND tenant_id = %s",
            (conversation_id, tenant_id),
        )
        if row:
            return row["id"]
        logger.info("conversation %s not found for this tenant; starting a new one",
                    conversation_id)

    title = title_hint.strip()[:80] or "Consulta"
    row = fetch_required(
        conn,
        """
        INSERT INTO raw.ai_conversation (tenant_id, user_id, country_code, title)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (tenant_id, user_id, country, title),
    )
    return row["id"]


def append_message(
    conn: psycopg.Connection,
    tenant_id: UUID,
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    sql_executed: str | None = None,
    row_count: int | None = None,
    tokens: int = 0,
) -> UUID | None:
    """Persist one turn. Never the reason an answer fails to reach the user."""
    try:
        row = fetch_one(
            conn,
            """
            INSERT INTO raw.ai_message
                (conversation_id, tenant_id, role, content, sql_executed, row_count, tokens)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, tenant_id, role, content, sql_executed, row_count, tokens),
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE raw.ai_conversation SET last_message_at = now() WHERE id = %s",
                (conversation_id,),
            )
        return row["id"] if row else None
    except Exception:
        logger.warning("could not persist a %s message", role, exc_info=True)
        return None


def conversation_history(
    conn: psycopg.Connection, tenant_id: UUID, conversation_id: UUID, limit: int = CONTEXT_MESSAGES
) -> list[dict[str, Any]]:
    """The last N turns, oldest first, so the model reads them in order."""
    rows = fetch_all(
        conn,
        """
        SELECT role, content, created_at FROM (
            SELECT role, content, created_at
            FROM raw.ai_message
            WHERE conversation_id = %s AND tenant_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        ) recent
        ORDER BY created_at
        """,
        (conversation_id, tenant_id, limit),
    )
    return rows


def format_history(messages: list[dict[str, Any]]) -> str:
    """Render past turns compactly. Answers are truncated: the question is the
    part that carries the thread, the answer is only there for reference."""
    if not messages:
        return ""
    lines = ["CONVERSACIÓN HASTA AHORA (la pregunta nueva puede referirse a esto):"]
    for message in messages:
        speaker = "Operador" if message["role"] == "user" else "Tú"
        content = message["content"].strip().replace("\n", " ")
        lines.append(f"- {speaker}: {content[:300]}")
    return "\n".join(lines)


def list_conversations(
    conn: psycopg.Connection, tenant_id: UUID, *, limit: int = 30
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT c.id, c.country_code, c.title, c.created_at, c.last_message_at,
               count(m.id) AS message_count
        FROM raw.ai_conversation c
        LEFT JOIN raw.ai_message m ON m.conversation_id = c.id
        WHERE c.tenant_id = %s
        GROUP BY c.id
        ORDER BY c.last_message_at DESC
        LIMIT %s
        """,
        (tenant_id, limit),
    )


def get_conversation(
    conn: psycopg.Connection, tenant_id: UUID, conversation_id: UUID
) -> dict[str, Any] | None:
    header = fetch_one(
        conn,
        """
        SELECT id, country_code, title, created_at, last_message_at
        FROM raw.ai_conversation WHERE id = %s AND tenant_id = %s
        """,
        (conversation_id, tenant_id),
    )
    if header is None:
        return None

    messages = fetch_all(
        conn,
        """
        SELECT m.id, m.role, m.content, m.sql_executed, m.row_count, m.tokens,
               m.created_at, f.helpful, f.comment AS feedback_comment
        FROM raw.ai_message m
        LEFT JOIN raw.ai_feedback f ON f.message_id = m.id
        WHERE m.conversation_id = %s AND m.tenant_id = %s
        ORDER BY m.created_at
        """,
        (conversation_id, tenant_id),
    )
    return {**header, "messages": messages}


def record_feedback(
    conn: psycopg.Connection,
    tenant_id: UUID,
    message_id: UUID,
    *,
    helpful: bool,
    comment: str | None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    """Store a verdict, and turn an explained rejection into a durable fact.

    This is the closest thing to "the AI learns from what is done" that exists
    here, and it is worth being precise about: nothing is trained. An unhelpful
    verdict WITH a reason becomes one row in `raw.ai_memory`, which is then read
    back into every later prompt for that country. A thumbs-down with no comment
    is recorded and nothing more - there is nothing to learn from a verdict that
    does not say what was wrong.
    """
    from ai.memory import learn_from_correction

    owned = fetch_one(
        conn,
        "SELECT id FROM raw.ai_message WHERE id = %s AND tenant_id = %s AND role = 'assistant'",
        (message_id, tenant_id),
    )
    if owned is None:
        return {"stored": False, "learned": False,
                "message": "Esa respuesta no existe en esta cuenta."}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.ai_feedback (message_id, tenant_id, helpful, comment)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET
                helpful = EXCLUDED.helpful,
                comment = EXCLUDED.comment,
                created_at = now()
            """,
            (message_id, tenant_id, helpful, (comment or "").strip()[:2000] or None),
        )

    learned = False
    if not helpful and (comment or "").strip():
        learned = learn_from_correction(
            conn, tenant_id, message_id, comment or "", created_by=user_id
        ) is not None

    return {
        "stored": True,
        "learned": learned,
        "message": (
            "Anotado. La próxima vez que preguntes algo parecido, el copiloto va a "
            "tener en cuenta tu corrección."
            if learned
            else "Gracias, quedó registrado."
        ),
    }


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

    # What the copilot already knows about this operation goes into the cache key
    # as well as into the prompt: a corrected fact has to produce a new brief,
    # not serve yesterday's from cache.
    memory = build_prompt_memory(conn, tenant_id, country)

    cache_key = context_hash("brief", tenant_id, country, {**context, "_memory": memory})
    cached = read_cache(conn, cache_key)
    if cached:
        return {**cached, "cached": True}

    check_budget(conn, tenant_id, settings)

    response = call_llm(
        settings,
        system=f"{ANALYST_SYSTEM_PROMPT}\n\n{memory}" if memory else ANALYST_SYSTEM_PROMPT,
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
    # Severity is text, and 'warning' sorts after 'critical' alphabetically -
    # ORDER BY severity DESC put the critical ones LAST. Spelled out instead.
    query += (
        " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,"
        " impact_amount DESC NULLS LAST LIMIT 20"
    )

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
    *,
    conversation_id: UUID | None = None,
    user_id: UUID | None = None,
    allowed_countries: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """question -> SQL -> validator -> read-only execution -> prose, with context.

    Two blocks are prepended to the prompts: what the copilot remembers about
    this operation, and the last few turns of this thread. Neither widens what
    the generated SQL is allowed to touch - the validator does not know or care
    that memory exists.

    `allowed_countries` is the caller's country scope (None = the whole
    company). The rows are cut to it BEFORE the prose is written, so a limited
    membership never has another country's numbers narrated back to it, even
    if the generated SQL ignored the country hint.
    """
    conversation = ensure_conversation(
        conn, tenant_id,
        conversation_id=conversation_id, user_id=user_id,
        country=country, title_hint=question,
    )
    append_message(conn, tenant_id, conversation, "user", question)

    memory = build_prompt_memory(conn, tenant_id, country)
    history = format_history(
        # Drop the turn just written: the model gets it as the question.
        conversation_history(conn, tenant_id, conversation, CONTEXT_MESSAGES + 1)[:-1]
    )

    def _finish(payload: dict[str, Any], *, tokens: int = 0) -> dict[str, Any]:
        """Persist the assistant turn and hand back the response body."""
        message_id = append_message(
            conn, tenant_id, conversation, "assistant", payload["answer"],
            sql_executed=payload.get("sql"),
            row_count=payload.get("row_count"),
            tokens=tokens,
        )
        return {**payload, "conversation_id": conversation, "message_id": message_id}

    try:
        check_budget(conn, tenant_id, settings)

        hint = f"\n\nEl usuario está mirando el país '{country}'." if country else ""
        context_blocks = "\n\n".join(block for block in (memory, history) if block)
        sql_response = call_llm(
            settings,
            system=f"{SYSTEM_PROMPT}\n\n{context_blocks}" if context_blocks else SYSTEM_PROMPT,
            user_message=f"Pregunta: {question}{hint}",
            max_tokens=700,
            temperature=0.0,
        )
        record_usage(conn, tenant_id, "ask", sql_response)
    except AiUnavailable as exc:
        # The question is already on record; the failure should be too. Then it
        # is re-raised so the router degrades exactly as it always has.
        _finish(
            {
                "answer": exc.message, "sql": None, "columns": [], "rows": [],
                "row_count": 0, "rejected": True, "rejection_reason": exc.reason,
                "suggestions": REJECTION_SUGGESTIONS,
            }
        )
        raise

    validation = validate_sql(sql_response.text)
    if validation.rejected:
        logger.info("nl2sql rejection for question %r: %s", question[:120], validation.reason)
        return _finish(
            {
                "answer": (
                    "No puedo consultar eso. "
                    f"{validation.reason}. "
                    "Puedo responder sobre contribución, transportadoras, productos, "
                    "geografía, cohortes, antigüedad de guías, servicio al cliente, pauta, "
                    "márgenes de dropshipping, alistamiento, guías en oficina, flete y "
                    "ciclo de caja."
                ),
                "sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "rejected": True,
                "rejection_reason": validation.reason,
                "suggestions": REJECTION_SUGGESTIONS,
            },
            tokens=sql_response.input_tokens + sql_response.output_tokens,
        )

    try:
        rows, columns = _execute_readonly(validation.sql or "", tenant_id)
        if allowed_countries is not None:
            rows, columns, cut_note = cut_rows_to_countries(rows, columns, allowed_countries)
            if cut_note:
                return _finish(
                    {
                        "answer": cut_note, "sql": validation.sql, "columns": columns,
                        "rows": [], "row_count": 0, "rejected": True,
                        "rejection_reason": "fuera_de_alcance",
                        "suggestions": REJECTION_SUGGESTIONS,
                    },
                    tokens=sql_response.input_tokens + sql_response.output_tokens,
                )
    except AiUnavailable as exc:
        _finish(
            {
                "answer": exc.message, "sql": validation.sql, "columns": [], "rows": [],
                "row_count": 0, "rejected": True, "rejection_reason": exc.reason,
                "suggestions": REJECTION_SUGGESTIONS,
            }
        )
        raise

    if not rows:
        return _finish(
            {
                "answer": (
                    "No encontré guías ni movimientos que respondan eso con los datos "
                    "cargados. Prueba con otro rango de fechas, otro país u otra "
                    "transportadora."
                ),
                "sql": validation.sql,
                "columns": columns,
                "rows": [],
                "row_count": 0,
                "rejected": False,
                "suggestions": [],
            },
            tokens=sql_response.input_tokens + sql_response.output_tokens,
        )

    currency = _country_currency(conn, country)
    currency_hint = (
        f" Todo monto va con la moneda {currency}." if currency
        else " Todo monto va con la moneda del país al que pertenece."
    )
    try:
        prose = call_llm(
            settings,
            system="\n\n".join(
                block for block in (ANALYST_SYSTEM_PROMPT, memory, history) if block
            ),
            user_message=(
                f"Pregunta del dueño: {question}\n\n"
                f"Cifras ya calculadas ({len(rows)} resultados):\n{_as_readable(rows[:40])}\n\n"
                f"Responde en 3 a 4 frases, en lenguaje de negocio, usando solo estas cifras y "
                f"dándoles contexto (qué se despachó, qué se entregó, qué se devolvió, cuánto "
                f"es en plata). No menciones cómo se obtuvieron ni nombres técnicos.{currency_hint}"
            ),
            max_tokens=500,
        )
    except AiUnavailable as exc:
        _finish(
            {
                "answer": exc.message, "sql": validation.sql, "columns": columns,
                "rows": [], "row_count": len(rows), "rejected": True,
                "rejection_reason": exc.reason, "suggestions": [],
            }
        )
        raise
    record_usage(conn, tenant_id, "ask", prose)

    return _finish(
        {
            "answer": prose.text,
            "sql": validation.sql,
            "columns": columns,
            "rows": _decimal_to_float(rows),
            "row_count": len(rows),
            "rejected": False,
            "suggestions": [],
        },
        tokens=(
            sql_response.input_tokens + sql_response.output_tokens
            + prose.input_tokens + prose.output_tokens
        ),
    )


def cut_rows_to_countries(
    rows: list[dict[str, Any]], columns: list[str], countries: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Keep only the rows a limited membership may read.

    A row that names its country is kept when the country is in scope. A
    result with no country column at all cannot be attributed, so it is not
    handed over: the third value is the sentence to answer with instead.
    """
    if "country_code" not in columns:
        return (
            [],
            columns,
            "Esa respuesta mezcla países y tu usuario solo tiene acceso a "
            f"{', '.join(countries)}. Pregunta nombrando el país.",
        )
    allowed = {c.upper() for c in countries}
    kept = [
        row for row in rows
        if row.get("country_code") is not None and str(row["country_code"]).upper() in allowed
    ]
    return kept, columns, None


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
