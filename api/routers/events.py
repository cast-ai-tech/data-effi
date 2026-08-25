"""Long-poll over raw.event: "anything after this id?", answered within seconds.

THE SHAPE OF THE WAIT. The request may hold for up to `wait` seconds, but the
database connection never does. Each iteration checks out a connection, runs
one indexed SELECT, and hands it back before sleeping - so forty open tabs
cost forty sleeping coroutines, not forty of the pool's connections. A `wait`
of six fits under the ten-second function limit the web's serverless host
enforces at its lowest tier (Vercel Hobby, Netlify) with room for a cold start;
eight is the hard ceiling for the same reason.

WHO HEARS WHAT. An `uploader` may watch their own loads and nothing else, so
without `read` the feed is cut to `upload_job.*`. A membership limited to some
countries only hears events tagged with those countries, or with none.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from api.db import connection, fetch_all, fetch_one
from api.deps import CurrentUser, CurrentUserDep, tenant_of
from api.events import EVENT_TYPES
from api.schemas import EventItem, EventsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

DEFAULT_WAIT = 6
MAX_WAIT = 8
POLL_INTERVAL = 1.0
PAGE = 100


@router.get("", response_model=EventsResponse, summary="Cambios desde un cursor (long-poll)")
async def events(
    user: CurrentUserDep,
    since: Annotated[int | None, Query(ge=0, description="Último id recibido")] = None,
    wait: Annotated[int, Query(ge=0, le=MAX_WAIT, description="Segundos máximos de espera")] = DEFAULT_WAIT,
    types: Annotated[str | None, Query(description="Tipos, separados por coma")] = None,
) -> EventsResponse:
    """Without `since`: the current cursor and no events - the place to start.

    With it: every event after that id, or an empty list once `wait` runs out.
    The caller passes the returned `cursor` back as `since` and loops.
    """
    tenant_id = tenant_of(user)

    if since is None:
        cursor = await asyncio.to_thread(_current_cursor, tenant_id)
        return EventsResponse(cursor=cursor, events=[])

    wanted = _parse_types(types)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait

    while True:
        rows = await asyncio.to_thread(_fetch, user, tenant_id, since, wanted)
        remaining = deadline - loop.time()
        if rows or remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL, remaining))

    cursor = int(rows[-1]["id"]) if rows else since
    return EventsResponse(cursor=cursor, events=[EventItem(**row) for row in rows])


def _parse_types(raw: str | None) -> list[str]:
    """The CSV filter, kept to known types so a typo is empty rather than a scan."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip() in EVENT_TYPES]


def _current_cursor(tenant_id: UUID) -> int:
    with connection(tenant_id) as conn:
        row = fetch_one(
            conn,
            "SELECT coalesce(max(id), 0) AS cursor FROM raw.event WHERE tenant_id = %s",
            (tenant_id,),
        )
    return int(row["cursor"]) if row else 0


def _fetch(
    user: CurrentUser, tenant_id: UUID, since: int, wanted: list[str]
) -> list[dict[str, Any]]:
    """One short transaction: read forward from the cursor and let go."""
    query, params = _query(user, tenant_id, since, wanted)
    with connection(tenant_id) as conn:
        rows = fetch_all(conn, query, tuple(params))
    for row in rows:
        row["payload"] = row["payload"] or {}
    return rows


def _query(
    user: CurrentUser, tenant_id: UUID, since: int, wanted: list[str]
) -> tuple[str, list[Any]]:
    """The SELECT and its bound values, so the filter can be tested without the pool."""
    clauses = ["tenant_id = %s", "id > %s"]
    params: list[Any] = [tenant_id, since]

    if not user.can("read"):
        # The one screen an uploader may use only needs to know about loads.
        clauses.append("type LIKE 'upload_job.%%'")
    if wanted:
        clauses.append("type = ANY(%s)")
        params.append(wanted)
    if user.countries is not None:
        clauses.append("(country_code IS NULL OR upper(country_code) = ANY(%s))")
        params.append(list(user.countries))

    query = (
        "SELECT id, type, country_code, payload, created_at FROM raw.event "
        f"WHERE {' AND '.join(clauses)} ORDER BY id LIMIT {PAGE}"
    )
    return query, params
