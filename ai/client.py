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


# Headroom for reasoning tokens, on top of whatever the caller asked for.
# Sized from observation, not from the documented budget: see the note in
# call_llm. Generous on purpose - the cost of over-reserving is nothing, the
# cost of under-reserving is a truncated sentence shown to the operator.
THINKING_RESERVE = 1024


def call_llm(
    settings: Settings,
    *,
    system: str,
    user_message: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> LlmResponse:
    """One call to Gemini. Every failure becomes AiUnavailable."""
    try:
        settings.require_ai()
    except RuntimeError as exc:
        raise AiUnavailable(
            "El copiloto no está configurado en este despliegue. "
            "Falta GEMINI_API_KEY o AI_ENABLED.",
            reason="not_configured",
        ) from exc

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:      # pragma: no cover - declared dependency
        raise AiUnavailable(
            "Falta la librería google-genai en el servidor.", reason="missing_dep"
        ) from exc

    try:
        # HttpOptions.timeout is in MILLISECONDS; the setting is in seconds.
        # Building the client counts as a failure path too: a malformed key or a
        # bad transport config must degrade, not escape as a raw exception.
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=settings.ai_request_timeout_seconds * 1000,
            ),
        )
        response = client.models.generate_content(
            model=settings.ai_model,
            contents=user_message,
            config=types.GenerateContentConfig(
                # Gemini takes the system prompt here, not as a message role.
                system_instruction=system,
                # Thinking is billed against this ceiling, so the reserve is
                # added on top of what the caller asked for.
                #
                # The reserve is NOT ai_thinking_budget. Measured against
                # gemini-3.6-flash, that budget is a hint the model ignores: ask
                # for 128 and it spends 391-509 on a three-sentence answer. With
                # a ceiling of max_tokens + 128 the reply got roughly 128 tokens
                # of the 628 and stopped mid-word - the copilot was cutting every
                # answer at about a hundred characters while reporting a clean
                # finish.
                max_output_tokens=max_tokens + THINKING_RESERVE,
                temperature=temperature,
                # Thinking tokens are charged against max_output_tokens. Left
                # unbounded, a 600-token brief spends its whole budget reasoning
                # and comes back empty, so the callers' ceilings mean what they
                # say. It is capped rather than switched off: Gemini 3.x rejects
                # thinking_budget=0 outright with a 400, so the old "disable it"
                # setting made every call fail the moment the model moved on
                # from 2.5.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=settings.ai_thinking_budget
                ),
            ),
        )
    except Exception as exc:
        # The class name alone is useless for diagnosis: every upstream refusal
        # arrives as ClientError, and the reason - wrong model, unsupported
        # argument, exhausted quota - lives only in the message.
        logger.warning("LLM call failed (model=%s): %s: %s",
                       settings.ai_model, type(exc).__name__, exc)
        raise AiUnavailable(
            "El copiloto no respondió a tiempo. Vuelve a intentarlo en un momento.",
            reason="upstream_error",
        ) from exc

    # `response.text` is None when there is no candidate to read: a safety block,
    # a truncated answer, a recitation stop. Silence is not an answer, so it
    # degrades like any other failure instead of returning an empty string.
    text = (response.text or "").strip()
    if not text:
        finish_reason = _finish_reason(response)
        logger.warning("LLM returned no text (finish_reason=%s)", finish_reason)
        raise AiUnavailable(
            "El modelo no devolvió ninguna respuesta"
            f"{f' ({finish_reason})' if finish_reason else ''}. "
            "Reformula la pregunta o vuelve a intentarlo en un momento.",
            reason="empty_response",
        )

    # usage_metadata is absent on some responses; missing counts are 0, never a
    # crash. Undercounting the budget is survivable, taking the dashboard down
    # over a bookkeeping field is not.
    usage = getattr(response, "usage_metadata", None)
    return LlmResponse(
        text=text,
        input_tokens=getattr(usage, "prompt_token_count", None) or 0,
        output_tokens=getattr(usage, "candidates_token_count", None) or 0,
        model=settings.ai_model,
    )


def _finish_reason(response: Any) -> str | None:
    """The model's stated reason for stopping, when it gave one."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    return getattr(reason, "name", None) or str(reason)
