"""Scheduled jobs.

Every job here obeys the same three rules:

1. IDEMPOTENT. Running it twice does what running it once did.
2. LOCKED. A PostgreSQL advisory lock means a second worker process skips the job
   instead of duplicating it. No leader election, no Redis, no coordination
   service - the database already knows how to do this.
3. RECORDED. Start, end, outcome and error land in raw.job_run, so "did the sync
   run last night?" is a query, not a guess.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from pipeline.ingest import IngestEngine
from pipeline.models import BatchKind
from pipeline.store_pg import PostgresStore

logger = logging.getLogger(__name__)

# How far back a tier-3 fetch reaches on each run. Overlap is free: the content
# hash makes a repeated report a no-op.
TIER3_LOOKBACK_DAYS = 14

# Currencies Data Effi converts to USD for the global view.
FX_BASE_CURRENCIES = ("COP", "MXN", "PEN", "CLP", "GTQ", "USD")


class JobResult(dict):
    """Plain dict, named for readability at the call site."""


@contextmanager
def advisory_lock(conn: psycopg.Connection, job_name: str):
    """Try to take the lock for this job. Yields False when someone else has it."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (job_name,))
        acquired = bool(cur.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (job_name,))


def run_job(
    conn: psycopg.Connection,
    job_name: str,
    body: Callable[[psycopg.Connection], dict[str, Any]],
    *,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    """Run one job under its lock, recording the attempt either way."""
    with advisory_lock(conn, job_name) as acquired:
        if not acquired:
            logger.info("job %s already running elsewhere; skipping", job_name)
            _record(conn, job_name, tenant_id, "skipped", {"reason": "lock_held"}, None)
            return {"status": "skipped", "reason": "lock_held"}

        started = datetime.now(UTC)
        try:
            result = body(conn)
        except Exception as exc:
            logger.exception("job %s failed", job_name)
            _record(conn, job_name, tenant_id, "failed", {}, f"{type(exc).__name__}: {exc}")
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

        elapsed = (datetime.now(UTC) - started).total_seconds()
        result = {**result, "elapsed_seconds": round(elapsed, 2)}
        _record(conn, job_name, tenant_id, "ok", result, None)
        logger.info("job %s ok in %.2fs: %s", job_name, elapsed, result)
        return {"status": "ok", **result}


def _record(
    conn: psycopg.Connection,
    job_name: str,
    tenant_id: UUID | None,
    status: str,
    result: dict[str, Any],
    error: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.job_run (job_name, tenant_id, status, finished_at, result, error)
            VALUES (%s, %s, %s, now(), %s, %s)
            """,
            (job_name, tenant_id, status, Json(_jsonable(result)), error),
        )
    conn.commit()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, UUID | date | datetime):
        return str(value)
    return value


# =============================================================================
# Job: relink orphan movements
# =============================================================================


def job_relink_orphans(conn: psycopg.Connection) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT core.relink_orphan_movements(NULL)")
        linked = int(cur.fetchone()[0])
    conn.commit()
    return {"movements_linked": linked}


# =============================================================================
# Job: daily FX rates
# =============================================================================


def job_refresh_fx(conn: psycopg.Connection, *, provider_url: str, api_key: str | None = None
                   ) -> dict[str, Any]:
    """Fetch today's rates to USD; fall back to the last known rate on failure.

    A missing rate is never invented. The global view marks `fx_missing` and shows
    local currency instead - a made-up conversion is worse than no conversion.
    """
    import httpx

    today = date.today()
    fetched: dict[str, float] = {}
    error: str | None = None

    try:
        url = f"{provider_url.rstrip('/')}/USD"
        params = {"apikey": api_key} if api_key else None
        with httpx.Client(timeout=20.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()

        # Providers disagree on the envelope; accept the two common shapes.
        rates = payload.get("rates") or payload.get("conversion_rates") or {}
        for currency in FX_BASE_CURRENCIES:
            # The API gives USD -> X. We store X -> USD, which is what the global
            # view multiplies by.
            usd_to_currency = rates.get(currency)
            if usd_to_currency:
                fetched[currency] = 1.0 / float(usd_to_currency)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("FX provider unreachable (%s); falling back to last known rates", error)

    written = 0
    with conn.cursor() as cur:
        for currency, rate in fetched.items():
            cur.execute(
                """
                INSERT INTO core.fx_rate (rate_date, base_currency, quote_currency, rate, source)
                VALUES (%s, %s, 'USD', %s, %s)
                ON CONFLICT (rate_date, base_currency, quote_currency)
                DO UPDATE SET rate = EXCLUDED.rate, fetched_at = now()
                """,
                (today, currency, rate, "api"),
            )
            written += 1

        if not fetched:
            # Carry yesterday's rates forward so the dashboard keeps working.
            cur.execute(
                """
                INSERT INTO core.fx_rate (rate_date, base_currency, quote_currency, rate, source)
                SELECT %s, base_currency, quote_currency, rate, 'carried_forward'
                FROM (
                    SELECT DISTINCT ON (base_currency, quote_currency)
                           base_currency, quote_currency, rate
                    FROM core.fx_rate
                    ORDER BY base_currency, quote_currency, rate_date DESC
                ) latest
                ON CONFLICT DO NOTHING
                """,
                (today,),
            )
            written = cur.rowcount
    conn.commit()

    return {"rates_written": written, "source": "api" if fetched else "carried_forward",
            "error": error}


# =============================================================================
# Job: maturation calibration (proposes, never applies)
# =============================================================================


MIN_DELIVERIES_FOR_CALIBRATION = 30


def job_calibrate_maturation(conn: psycopg.Connection) -> dict[str, Any]:
    """Measure the real p90 of days-to-delivery per tenant+country.

    Writes it to `maturation_days_suggested`. It deliberately does NOT touch
    `maturation_days`: changing how the business measures itself is a decision a
    person makes, not a cron job.
    """
    proposals: list[dict[str, Any]] = []

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT tenant_id, country_code, maturation_days FROM core.workspace_country "
            "WHERE is_active"
        )
        workspaces = cur.fetchall()

        for workspace in workspaces:
            cur.execute(
                """
                SELECT count(*) AS delivered
                FROM core.shipment
                WHERE tenant_id = %s AND country_code = %s AND delivered_at IS NOT NULL
                  AND created_date >= CURRENT_DATE - interval '180 days'
                """,
                (workspace["tenant_id"], workspace["country_code"]),
            )
            delivered = cur.fetchone()["delivered"]
            if delivered < MIN_DELIVERIES_FOR_CALIBRATION:
                continue

            cur.execute(
                "SELECT core.measure_maturation_p90(%s, %s) AS p90",
                (workspace["tenant_id"], workspace["country_code"]),
            )
            p90 = cur.fetchone()["p90"]
            if p90 is None or p90 <= 0:
                continue

            p90 = max(3, min(int(p90), 90))
            cur.execute(
                """
                UPDATE core.workspace_country
                   SET maturation_days_suggested = %s, maturation_suggested_at = now()
                 WHERE tenant_id = %s AND country_code = %s
                """,
                (p90, workspace["tenant_id"], workspace["country_code"]),
            )
            proposals.append(
                {
                    "tenant_id": workspace["tenant_id"],
                    "country_code": workspace["country_code"],
                    "current": workspace["maturation_days"],
                    "suggested": p90,
                    "based_on_deliveries": delivered,
                }
            )
    conn.commit()
    return {"proposals": proposals, "count": len(proposals)}


# =============================================================================
# Job: tier-3 sync
# =============================================================================


def job_sync_tier3(
    conn: psycopg.Connection, *, pii_salt: str, enabled: bool
) -> dict[str, Any]:
    """Fetch reports for consented tier-3 connections and ingest them normally.

    Off by default (TIER3_FETCH_ENABLED). Only touches connections that are
    active AND carry a consent timestamp AND name an env var holding the session.
    """
    if not enabled:
        return {"skipped": True, "reason": "TIER3_FETCH_ENABLED is false"}

    from connectors.effi.session_fetcher import (
        ConsentError,
        EffiSessionFetcher,
        FetchError,
        SessionExpiredError,
    )

    results: list[dict[str, Any]] = []
    date_to = date.today()
    date_from = date_to - timedelta(days=TIER3_LOOKBACK_DAYS)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.id, c.tenant_id, c.country_code, c.platform_code, c.secret_ref,
                   c.consent_granted_at, c.name, co.currency_code
            FROM core.connection c
            JOIN core.platform p ON p.code = c.platform_code
            JOIN core.country co ON co.code = c.country_code
            WHERE p.tier = 3 AND c.status = 'active' AND c.consent_granted_at IS NOT NULL
            """
        )
        connections = cur.fetchall()

    for connection_row in connections:
        entry: dict[str, Any] = {
            "connection_id": connection_row["id"],
            "name": connection_row["name"],
        }
        try:
            fetcher = EffiSessionFetcher.from_env(
                secret_ref=connection_row["secret_ref"],
                consent_granted_at=connection_row["consent_granted_at"],
            )
            store = PostgresStore(conn)
            engine = IngestEngine(store, pii_salt=pii_salt)
            ingested = []

            for kind in (BatchKind.SHIPMENTS, BatchKind.MOVEMENTS):
                fetch = fetcher.fetch_report(kind, date_from=date_from, date_to=date_to)
                report = engine.ingest(
                    payload=fetch.payload,
                    source_name=fetch.filename,
                    kind=kind,
                    tenant_id=connection_row["tenant_id"],
                    connection_id=connection_row["id"],
                    country_code=connection_row["country_code"],
                    platform_code=connection_row["platform_code"],
                    default_currency=connection_row["currency_code"],
                )
                conn.commit()
                ingested.append(
                    {
                        "kind": kind.value,
                        "already_loaded": report.already_loaded,
                        "inserted": report.rows_inserted,
                        "updated": report.rows_updated,
                    }
                )
            entry["ingested"] = ingested
            entry["status"] = "ok"

        except (ConsentError, SessionExpiredError) as exc:
            # These need a human. Mark the connection so the UI can ask.
            conn.rollback()
            _mark_connection_error(conn, connection_row["id"], str(exc))
            entry["status"] = "needs_reauthorization"
            entry["error"] = str(exc)
        except FetchError as exc:
            conn.rollback()
            _mark_connection_error(conn, connection_row["id"], str(exc))
            entry["status"] = "error"
            entry["error"] = str(exc)

        results.append(entry)

    return {"connections": results, "count": len(results)}


def _mark_connection_error(conn: psycopg.Connection, connection_id: UUID, message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.connection SET status = 'error', last_error = %s WHERE id = %s",
            (message[:1000], connection_id),
        )
    conn.commit()


JOB_NAMES = ("sync_tier3", "relink_orphans", "refresh_fx", "calibrate_maturation")
