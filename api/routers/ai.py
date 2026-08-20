"""Copilot endpoints.

Every one of these degrades instead of failing: if the model is unreachable, the
budget is gone, or the key is missing, the response says so plainly and the
dashboard carries on. The AI is an assistant to the numbers, never a dependency
of them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import CurrentUserDep, DbDep, SettingsDep
from api.schemas import AlertResponse, AlertsResponse, AskRequest, AskResponse, BriefResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

CountryQuery = Annotated[str, Query(min_length=2, max_length=2)]


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
