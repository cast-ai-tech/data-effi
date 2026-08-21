"""Copilot endpoints.

Every one of these degrades instead of failing: if the model is unreachable, the
budget is gone, or the key is missing, the response says so plainly and the
dashboard carries on. The AI is an assistant to the numbers, never a dependency
of them.

Two of these endpoints do not call a model at all. `/ai/alerts` and
`/ai/recommendations` detect with SQL and phrase with templates, so they keep
working with the AI switched off entirely - which is the state a deployment
without a Gemini key is in, and it should still be useful.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from api.deps import CurrentUserDep, DbDep, SettingsDep
from api.errors import NotFound
from api.schemas import (
    AlertResponse,
    AlertsResponse,
    AskRequest,
    AskResponse,
    BriefResponse,
    ConversationDetail,
    ConversationsResponse,
    ConversationSummary,
    FeedbackRequest,
    FeedbackResponse,
    RecommendationResponse,
    RecommendationsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

CountryQuery = Annotated[str, Query(min_length=2, max_length=2)]
OptionalCountryQuery = Annotated[str | None, Query(min_length=2, max_length=2)]


@router.get("/brief", response_model=BriefResponse, summary="Resumen del día por país")
async def brief(
    conn: DbDep, user: CurrentUserDep, settings: SettingsDep, country: CountryQuery
) -> BriefResponse:
    from ai.client import AiUnavailable
    from ai.features import generate_brief

    country_code = country.upper()
    try:
        result = await asyncio.to_thread(
            generate_brief, conn, settings, user.tenant_id, country_code
        )
    except AiUnavailable as exc:
        return BriefResponse(
            country_code=country_code,
            generated_at=datetime.now(UTC),
            summary=exc.message,
            cached=False,
            degraded=True,
            degraded_reason=exc.reason,
        )

    generated_at = result["generated_at"]
    if isinstance(generated_at, str):
        generated_at = datetime.fromisoformat(generated_at)

    return BriefResponse(
        country_code=country_code,
        generated_at=generated_at,
        summary=result["summary"],
        cached=result.get("cached", False),
        degraded=result.get("degraded", False),
        degraded_reason=result.get("degraded_reason"),
    )


@router.get("/alerts", response_model=AlertsResponse, summary="Alertas con impacto en dinero")
def alerts(
    conn: DbDep,
    user: CurrentUserDep,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> AlertsResponse:
    """Detections are SQL, not model output.

    That is why this endpoint has no degraded path for the model being down: it
    never calls it. The phrasing comes from templates over deterministic numbers.
    """
    from ai.features import collect_alerts

    found = collect_alerts(conn, country)
    return AlertsResponse(
        country_code=country.upper() if country else None,
        alerts=[AlertResponse(**alert) for alert in found],
    )


@router.post("/ask", response_model=AskResponse, summary="Pregúntale a tus datos")
async def ask(
    payload: AskRequest, conn: DbDep, user: CurrentUserDep, settings: SettingsDep
) -> AskResponse:
    from ai.client import AiUnavailable
    from ai.features import ask_data
    from ai.nl2sql import REJECTION_SUGGESTIONS

    try:
        result = await asyncio.to_thread(
            ask_data,
            conn,
            settings,
            user.tenant_id,
            payload.question,
            payload.country_code.upper() if payload.country_code else None,
            conversation_id=payload.conversation_id,
            user_id=user.id,
        )
    except AiUnavailable as exc:
        return AskResponse(
            answer=exc.message,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            rejected=True,
            rejection_reason=exc.reason,
            suggestions=REJECTION_SUGGESTIONS,
        )

    return AskResponse(**result)


@router.get(
    "/conversations",
    response_model=ConversationsResponse,
    summary="Hilos de conversación con el copiloto",
)
def conversations(conn: DbDep, user: CurrentUserDep) -> ConversationsResponse:
    from ai.features import list_conversations

    rows = list_conversations(conn, user.tenant_id)
    return ConversationsResponse(
        conversations=[ConversationSummary(**row) for row in rows]
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Un hilo completo, con su feedback",
)
def conversation_detail(
    conversation_id: UUID, conn: DbDep, user: CurrentUserDep
) -> ConversationDetail:
    from ai.features import get_conversation

    found = get_conversation(conn, user.tenant_id, conversation_id)
    if found is None:
        raise NotFound("Esa conversación no existe en esta cuenta.")
    return ConversationDetail(**found)


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Calificar una respuesta (y corregirla)",
)
def feedback(
    payload: FeedbackRequest, conn: DbDep, user: CurrentUserDep
) -> FeedbackResponse:
    """A thumbs-down with a reason becomes memory; without one it is just a tally.

    Nothing here trains a model. The comment is stored as a fact and read back
    into later prompts, which is what actually stops the same mistake twice.
    """
    from ai.features import record_feedback

    result = record_feedback(
        conn,
        user.tenant_id,
        payload.message_id,
        helpful=payload.helpful,
        comment=payload.comment,
        user_id=user.id,
    )
    if not result["stored"]:
        raise NotFound(result["message"])
    return FeedbackResponse(**result)


@router.get(
    "/recommendations",
    response_model=RecommendationsResponse,
    summary="Qué hacer ahora, con el dinero que cuesta no hacerlo",
)
async def recommendations(
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    country: OptionalCountryQuery = None,
    narrative: Annotated[
        bool, Query(description="Agrega un párrafo del modelo sobre los hallazgos.")
    ] = False,
) -> RecommendationsResponse:
    """Detection is SQL; the model, if asked for and available, only phrases it.

    That ordering is the whole point. With `narrative=false` - the default - this
    endpoint never touches the network, so recommendations survive an expired
    key, an exhausted budget and an outage at the provider. With
    `narrative=true` the paragraph degrades on its own and the findings below it
    are unaffected.
    """
    from ai.client import AiUnavailable
    from ai.recommendations import detect, narrate

    country_code = country.upper() if country else None
    found = await asyncio.to_thread(detect, conn, country_code)

    paragraph: str | None = None
    degraded = False
    degraded_reason: str | None = None
    if narrative and found:
        try:
            paragraph = await asyncio.to_thread(
                narrate, conn, settings, user.tenant_id, country_code, found
            )
        except AiUnavailable as exc:
            degraded = True
            degraded_reason = exc.reason
            logger.info("recommendation narrative degraded: %s", exc.reason)

    return RecommendationsResponse(
        country_code=country_code,
        recommendations=[RecommendationResponse(**item) for item in found],
        narrative=paragraph,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


@router.get("/usage", summary="Consumo de tokens de hoy")
def usage(conn: DbDep, user: CurrentUserDep, settings: SettingsDep) -> dict:
    from ai.client import tokens_used_today

    used = tokens_used_today(conn, user.tenant_id)
    return {
        "tokens_used_today": used,
        "daily_budget": settings.ai_daily_token_budget,
        "remaining": max(0, settings.ai_daily_token_budget - used),
        "ai_enabled": settings.ai_enabled,
    }
