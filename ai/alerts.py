"""Findings that stay: writing what the detectors found into raw.notification.

Detection happens elsewhere and is SQL (`ai/recommendations.detect`,
`ai/features.collect_alerts`). This module only decides two things:

  1. IS THIS NEW?  The same product below its break-even is found after every
     load. A fingerprint over (code, country, subject) and a three-day window
     turn "found again" into "already told them" - the operator gets one
     notification, not one per upload.
  2. WHAT DOES THE BROWSER HEAR?  Every insert appends a `notification.created`
     event so an open tab can bump its counter without reloading.

Nothing here calls a model. The daily digest MAY carry a paragraph the brief
wrote, but it is handed in as text; if it is missing the digest still exists.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from api.db import fetch_one, fetch_required
from api.events import emit

logger = logging.getLogger(__name__)

# Inside this window a finding with the same fingerprint is the same finding.
# Three days: long enough that four loads a day do not repeat it, short enough
# that a problem still there next week gets said again.
DEDUP_DAYS = 3

# A digest is a page, not a headline. Everything past this is cut so the row
# stays a summary and the panel stays readable.
MAX_DIGEST_CHARS = 1_500


def fingerprint(code: str, country: str | None, subject: str) -> str:
    """Stable identity of a finding: same code, same country, same subject."""
    raw = f"{code}|{(country or '').upper()}|{subject.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def persist_findings(
    conn: psycopg.Connection,
    tenant_id: UUID,
    country_code: str | None,
    findings: list[dict[str, Any]],
    *,
    kind: str = "urgent",
    dedup_days: int = DEDUP_DAYS,
) -> list[int]:
    """Store each finding once per window. Returns the ids actually inserted.

    A finding is the dict shape `detect()` and `collect_alerts()` produce:
    code, severity, title, finding, action, impact_amount, impact_currency,
    deep_link, and optionally country_code. The title is the subject: it names
    the product, carrier or zone, which is what makes two findings "the same".
    """
    created: list[int] = []
    for item in findings:
        country = (item.get("country_code") or country_code or None)
        digest = fingerprint(item["code"], country, item["title"])

        already = fetch_one(
            conn,
            """
            SELECT 1 AS found
            FROM raw.notification
            WHERE tenant_id = %s AND fingerprint = %s
              AND created_at > now() - make_interval(days => %s)
            LIMIT 1
            """,
            (tenant_id, digest, dedup_days),
        )
        if already:
            continue

        notification_id = _insert(
            conn,
            tenant_id=tenant_id,
            country_code=country,
            kind=kind,
            code=item["code"],
            severity=item["severity"],
            title=item["title"],
            finding=item["finding"],
            action=item["action"],
            impact_amount=item.get("impact_amount"),
            impact_currency=item.get("impact_currency"),
            deep_link=item.get("deep_link"),
            digest=digest,
            payload={},
        )
        created.append(notification_id)
    return created


def persist_digest(
    conn: psycopg.Connection,
    tenant_id: UUID,
    country_code: str,
    *,
    brief: str | None,
    recommendations: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    local_date: date,
) -> int | None:
    """One digest per country per local day. Returns its id, or None if it exists.

    Idempotent by construction: the fingerprint is `digest|CC|date`, so the
    hourly cron that fires four times to catch every timezone writes once.
    """
    country = country_code.upper()
    digest = fingerprint("digest", country, local_date.isoformat())

    already = fetch_one(
        conn,
        "SELECT id FROM raw.notification WHERE tenant_id = %s AND fingerprint = %s LIMIT 1",
        (tenant_id, digest),
    )
    if already:
        return None

    items = [*recommendations, *alerts]
    worst = max((_impact(item) for item in items), default=None)
    currency = next(
        (item.get("impact_currency") for item in items if item.get("impact_currency")), None
    )
    first_action = next((item["action"] for item in items if item.get("action")), None)

    if brief:
        finding = brief.strip()[:MAX_DIGEST_CHARS]
    elif items:
        finding = _fallback_summary(items)
    else:
        finding = (
            "Ningún detector encontró algo que corregir hoy. Los números del tablero "
            "siguen siendo la fuente."
        )

    notification_id = _insert(
        conn,
        tenant_id=tenant_id,
        country_code=country,
        kind="digest",
        code="daily_digest",
        # A digest is never critical: the critical findings inside it already
        # went out as their own urgent notification the moment they appeared.
        severity="warning" if items else "info",
        title=f"Resumen del {local_date.strftime('%d/%m')} · {country}",
        finding=finding,
        action=first_action or "Abre el tablero del país y revisa la efectividad de hoy.",
        impact_amount=worst,
        impact_currency=currency,
        deep_link=f"/{country.lower()}",
        digest=digest,
        payload={
            "date": local_date.isoformat(),
            "brief_available": bool(brief),
            "findings": [_compact(item) for item in items[:12]],
        },
    )
    return notification_id


def _insert(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    country_code: str | None,
    kind: str,
    code: str,
    severity: str,
    title: str,
    finding: str,
    action: str,
    impact_amount: Any,
    impact_currency: str | None,
    deep_link: str | None,
    digest: str,
    payload: dict[str, Any],
) -> int:
    row = fetch_required(
        conn,
        """
        INSERT INTO raw.notification
            (tenant_id, country_code, kind, code, severity, title, finding, action,
             impact_amount, impact_currency, deep_link, fingerprint, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tenant_id, country_code, kind, code, severity, title[:300], finding, action,
            _impact_value(impact_amount), impact_currency, deep_link, digest, Json(payload),
        ),
    )
    notification_id = int(row["id"])
    emit(
        conn,
        tenant_id,
        "notification.created",
        country_code=country_code,
        payload={"notification_id": notification_id, "severity": severity, "kind": kind},
    )
    return notification_id


def _impact(item: dict[str, Any]) -> float:
    value = item.get("impact_amount")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _impact_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    """The part of a finding the panel lists under the digest: no long text."""
    return {
        "code": item.get("code"),
        "severity": item.get("severity"),
        "title": item.get("title"),
        "impact_amount": _impact_value(item.get("impact_amount")),
        "impact_currency": item.get("impact_currency"),
        "deep_link": item.get("deep_link"),
    }


def _fallback_summary(items: list[dict[str, Any]]) -> str:
    """When there is no brief: the findings, one line each, worst first."""
    ordered = sorted(items, key=_impact, reverse=True)
    lines = [f"- {item['title']}" for item in ordered[:6]]
    header = (
        f"{len(items)} hallazgo{'s' if len(items) != 1 else ''} para revisar hoy:"
    )
    return "\n".join([header, *lines])[:MAX_DIGEST_CHARS]
