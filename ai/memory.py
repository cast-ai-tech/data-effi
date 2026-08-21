"""What the copilot remembers about this operation.

READ THIS BEFORE CALLING IT "LEARNING".

The model is not fine-tuned. No weights are updated by anything in this file, or
anywhere else in Data Effi. Gemini is exactly as capable after a thousand
conversations as it was after zero.

What DOES improve is the context it answers from, and that is not a figure of
speech - it is rows in `raw.ai_memory`, written by three paths:

  1. THE OPERATOR SAYS SOMETHING.  "Servientrega siempre tarda más en Guayas."
     A fact the data cannot state, stored verbatim, injected into every later
     prompt for that country.
  2. THE OPERATOR CORRECTS AN ANSWER.  A thumbs-down with a reason becomes a
     'correction' fact, so the same misreading is not repeated.
  3. THE DATA ITSELF.  `infer_thresholds` derives what is NORMAL FOR THIS
     OPERATION - its own median delivery rate, its own freight share, its own
     days to cash. This is the difference between "38% de efectividad es bajo
     según los benchmarks del sector" (useless, possibly wrong) and "38% está 24
     puntos por debajo de tu propia mediana de 62%" (actionable, and true).

The honest description is memory and retrieval. It is also the only version of
"the AI learns" that can be inspected, edited and deleted - which is worth more
than the word.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from api.db import fetch_all, fetch_one

logger = logging.getLogger(__name__)

# How many facts reach a prompt, and how much room they get. Memory that grows
# without a ceiling quietly eats the token budget and pushes the actual numbers
# out of the context window.
MAX_FACTS_IN_PROMPT = 20
MAX_MEMORY_CHARS = 2_000

# An inferred threshold is a snapshot of the operation as it was. Left forever
# it becomes a confident lie, so it is written with an expiry and re-derived.
INFERRED_TTL_DAYS = 14

VALID_KINDS = ("preference", "threshold", "context", "decision", "correction")
VALID_SOURCES = ("user", "inferred", "system")

# How each kind is introduced to the model, in the operator's language.
_KIND_LABEL = {
    "preference": "Preferencia",
    "threshold": "Normal para esta operación",
    "context": "Contexto del negocio",
    "decision": "Decisión ya tomada",
    "correction": "Corrección del operador",
}


# =============================================================================
# Writing
# =============================================================================


def remember(
    conn: psycopg.Connection,
    tenant_id: UUID,
    kind: str,
    key: str,
    value: str,
    *,
    country_code: str | None = None,
    source: str = "user",
    confidence: float = 1.0,
    created_by: UUID | None = None,
    expires_at: datetime | None = None,
) -> UUID | None:
    """Upsert one durable fact. Returns its id, or None if it was rejected.

    Rejection is quiet on purpose: a malformed fact must never break the answer
    the user actually asked for. It is logged and dropped.
    """
    if kind not in VALID_KINDS:
        logger.warning("memory rejected: unknown kind %r", kind)
        return None
    if source not in VALID_SOURCES:
        logger.warning("memory rejected: unknown source %r", source)
        return None

    key = (key or "").strip()[:120]
    value = (value or "").strip()
    if not key or not value:
        logger.warning("memory rejected: empty key or value")
        return None

    # A fact long enough to be a document is a fact that will crowd out the
    # numbers. Truncated rather than refused - the first part is the useful part.
    value = value[:1_000]
    confidence = min(max(float(confidence), 0.0), 1.0)

    row = fetch_one(
        conn,
        """
        INSERT INTO raw.ai_memory
            (tenant_id, country_code, kind, key, value, source, confidence,
             created_by, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, country_code, key) DO UPDATE SET
            kind       = EXCLUDED.kind,
            value      = EXCLUDED.value,
            source     = EXCLUDED.source,
            confidence = EXCLUDED.confidence,
            expires_at = EXCLUDED.expires_at,
            updated_at = now()
        RETURNING id
        """,
        (tenant_id, country_code, kind, key, value, source, confidence,
         created_by, expires_at),
    )
    return row["id"] if row else None


def forget(conn: psycopg.Connection, tenant_id: UUID, memory_id: UUID) -> bool:
    """Delete one fact. Memory the operator cannot delete is a liability."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM raw.ai_memory WHERE id = %s AND tenant_id = %s",
            (memory_id, tenant_id),
        )
        return cur.rowcount > 0


