"""LLM access, with a budget and a cache in front of it.

Three things this module refuses to do:

* Spend without a ceiling. `AI_DAILY_TOKEN_BUDGET` is enforced per tenant per
  day. When it runs out the feature degrades with a clear message.
* Pay twice for the same answer. Responses are cached by a hash of the context,
  so the same numbers cost one call regardless of how many people open the page.
* Take the dashboard down. Every failure path returns `AiUnavailable`, which the
  router turns into a degraded response. The KPIs never depend on the model.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from api.db import fetch_one
from api.settings import Settings

logger = logging.getLogger(__name__)


class AiUnavailable(Exception):
    """The AI layer cannot answer right now. Carries a message for the user."""

    def __init__(self, message: str, *, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


@dataclass(slots=True)
class LlmResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


def context_hash(feature: str, tenant_id: UUID, country: str | None, context: Any) -> str:
    """Stable key over the exact numbers the model will see."""
    payload = json.dumps(context, sort_keys=True, default=str)
    raw = f"{feature}|{tenant_id}|{country or '-'}|{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_cache(conn: psycopg.Connection, cache_key: str) -> dict[str, Any] | None:
    row = fetch_one(
        conn,
        "SELECT payload FROM raw.ai_cache WHERE cache_key = %s AND expires_at > now()",
        (cache_key,),
    )
    return row["payload"] if row else None


def write_cache(
    conn: psycopg.Connection,
    *,
    cache_key: str,
    tenant_id: UUID,
    feature: str,
    country: str | None,
    payload: dict[str, Any],
    model: str,
    input_tokens: int,
    output_tokens: int,
    ttl_hours: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.ai_cache
                (cache_key, tenant_id, feature, country_code, payload, model,
                 input_tokens, output_tokens, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET
                payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at
            """,
            (
                cache_key, tenant_id, feature, country, Json(payload), model,
                input_tokens, output_tokens,
                datetime.now(UTC) + timedelta(hours=ttl_hours),
            ),
        )


def tokens_used_today(conn: psycopg.Connection, tenant_id: UUID) -> int:
    row = fetch_one(conn, "SELECT raw.ai_tokens_used_today(%s) AS used", (tenant_id,))
    return int(row["used"]) if row else 0


def check_budget(conn: psycopg.Connection, tenant_id: UUID, settings: Settings) -> None:
    used = tokens_used_today(conn, tenant_id)
    if used >= settings.ai_daily_token_budget:
        raise AiUnavailable(
            f"El presupuesto de IA de hoy se agotó ({used:,} de "
            f"{settings.ai_daily_token_budget:,} tokens). Los tableros siguen funcionando "
            f"normalmente; el copiloto vuelve mañana.",
            reason="budget_exhausted",
        )


def record_usage(
    conn: psycopg.Connection, tenant_id: UUID, feature: str, response: LlmResponse
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT raw.record_ai_usage(%s, %s, %s, %s)",
            (tenant_id, feature, response.input_tokens, response.output_tokens),
        )


def call_llm(
    settings: Settings,
    *,
    system: str,
    user_message: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> LlmResponse:
    """One call to Anthropic. Every failure becomes AiUnavailable."""
    try:
        settings.require_ai()
    except RuntimeError as exc:
        raise AiUnavailable(
            "El copiloto no está configurado en este despliegue. "
            "Falta ANTHROPIC_API_KEY o AI_ENABLED.",
            reason="not_configured",
        ) from exc

    try:
        import anthropic
    except ImportError as exc:      # pragma: no cover - declared dependency
        raise AiUnavailable("Falta la librería anthropic en el servidor.", reason="missing_dep") from exc

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=float(settings.ai_request_timeout_seconds),
        max_retries=1,
    )

    try:
        message = client.messages.create(
            model=settings.ai_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        logger.warning("LLM call failed: %s", type(exc).__name__)
        raise AiUnavailable(
            "El copiloto no respondió a tiempo. Vuelve a intentarlo en un momento.",
            reason="upstream_error",
        ) from exc

    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return LlmResponse(
        text=text.strip(),
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        model=settings.ai_model,
    )
