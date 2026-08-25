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

from api.deps import CurrentUserDep, DbDep, SettingsDep, tenant_of
from api.errors import Forbidden, NotFound
from api.schemas import (
    AlertResponse,
    AlertsResponse,
    AskRequest,
    AskResponse,
    BriefResponse,
    ConversationDetail,
    ConversationsResponse,
    ConversationSummary,
    DecisionItem,
    DecisionScope,
    DecisionsResponse,
    FeedbackRequest,
    FeedbackResponse,
    RecommendationResponse,
    RecommendationsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


def _only_scoped(items: list[dict], user) -> list[dict]:
    """Cut a list of findings to the caller's country scope.

    `country` is optional on these endpoints, so the door guard cannot help
    when it is omitted: a limited membership would get every country's
    alerts. A finding that names no country cannot be attributed and is
    dropped for them too.
    """
    if user.countries is None:
        return items
    allowed = {c.upper() for c in user.countries}
    return [
        item for item in items
        if item.get("country_code") and str(item["country_code"]).upper() in allowed
    ]

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
            generate_brief, conn, settings, tenant_of(user), country_code
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

    found = _only_scoped(collect_alerts(conn, country), user)
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

    # The question travels in the body, so the door guard never sees a
    # `country`. A limited membership must name one of its countries, and the
    # rows that come back are cut to the scope besides: the generated SQL is
    # not trusted to have honoured the hint.
    if user.countries is not None:
        if not payload.country_code:
            raise Forbidden(
                f"Elige un país para preguntar. Tu usuario solo tiene acceso a: "
                f"{', '.join(user.countries)}."
            )
        if not user.may_read_country(payload.country_code):
            raise Forbidden(
                f"Tu usuario solo tiene acceso a: {', '.join(user.countries)}."
            )

    try:
        result = await asyncio.to_thread(
            ask_data,
            conn,
            settings,
            tenant_of(user),
            payload.question,
            payload.country_code.upper() if payload.country_code else None,
            conversation_id=payload.conversation_id,
            user_id=user.id,
            allowed_countries=user.countries,
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

    rows = list_conversations(conn, tenant_of(user))
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

    found = get_conversation(conn, tenant_of(user), conversation_id)
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
        tenant_of(user),
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
    found = _only_scoped(await asyncio.to_thread(detect, conn, country_code), user)

    paragraph: str | None = None
    degraded = False
    degraded_reason: str | None = None
    if narrative and found:
        try:
            paragraph = await asyncio.to_thread(
                narrate, conn, settings, tenant_of(user), country_code, found
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


@router.get(
    "/decisions",
    response_model=DecisionsResponse,
    summary="Qué hacer con cada producto, zona, oficina y la caja",
)
async def decisions(
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    country: CountryQuery,
    scope: Annotated[DecisionScope, Query()] = "products",
    narrative: Annotated[
        bool, Query(description="Agrega un párrafo del modelo sobre los veredictos.")
    ] = False,
) -> DecisionsResponse:
    """Verdicts are arithmetic over the mart views; the paragraph is optional.

    Same contract as `/ai/recommendations`: with `narrative=false` nothing here
    touches the network, and with `narrative=true` the paragraph degrades on
    its own while the verdicts below it stay.
    """
    from ai.client import AiUnavailable
    from ai.decisions import as_findings, build_decisions
    from ai.recommendations import narrate

    country_code = country.upper()
    tenant_id = tenant_of(user)
    result = await asyncio.to_thread(build_decisions, conn, tenant_id, country_code, scope)
    items = result["items"]

    paragraph: str | None = None
    degraded = False
    degraded_reason: str | None = None
    if narrative and items:
        try:
            paragraph = await asyncio.to_thread(
                narrate, conn, settings, tenant_id, country_code, as_findings(scope, items[:8])
            )
        except AiUnavailable as exc:
            degraded = True
            degraded_reason = exc.reason
            logger.info("decision narrative degraded: %s", exc.reason)

    return DecisionsResponse(
        scope=scope,
        country_code=country_code,
        items=[DecisionItem(**item) for item in items],
        thresholds=result["thresholds"],
        narrative=paragraph,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


@router.get("/usage", summary="Consumo de tokens de hoy")
def usage(conn: DbDep, user: CurrentUserDep, settings: SettingsDep) -> dict:
    from ai.client import tokens_used_today

    used = tokens_used_today(conn, tenant_of(user))
    return {
        "tokens_used_today": used,
        "daily_budget": settings.ai_daily_token_budget,
        "remaining": max(0, settings.ai_daily_token_budget - used),
        "ai_enabled": settings.ai_enabled,
    }