# =============================================================================
# Reading
# =============================================================================


def recall(
    conn: psycopg.Connection,
    tenant_id: UUID,
    country_code: str | None = None,
    *,
    limit: int = MAX_FACTS_IN_PROMPT,
) -> list[dict[str, Any]]:
    """The facts relevant to a country: most confident first, expired excluded.

    A fact with no country applies everywhere - "no despachamos los domingos" is
    not an Ecuador fact. Country-specific facts sort first, because when both
    exist the specific one is the one that matters.
    """
    rows = fetch_all(
        conn,
        """
        SELECT id, country_code, kind, key, value, source, confidence, updated_at
        FROM raw.ai_memory
        WHERE tenant_id = %s
          AND (expires_at IS NULL OR expires_at > now())
          AND (%s::char(2) IS NULL OR country_code = %s OR country_code IS NULL)
        ORDER BY (country_code IS NOT NULL) DESC,
                 confidence DESC,
                 updated_at DESC
        LIMIT %s
        """,
        (tenant_id, country_code, country_code, limit),
    )
    return rows


def format_memory(facts: list[dict[str, Any]]) -> str:
    """Render facts as a compact prompt block. Empty string when there are none.

    Kept small and flat on purpose. This text is prepended to a system prompt,
    so every character competes with the actual numbers for the model's
    attention and for the token budget.
    """
    if not facts:
        return ""

    lines: list[str] = [
        "LO QUE YA SABES DE ESTA OPERACIÓN "
        "(hechos guardados, no suposiciones; úsalos y no los contradigas):"
    ]
    used = len(lines[0])

    for fact in facts:
        label = _KIND_LABEL.get(fact["kind"], "Dato")
        scope = f" [{fact['country_code']}]" if fact.get("country_code") else ""
        line = f"- {label}{scope}: {fact['value']}"
        if used + len(line) > MAX_MEMORY_CHARS:
            break
        lines.append(line)
        used += len(line)

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def recall_block(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str | None = None
) -> str:
    """recall + format in one call, and never a reason to fail an answer.

    Memory is an enhancement. If reading it breaks, the copilot answers without
    it rather than returning an error to someone who asked about their freight.
    """
    try:
        return format_memory(recall(conn, tenant_id, country_code))
    except Exception:
        logger.warning("memory recall failed; answering without it", exc_info=True)
        return ""


# =============================================================================
# Learning from a correction
# =============================================================================


def learn_from_correction(
    conn: psycopg.Connection,
    tenant_id: UUID,
    message_id: UUID,
    comment: str,
    *,
    created_by: UUID | None = None,
) -> UUID | None:
    """Turn "this answer was wrong, because X" into a durable fact.

    Keyed on the QUESTION, not on the message: the same question asked again
    next week must hit the same memory row and update it, not stack a second
    near-identical fact into every future prompt.
    """
    comment = (comment or "").strip()
    if not comment:
        return None

    answer = fetch_one(
        conn,
        """
        SELECT m.id, m.content, m.conversation_id, c.country_code
        FROM raw.ai_message m
        JOIN raw.ai_conversation c ON c.id = m.conversation_id
        WHERE m.id = %s AND m.tenant_id = %s
        """,
        (message_id, tenant_id),
    )
    if answer is None:
        logger.info("correction ignored: message %s not found for this tenant", message_id)
        return None

    question = fetch_one(
        conn,
        """
        SELECT content FROM raw.ai_message
        WHERE conversation_id = %s AND role = 'user' AND created_at <= (
            SELECT created_at FROM raw.ai_message WHERE id = %s
        )
        ORDER BY created_at DESC LIMIT 1
        """,
        (answer["conversation_id"], message_id),
    )
    question_text = (question["content"] if question else "").strip()

    fingerprint = hashlib.sha256(
        question_text.lower().encode("utf-8")
    ).hexdigest()[:12]

    value = (
        f"Cuando pregunten algo como «{question_text[:200]}», la respuesta anterior "
        f"estuvo mal. El operador aclaró: {comment[:400]}"
        if question_text
        else f"El operador corrigió una respuesta anterior: {comment[:400]}"
    )

    return remember(
        conn,
        tenant_id,
        "correction",
        f"correccion:{fingerprint}",
        value,
        country_code=answer["country_code"],
        source="user",
        confidence=1.0,
        created_by=created_by,
    )


