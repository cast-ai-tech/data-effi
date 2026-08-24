"""The bell: what the detectors found, whether this person has seen it.

Read state is PER USER. The notification row belongs to the company; the
`read_at` on it belongs to whoever clicked. Two partners share one tenant and
the one who reads at 7 am must not silence the bell for the one who logs in
at 9 - so every query here LEFT JOINs raw.notification_state on the caller's
own id and nothing else.

Country scope applies twice: `db_for_user` refuses a `country` outside the
membership before this file runs, and the multi-country listing filters rows
by `country_scope_sql` the same way the KPI roll-ups do.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from api.db import execute, fetch_all, fetch_one
from api.deps import CurrentUser, CurrentUserDep, DbDep, country_scope_sql, tenant_of
from api.errors import ApiError, NotFound
from api.schemas import (
    THRESHOLD_KEYS,
    NotificationItem,
    NotificationsResponse,
    ReadAllResponse,
    ThresholdRow,
    ThresholdsResponse,
    ThresholdsUpdateRequest,
    UnreadCountResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

CountryQuery = Annotated[str, Query(min_length=2, max_length=2, description="Código ISO del país")]
OptionalCountryQuery = Annotated[str | None, Query(min_length=2, max_length=2)]

MAX_PAGE = 100

# A user-set threshold is a sentence or a number the operator typed. Past this
# it is a document, and documents crowd the numbers out of the prompt.
MAX_THRESHOLD_CHARS = 300


# =============================================================================
# Listing and counts
# =============================================================================


@router.get("", response_model=NotificationsResponse, summary="Centro de notificaciones")
def list_notifications(
    conn: DbDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 30,
    before: Annotated[int | None, Query(ge=1, description="Trae las anteriores a este id")] = None,
    country: OptionalCountryQuery = None,
    unread_only: bool = False,
) -> NotificationsResponse:
    tenant_id = tenant_of(user)
    where, params = _where(user, country)
    if before is not None:
        where += " AND n.id < %s"
        params.append(before)
    if unread_only:
        where += " AND s.read_at IS NULL"

    rows = fetch_all(
        conn,
        f"""
        SELECT n.id, n.kind, n.code, n.severity, n.country_code, n.title, n.finding,
               n.action, n.impact_amount, n.impact_currency, n.deep_link, n.created_at,
               n.payload, s.read_at
        FROM raw.notification n
        LEFT JOIN raw.notification_state s
               ON s.notification_id = n.id AND s.user_id = %s
        WHERE n.tenant_id = %s AND s.dismissed_at IS NULL{where}
        ORDER BY n.id DESC
        LIMIT %s
        """,
        (user.id, tenant_id, *params, limit + 1),
    )

    # One extra row tells us whether there is a next page without a COUNT.
    has_more = len(rows) > limit
    rows = rows[:limit]
    counts = _counts(conn, user, country)

    return NotificationsResponse(
        items=[NotificationItem(**_item(row)) for row in rows],
        unread_count=counts["unread_count"],
        critical_unread_count=counts["critical_unread_count"],
        next_before=int(rows[-1]["id"]) if has_more and rows else None,
    )


@router.get(
    "/unread-count", response_model=UnreadCountResponse, summary="Cuántas sin leer"
)
def unread_count(
    conn: DbDep, user: CurrentUserDep, country: OptionalCountryQuery = None
) -> UnreadCountResponse:
    return UnreadCountResponse(**_counts(conn, user, country))


# =============================================================================
# Thresholds. Declared BEFORE `/{notification_id}` so "thresholds" is never
# parsed as an id.
# =============================================================================


@router.get(
    "/thresholds", response_model=ThresholdsResponse, summary="Umbrales de este país"
)
def get_thresholds(conn: DbDep, user: CurrentUserDep, country: CountryQuery) -> ThresholdsResponse:
    """The four normals the detectors compare against: inferred, or set by hand."""
    from ai.memory import ensure_thresholds

    tenant_id = tenant_of(user)
    country_code = country.upper()
    ensure_thresholds(conn, tenant_id, country_code)
    return _thresholds_response(conn, tenant_id, country_code)


@router.put(
    "/thresholds", response_model=ThresholdsResponse, summary="Fijar umbrales a mano"
)
def put_thresholds(
    payload: ThresholdsUpdateRequest,
    conn: DbDep,
    user: CurrentUserDep,
    country: CountryQuery,
) -> ThresholdsResponse:
    """A value the operator typed outranks the inferred one, and never expires.

    An EMPTY value is "reset": the hand-set row goes away and the inferred one
    is derived again from the data, so the panel's "Restablecer" is one PUT.
    """
    from ai.memory import infer_thresholds, remember

    tenant_id = tenant_of(user)
    country_code = country.upper()

    unknown = sorted(set(payload.thresholds) - set(THRESHOLD_KEYS))
    if unknown:
        raise ApiError(
            "invalid_threshold",
            f"Umbral desconocido: {', '.join(unknown)}. "
            f"Válidos: {', '.join(THRESHOLD_KEYS)}.",
        )

    reset_any = False
    for key, value in payload.thresholds.items():
        text = (value or "").strip()
        if not text:
            execute(
                conn,
                """
                DELETE FROM raw.ai_memory
                WHERE tenant_id = %s AND country_code = %s AND kind = 'threshold' AND key = %s
                """,
                (tenant_id, country_code, key),
            )
            reset_any = True
            continue
        if len(text) > MAX_THRESHOLD_CHARS:
            raise ApiError(
                "invalid_threshold",
                f"El valor de '{key}' es demasiado largo (máximo {MAX_THRESHOLD_CHARS} caracteres).",
            )
        remember(
            conn, tenant_id, "threshold", key, text,
            country_code=country_code, source="user", confidence=1.0,
            created_by=user.id, expires_at=None,
        )

    if reset_any:
        infer_thresholds(conn, tenant_id, country_code)

    return _thresholds_response(conn, tenant_id, country_code)


# =============================================================================
# Read / dismiss
# =============================================================================


@router.post(
    "/read-all", response_model=ReadAllResponse, summary="Marcar todas como leídas"
)
def read_all(
    conn: DbDep, user: CurrentUserDep, country: OptionalCountryQuery = None
) -> ReadAllResponse:
    tenant_id = tenant_of(user)
    where, params = _where(user, country)
    marked = execute(
        conn,
        f"""
        INSERT INTO raw.notification_state (notification_id, user_id, tenant_id, read_at)
        SELECT n.id, %s, n.tenant_id, now()
        FROM raw.notification n
        LEFT JOIN raw.notification_state s
               ON s.notification_id = n.id AND s.user_id = %s
        WHERE n.tenant_id = %s AND s.dismissed_at IS NULL AND s.read_at IS NULL{where}
        ON CONFLICT (notification_id, user_id) DO UPDATE
            SET read_at = coalesce(raw.notification_state.read_at, now())
        """,
        (user.id, user.id, tenant_id, *params),
    )
    return ReadAllResponse(marked=marked)


@router.post(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Marcar una como leída",
)
def mark_read(notification_id: int, conn: DbDep, user: CurrentUserDep) -> Response:
    _set_state(conn, user, notification_id, dismiss=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{notification_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Descartar una notificación",
)
def dismiss(notification_id: int, conn: DbDep, user: CurrentUserDep) -> Response:
    """Dismissed is read plus hidden - for this person only."""
    _set_state(conn, user, notification_id, dismiss=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Helpers
# =============================================================================


def _where(user: CurrentUser, country: str | None) -> tuple[str, list[Any]]:
    """Country filter plus membership scope, as an ` AND ...` fragment."""
    where = ""
    params: list[Any] = []
    if country:
        where += " AND n.country_code = %s"
        params.append(country.upper())
    # A digest or a system notice with no country is visible to everyone in
    # the company; a limited membership just cannot see other countries' rows.
    scope, scope_params = country_scope_sql(user, "n.country_code", include_global=True)
    where += scope
    params.extend(scope_params)
    return where, params


def _counts(conn, user: CurrentUser, country: str | None) -> dict[str, int]:
    where, params = _where(user, country)
    row = fetch_one(
        conn,
        f"""
        SELECT count(*) FILTER (WHERE s.read_at IS NULL)                AS unread_count,
               count(*) FILTER (WHERE s.read_at IS NULL
                                  AND n.severity = 'critical')          AS critical_unread_count
        FROM raw.notification n
        LEFT JOIN raw.notification_state s
               ON s.notification_id = n.id AND s.user_id = %s
        WHERE n.tenant_id = %s AND s.dismissed_at IS NULL{where}
        """,
        (user.id, tenant_of(user), *params),
    )
    return {
        "unread_count": int(row["unread_count"] or 0) if row else 0,
        "critical_unread_count": int(row["critical_unread_count"] or 0) if row else 0,
    }


def _set_state(conn, user: CurrentUser, notification_id: int, *, dismiss: bool) -> None:
    dismissed_expr = "now()" if dismiss else "raw.notification_state.dismissed_at"
    affected = execute(
        conn,
        f"""
        INSERT INTO raw.notification_state
            (notification_id, user_id, tenant_id, read_at, dismissed_at)
        SELECT n.id, %s, n.tenant_id, now(), {"now()" if dismiss else "NULL"}
        FROM raw.notification n
        WHERE n.id = %s AND n.tenant_id = %s
        ON CONFLICT (notification_id, user_id) DO UPDATE
            SET read_at      = coalesce(raw.notification_state.read_at, now()),
                dismissed_at = {dismissed_expr}
        """,
        (user.id, notification_id, tenant_of(user)),
    )
    if affected == 0:
        raise NotFound("Esa notificación no existe en esta cuenta.")


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "impact_amount": float(row["impact_amount"]) if row["impact_amount"] is not None else None,
        "payload": row["payload"] or {},
    }


def _thresholds_response(conn, tenant_id, country_code: str) -> ThresholdsResponse:
    rows = fetch_all(
        conn,
        """
        SELECT key, value, source, confidence, updated_at
        FROM raw.ai_memory
        WHERE tenant_id = %s AND country_code = %s AND kind = 'threshold'
          AND key = ANY(%s)
          AND (expires_at IS NULL OR expires_at > now())
        ORDER BY key
        """,
        (tenant_id, country_code, list(THRESHOLD_KEYS)),
    )
    return ThresholdsResponse(
        country_code=country_code,
        thresholds=[
            ThresholdRow(
                key=row["key"],
                value=row["value"],
                source=row["source"],
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                updated_at=_as_datetime(row["updated_at"]),
            )
            for row in rows
        ],
    )


def _as_datetime(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
