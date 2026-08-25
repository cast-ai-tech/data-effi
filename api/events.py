"""The change feed the browser long-polls.

WHY A TABLE AND NOT A SOCKET. The API runs as one process on Render's free
plan, the web app sits behind serverless functions (Vercel today, Netlify
before) that buffer every response and cut it after seconds, and the database
is reached through a transaction pooler that cannot LISTEN. None of those can
hold a connection open. What all of them can do is answer "anything after id
4812?" in a few milliseconds - so that is the protocol: append here, and
`GET /events?since=` reads forward.

WHAT GOES IN A PAYLOAD. Identifiers and statuses, never text. A notification
event says "notification 17 is critical"; the title lives in raw.notification
behind its own guard. An upload event says which job and which status; the
error message stays on the job row. Anyone who can read the feed can already
read those rows, and the feed stays small enough to keep for a week.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from api.db import fetch_required

logger = logging.getLogger(__name__)

# Every type the feed carries. A typo here would be an event nobody
# subscribes to, so the set is closed and `emit` refuses anything outside it.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "upload_job.updated",     # {job_id, status, kind, filename, batch_id?, error?}
        "batch.finished",         # {batch_id, kind, country_code}
        "notification.created",   # {notification_id, severity, kind}
        "job_run.finished",       # {job, ok}
        "fx.refreshed",           # {}
    }
)


def emit(
    conn: psycopg.Connection,
    tenant_id: UUID,
    type: str,
    *,
    country_code: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append one event for this tenant. Returns its id.

    Runs inside the caller's transaction on purpose: an upload that is marked
    `done` and an event that says so must be one commit, or the screen will
    hear about a load it cannot yet see.
    """
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")

    row = fetch_required(
        conn,
        """
        INSERT INTO raw.event (tenant_id, type, country_code, payload)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (tenant_id, type, country_code.upper() if country_code else None,
         Json(_jsonable(payload or {}))),
    )
    return int(row["id"])


def _jsonable(value: Any) -> Any:
    """UUIDs and dates as strings, so a payload can carry a job id unchanged."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