# =============================================================================
# Learning from the data: what is normal FOR THIS OPERATION
# =============================================================================


def infer_thresholds(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str
) -> list[dict[str, Any]]:
    """Derive the operation's own normals and store them as 'threshold' facts.

    Every number here comes from the tenant's own mart views. None of it is a
    sector benchmark, because a sector benchmark for COD dropshipping in LATAM
    is a number somebody made up, and quoting it to an operator who can see his
    own dashboard destroys trust in everything else the copilot says.

    Returns the facts it derived. Facts it could not derive are simply absent -
    silence is better than a threshold computed from nine guides.
    """
    country_code = country_code.upper()
    expires = datetime.now(UTC) + timedelta(days=INFERRED_TTL_DAYS)
    derived: list[dict[str, Any]] = []

    def _store(key: str, value: str, confidence: float) -> None:
        memory_id = remember(
            conn, tenant_id, "threshold", key, value,
            country_code=country_code, source="inferred",
            confidence=confidence, expires_at=expires,
        )
        derived.append(
            {"key": key, "value": value, "confidence": confidence, "id": memory_id}
        )

    for probe in (_infer_delivery_rate, _infer_freight, _infer_cash, _infer_prep):
        try:
            result = probe(conn, country_code)
        except Exception:
            # One unavailable view must not cost the others. A missing threshold
            # degrades the phrasing; a raised exception would degrade the answer.
            logger.warning("threshold probe %s failed", probe.__name__, exc_info=True)
            continue
        if result is not None:
            _store(*result)

    return derived


def ensure_thresholds(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str
) -> None:
    """Derive the operation's normals, but only when they are missing or stale.

    `infer_thresholds` runs four aggregate queries and writes four rows. Doing
    that on every single question would put a write on the read path for numbers
    that move over weeks, not seconds - so it runs when there is nothing stored,
    or when what is stored has expired. Ingestion re-derives them anyway, which
    is where a real change actually arrives from.

    Never raises: a missing threshold makes the answer less specific, not absent.
    """
    try:
        row = fetch_one(
            conn,
            """
            SELECT count(*) AS fresh
            FROM raw.ai_memory
            WHERE tenant_id = %s AND country_code = %s
              AND kind = 'threshold' AND source = 'inferred'
              AND (expires_at IS NULL OR expires_at > now())
            """,
            (tenant_id, country_code.upper()),
        )
        if row and int(row["fresh"]) > 0:
            return
        infer_thresholds(conn, tenant_id, country_code)
    except Exception:
        logger.warning("threshold refresh failed for %s", country_code, exc_info=True)


def build_prompt_memory(
    conn: psycopg.Connection, tenant_id: UUID, country_code: str | None
) -> str:
    """The block that goes into a system prompt: inferred normals + stored facts."""
    if country_code:
        ensure_thresholds(conn, tenant_id, country_code)
    return recall_block(conn, tenant_id, country_code)


def _confidence_for(volume: int, *, full_at: int) -> float:
    """More evidence, more confidence - capped at 0.9 because it is still a guess."""
    if volume <= 0:
        return 0.0
    return round(min(0.9, 0.35 + 0.55 * min(1.0, volume / full_at)), 3)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _infer_delivery_rate(
    conn: psycopg.Connection, country_code: str
) -> tuple[str, str, float] | None:
    """The operation's own median delivery rate over its mature cohorts."""
    row = fetch_one(
        conn,
        """
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY delivery_rate_terminal_pct)
                   AS median_pct,
               sum(shipments) AS shipments
        FROM mart.v_daily_contribution
        WHERE country_code = %s
          AND day >= CURRENT_DATE - 90
          AND delivery_rate_terminal_pct IS NOT NULL
        """,
        (country_code,),
    )
    median = _num(row and row["median_pct"])
    volume = int(row["shipments"] or 0) if row else 0
    if median is None or volume < 100:
        return None

    return (
        "efectividad_tipica_pct",
        f"Tu efectividad de entrega mediana en {country_code} es {median:.1f}% "
        f"(últimos 90 días, {volume:,} guías). Compara cualquier cifra contra ESTA, "
        f"no contra un promedio del sector.",
        _confidence_for(volume, full_at=2_000),
    )


