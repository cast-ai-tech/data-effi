"""Worker entrypoint.

Runs four scheduled jobs. Each one opens its own connection, takes its advisory
lock, and records the attempt. Running two workers is safe: the second one finds
the lock held and skips.

You do not have to use this. Every job is also reachable through
`POST /worker/trigger/{job}` with the shared secret, so an existing n8n or cron
setup can drive them instead.
"""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

import psycopg
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from api.settings import get_settings
from pipeline.dbconn import connect as db_connect
from worker.jobs import (
    job_calibrate_maturation,
    job_daily_digest,
    job_refresh_fx,
    job_relink_orphans,
    job_sync_sheets,
    job_sync_tier3,
    run_job,
)

logger = logging.getLogger("masterdata.worker")


def _connect() -> psycopg.Connection:
    """Open a worker connection.

    Declares the service context so row-level security lets these jobs see every
    tenant - which is the point of a worker. See migration 007.
    """
    conn = db_connect(get_settings().database_url, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', false)")
    conn.commit()
    return conn


def run_named_job(job_name: str) -> dict[str, Any]:
    """Run one job by name. Used by the scheduler and by the webhook alike."""
    settings = get_settings()

    bodies = {
        "relink_orphans": job_relink_orphans,
        "refresh_fx": lambda conn: job_refresh_fx(
            conn,
            provider_url=settings.fx_provider_url,
            api_key=settings.fx_provider_api_key,
        ),
        "calibrate_maturation": job_calibrate_maturation,
        "sync_tier3": lambda conn: job_sync_tier3(
            conn,
            pii_salt=settings.pii_hash_salt,
            enabled=settings.tier3_fetch_enabled,
        ),
        "sync_sheets": lambda conn: job_sync_sheets(conn, pii_salt=settings.pii_hash_salt),
        "daily_digest": lambda conn: job_daily_digest(conn, settings=settings),
    }

    body = bodies.get(job_name)
    if body is None:
        raise ValueError(f"Unknown job: {job_name}")

    with _connect() as conn:
        return run_job(conn, job_name, body)


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")

    # Tier-3 fetches twice a day, off-peak for the source platform.
    scheduler.add_job(
        lambda: run_named_job("sync_tier3"),
        CronTrigger(hour="6,18", minute=15),
        id="sync_tier3",
        max_instances=1,
        coalesce=True,
    )
    # A published sheet is the operator's own working file: they edit it during
    # the day and expect the dashboard to catch up without being asked.
    scheduler.add_job(
        lambda: run_named_job("sync_sheets"),
        CronTrigger(minute="5,35"),
        id="sync_sheets",
        max_instances=1,
        coalesce=True,
    )
    # Orphans are cheap to relink and annoying to leave dangling.
    scheduler.add_job(
        lambda: run_named_job("relink_orphans"),
        CronTrigger(minute="*/30"),
        id="relink_orphans",
        max_instances=1,
        coalesce=True,
    )
    # FX before the working day starts in LATAM.
    scheduler.add_job(
        lambda: run_named_job("refresh_fx"),
        CronTrigger(hour=10, minute=5),
        id="refresh_fx",
        max_instances=1,
        coalesce=True,
    )
    # Calibration once a day; it only ever writes a suggestion.
    scheduler.add_job(
        lambda: run_named_job("calibrate_maturation"),
        CronTrigger(hour=7, minute=30),
        id="calibrate_maturation",
        max_instances=1,
        coalesce=True,
    )
    # The 7 am digest, once per country per LOCAL day. Four UTC slots cover
    # every timezone the platform serves; the job itself skips a country whose
    # morning has not started and a country already written today.
    scheduler.add_job(
        lambda: run_named_job("daily_digest"),
        CronTrigger(hour="10,11,12,13", minute=50),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def main() -> int:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not settings.worker_enabled:
        logger.info("WORKER_ENABLED is false; exiting without scheduling anything")
        return 0

    if len(sys.argv) > 1:
        # One-shot mode: `python -m worker.main refresh_fx`
        job_name = sys.argv[1]
        result = run_named_job(job_name)
        logger.info("%s -> %s", job_name, result)
        return 0 if result.get("status") != "failed" else 1

    scheduler = build_scheduler()

    def shutdown(signum, _frame):
        logger.info("signal %s received; stopping scheduler", signum)
        scheduler.shutdown(wait=True)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info(
        "worker started: tier3=%s jobs=%s",
        "enabled" if settings.tier3_fetch_enabled else "disabled",
        [job.id for job in scheduler.get_jobs()],
    )
    # Run the cheap ones once at boot so a fresh install is not empty.
    run_named_job("relink_orphans")
    run_named_job("refresh_fx")

    scheduler.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
