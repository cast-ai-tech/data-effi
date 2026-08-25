"""Plans and subscriptions: the free month, the three plans, the advisor.

One place decides whether an organisation may still use the product and how
many companies it may hold. `db_for_user` asks it on every data request, the
org router asks it before creating a company, the billing router shows it,
and `scripts/activate_plan.py` is how an advisor turns a `pending` choice into
an `active` plan. Nothing here talks to a payment provider: billing is manual
for now, on purpose (migration 048).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID

import psycopg

from api.db import execute, fetch_all, fetch_one, fetch_required

logger = logging.getLogger(__name__)

TRIAL_DAYS = 30
# What a free month and a plan that is only requested may hold.
TRIAL_MAX_TENANTS = 1

STATUS_LABELS = {
    "trial": "Prueba gratis",
    "pending": "Plan elegido, pendiente de activación",
    "active": "Plan activo",
    "expired": "Plan vencido",
}


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    status: str
    plan_code: str | None
    plan_name: str | None
    requested_plan_code: str | None
    requested_plan_name: str | None
    trial_ends_at: datetime
    current_period_end: datetime | None
    days_left: int | None
    max_tenants: int | None
    tenants_used: int
    blocked: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_plans(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        "SELECT code, name, price_usd, max_tenants, is_custom "
        "FROM core.plan WHERE is_active ORDER BY sort_order",
    )


def start_trial(conn: psycopg.Connection, org_id: UUID, *, days: int = TRIAL_DAYS) -> None:
    """Every new organisation starts on the free month. Idempotent."""
    execute(
        conn,
        """
        INSERT INTO core.org_subscription (org_id, status, trial_ends_at)
        VALUES (%s, 'trial', now() + make_interval(days => %s))
        ON CONFLICT (org_id) DO NOTHING
        """,
        (org_id, days),
    )


def subscription_state(conn: psycopg.Connection, org_id: UUID) -> SubscriptionState:
    """Where this organisation stands, and whether the door is open."""
    row = fetch_one(
        conn,
        """
        SELECT s.status, s.plan_code, p.name AS plan_name, p.max_tenants AS plan_max,
               s.requested_plan_code, rp.name AS requested_plan_name,
               s.trial_ends_at, s.current_period_end,
               (SELECT count(*) FROM core.tenant t WHERE t.org_id = s.org_id AND t.is_active)
                   AS tenants_used
        FROM core.org_subscription s
        LEFT JOIN core.plan p  ON p.code = s.plan_code
        LEFT JOIN core.plan rp ON rp.code = s.requested_plan_code
        WHERE s.org_id = %s
        """,
        (org_id,),
    )
    if row is None:
        # An organisation older than migration 048's backfill, or created by a
        # path that skipped start_trial. Give it the month rather than the door.
        start_trial(conn, org_id)
        return subscription_state(conn, org_id)

    now = datetime.now(UTC)
    status = row["status"]
    trial_ends_at: datetime = row["trial_ends_at"]
    period_end: datetime | None = row["current_period_end"]

    def days_until(moment: datetime) -> int:
        # Whole days still ahead, rounded UP: right after registering the
        # month reads "30 días", not "29".
        seconds = (moment - now).total_seconds()
        return max(0, -(-int(seconds) // 86400))

    if status == "active":
        blocked = period_end is not None and period_end < now
        days_left = days_until(period_end) if period_end else None
        max_tenants = row["plan_max"]           # NULL = custom, no limit
    elif status == "expired":
        blocked, days_left, max_tenants = True, 0, TRIAL_MAX_TENANTS
    else:                                       # trial | pending
        blocked = trial_ends_at < now
        days_left = days_until(trial_ends_at)
        max_tenants = TRIAL_MAX_TENANTS

    if blocked and status == "active":
        message = "Tu plan venció. Renueva con tu asesor para seguir entrando."
    elif blocked and status == "pending":
        message = (
            "Tu mes gratis terminó. Ya elegiste un plan: apenas un asesor lo active "
            "vuelves a entrar."
        )
    elif blocked:
        message = "Tu mes gratis terminó. Elige un plan para seguir usando Data Effi."
    elif status == "trial":
        message = f"Prueba gratis: te quedan {days_left} días."
    elif status == "pending":
        message = f"Elegiste {row['requested_plan_name']}. Un asesor lo activa; mientras, sigues en tu mes gratis ({days_left} días)."
    else:
        message = f"Plan {row['plan_name']} activo."

    return SubscriptionState(
        status=status,
        plan_code=row["plan_code"],
        plan_name=row["plan_name"],
        requested_plan_code=row["requested_plan_code"],
        requested_plan_name=row["requested_plan_name"],
        trial_ends_at=trial_ends_at,
        current_period_end=period_end,
        days_left=days_left,
        max_tenants=max_tenants,
        tenants_used=int(row["tenants_used"]),
        blocked=blocked,
        message=message,
    )


def choose_plan(conn: psycopg.Connection, org_id: UUID, plan_code: str) -> SubscriptionState:
    """The customer picks a plan. Nothing is charged: an advisor activates it."""
    plan = fetch_one(conn, "SELECT code FROM core.plan WHERE code = %s AND is_active", (plan_code,))
    if plan is None:
        from api.errors import NotFound

        raise NotFound(f"No existe el plan '{plan_code}'.")
    subscription_state(conn, org_id)      # makes sure the row exists
    execute(
        conn,
        """
        UPDATE core.org_subscription
           SET requested_plan_code = %s,
               requested_at = now(),
               status = CASE WHEN status = 'active' THEN status ELSE 'pending' END,
               updated_at = now()
         WHERE org_id = %s
        """,
        (plan_code, org_id),
    )
    logger.info("org %s requested plan %s", org_id, plan_code)
    return subscription_state(conn, org_id)


def activate_plan(
    conn: psycopg.Connection,
    org_id: UUID,
    plan_code: str,
    *,
    months: int | None = 1,
    activated_by: UUID | None = None,
    notes: str | None = None,
) -> SubscriptionState:
    """What the advisor does after the customer paid. `months=None` = no end date."""
    fetch_required(conn, "SELECT code FROM core.plan WHERE code = %s AND is_active", (plan_code,))
    subscription_state(conn, org_id)
    period_end = None if months is None else datetime.now(UTC) + timedelta(days=30 * months)
    execute(
        conn,
        """
        UPDATE core.org_subscription
           SET status = 'active', plan_code = %s, requested_plan_code = NULL,
               current_period_end = %s, activated_at = now(), activated_by = %s,
               notes = COALESCE(%s, notes), updated_at = now()
         WHERE org_id = %s
        """,
        (plan_code, period_end, activated_by, notes, org_id),
    )
    logger.info("org %s activated on plan %s until %s", org_id, plan_code, period_end)
    return subscription_state(conn, org_id)


def expire_subscription(conn: psycopg.Connection, org_id: UUID, *, notes: str | None = None) -> None:
    execute(
        conn,
        "UPDATE core.org_subscription SET status = 'expired', notes = COALESCE(%s, notes), "
        "updated_at = now() WHERE org_id = %s",
        (notes, org_id),
    )


def advisor_whatsapp_url(number: str | None, org_name: str | None = None) -> str | None:
    """A wa.me link with the message already written, or None when not configured."""
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isdigit())
    if not digits:
        return None
    who = f" para {org_name}" if org_name else ""
    text = f"Hola, quiero un plan a la medida de Data Effi{who}."
    return f"https://wa.me/{digits}?text={quote(text)}"
