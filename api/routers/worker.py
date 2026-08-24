"""Webhook to trigger worker jobs from outside.

Protected by a shared secret, not by a user token: the caller is n8n or cron, not
a person. The comparison is constant-time so the secret cannot be guessed by
timing the response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query

from api.deps import SettingsDep
from api.errors import ApiError, NotFound, Unauthorized
from api.security import constant_time_equals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker", tags=["worker"])

ALLOWED_JOBS = (
    "sync_tier3",
    "sync_sheets",
    "relink_orphans",
    "refresh_fx",
    "calibrate_maturation",
    "daily_digest",
)


@router.post(
    "/trigger/{job}",
    summary="Ejecutar un job del worker (requiere secreto compartido)",
)
async def trigger(
    job: str,
    settings: SettingsDep,
    x_worker_secret: Annotated[str | None, Header(alias="X-Worker-Secret")] = None,
) -> dict[str, Any]:
    if not x_worker_secret or not constant_time_equals(
        x_worker_secret, settings.worker_trigger_secret
    ):
        # Same response whether the header is missing or wrong.
        raise Unauthorized("Secreto de worker inválido")

    if job not in ALLOWED_JOBS:
        raise NotFound(f"Job desconocido: '{job}'. Disponibles: {', '.join(ALLOWED_JOBS)}")

    from worker.main import run_named_job

    try:
        result = await asyncio.to_thread(run_named_job, job)
    except Exception as exc:
        logger.exception("triggered job %s failed", job)
        raise ApiError(
            "job_failed", f"El job '{job}' falló: {type(exc).__name__}"
        ) from exc

    return {"job": job, **result}


@router.get("/runs", summary="Últimas ejecuciones de jobs")
def recent_runs(
    settings: SettingsDep,
    x_worker_secret: Annotated[str | None, Header(alias="X-Worker-Secret")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    if not x_worker_secret or not constant_time_equals(
        x_worker_secret, settings.worker_trigger_secret
    ):
        raise Unauthorized("Secreto de worker inválido")

    from api.db import connection, fetch_all

    with connection(service=True) as conn:
        return fetch_all(
            conn,
            """
            SELECT job_name, status, started_at, finished_at, result, error
            FROM raw.job_run ORDER BY started_at DESC LIMIT %s
            """,
            (min(limit, 100),),
        )