def _infer_freight(
    conn: psycopg.Connection, country_code: str
) -> tuple[str, str, float] | None:
    """What freight normally costs here, as a share of what a guide is worth."""
    row = fetch_one(
        conn,
        """
        SELECT sum(freight_total) AS freight,
               sum(shipments)     AS shipments,
               round(sum(freight_total) / NULLIF(sum(total_weight_kg), 0), 2) AS per_kg,
               min(currency_code) AS currency_code
        FROM mart.v_freight_analysis
        WHERE country_code = %s
        """,
        (country_code,),
    )
    if row is None:
        return None

    volume = int(row["shipments"] or 0)
    per_kg = _num(row["per_kg"])
    if volume < 100 or per_kg is None or per_kg <= 0:
        return None

    share = fetch_one(
        conn,
        """
        SELECT round(sum(freight) / NULLIF(sum(revenue), 0) * 100, 1) AS share_pct
        FROM mart.v_daily_contribution
        WHERE country_code = %s AND day >= CURRENT_DATE - 90
        """,
        (country_code,),
    )
    share_pct = _num(share and share["share_pct"])
    currency = row["currency_code"] or ""

    text = f"Tu flete típico en {country_code} es {per_kg:,.0f} {currency} por kilo".strip()
    if share_pct is not None:
        text += f", y pesa {share_pct:.1f}% del recaudo"
    text += f" ({volume:,} guías)."

    return ("flete_tipico", text, _confidence_for(volume, full_at=2_000))


def _infer_cash(
    conn: psycopg.Connection, country_code: str
) -> tuple[str, str, float] | None:
    """How long money normally takes to become spendable here."""
    row = fetch_one(
        conn,
        """
        SELECT p50_days_to_cash, p90_days_to_cash, settled, cash_in_transit,
               currency_code
        FROM mart.v_cash_cycle
        WHERE country_code = %s
        """,
        (country_code,),
    )
    if row is None:
        return None

    p50 = _num(row["p50_days_to_cash"])
    settled = int(row["settled"] or 0)
    if p50 is None or settled < 50:
        return None

    p90 = _num(row["p90_days_to_cash"])
    text = (
        f"Tu ciclo de caja normal en {country_code} es {p50:.0f} días desde el despacho "
        f"hasta que la plata es tuya"
    )
    if p90 is not None:
        text += f", y {p90:.0f} días en el peor 10%"
    text += f" ({settled:,} guías liquidadas)."

    return ("dias_a_caja_tipicos", text, _confidence_for(settled, full_at=500))


def _infer_prep(
    conn: psycopg.Connection, country_code: str
) -> tuple[str, str, float] | None:
    """How long this operation normally takes to get a parcel out the door."""
    row = fetch_one(
        conn,
        """
        SELECT round(sum(p50_prep_days * shipments) / NULLIF(sum(shipments), 0), 1)
                   AS p50_days,
               max(p90_prep_days) AS p90_days,
               sum(shipments)     AS shipments
        FROM mart.v_fulfillment_sla
        WHERE country_code = %s AND p50_prep_days IS NOT NULL
        """,
        (country_code,),
    )
    if row is None:
        return None

    p50 = _num(row["p50_days"])
    volume = int(row["shipments"] or 0)
    if p50 is None or volume < 100:
        return None

    p90 = _num(row["p90_days"])
    text = f"Tu alistamiento típico en {country_code} es {p50:.1f} días entre crear la guía y despacharla"
    if p90 is not None:
        text += f"; el 10% más lento pasa de {p90:.1f} días"
    text += f" ({volume:,} guías). Esa parte del reloj la controlas tú."

    return ("alistamiento_tipico_dias", text, _confidence_for(volume, full_at=2_000))
