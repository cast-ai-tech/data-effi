"""Plans and the organisation's subscription.

Reachable while blocked on purpose: it is the one screen a person whose free
month ended must still be able to open. Everything here runs on the unscoped
connection - core.plan and core.org_subscription sit outside row-level
security, like core.org - and is filtered by the caller's org.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.billing import advisor_whatsapp_url, choose_plan, list_plans, subscription_state
from api.db import fetch_one
from api.deps import CurrentUser, CurrentUserDep, SettingsDep, UnscopedDbDep
from api.errors import Forbidden
from api.schemas import BillingResponse, ChoosePlanRequest, PlanRow, SubscriptionSummary

router = APIRouter(prefix="/billing", tags=["billing"])


def _require_org(user: CurrentUserDep) -> CurrentUser:
    if user.org_id is None:
        raise Forbidden("Tu usuario no pertenece a ninguna organización.")
    return user


def _require_org_admin(user: CurrentUserDep) -> CurrentUser:
    if user.org_id is None or not user.manages_org():
        raise Forbidden("Solo el administrador de la organización elige el plan.")
    return user


OrgUser = Annotated[CurrentUser, Depends(_require_org)]
OrgAdmin = Annotated[CurrentUser, Depends(_require_org_admin)]


def _response(conn, settings, user: CurrentUser) -> BillingResponse:
    assert user.org_id is not None
    org = fetch_one(conn, "SELECT name FROM core.org WHERE id = %s", (user.org_id,))
    state = subscription_state(conn, user.org_id)
    return BillingResponse(
        plans=[PlanRow(**row) for row in list_plans(conn)],
        subscription=SubscriptionSummary(**state.as_dict()),
        advisor_whatsapp_url=advisor_whatsapp_url(
            settings.advisor_whatsapp, org["name"] if org else None
        ),
        can_choose=user.manages_org(),
    )


@router.get("", response_model=BillingResponse, summary="Planes y estado de la suscripción")
def billing(conn: UnscopedDbDep, settings: SettingsDep, user: OrgUser) -> BillingResponse:
    return _response(conn, settings, user)


@router.post("/choose", response_model=BillingResponse, summary="Elegir un plan")
def choose(
    payload: ChoosePlanRequest, conn: UnscopedDbDep, settings: SettingsDep, user: OrgAdmin
) -> BillingResponse:
    """Records the choice. Nothing is charged here: an advisor activates it."""
    assert user.org_id is not None
    choose_plan(conn, user.org_id, payload.plan_code)
    return _response(conn, settings, user)
