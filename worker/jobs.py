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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from api.events import emit
from pipeline.ingest import IngestEngine
from pipeline.models import BatchKind
from pipeline.store_pg import PostgresStore
from worker.official_rates import fetch_official_rates

logger = logging.getLogger(__name__)

# How far back a tier-3 fetch reaches on each run. Overlap is free: the content
# hash makes a repeated report a no-op.
TIER3_LOOKBACK_DAYS = 14

# Colombia's TRM, published by the Superintendencia Financiera. Free, no key,
# Fallback only. The real list comes from core.country - see _fx_currencies -
# because "el país es dato, no código": adding a country must not require a
# release to make its money convertible. This tuple is what the job uses if that
# query fails, and it is deliberately the set that existed before countries were
# added by migration.
FX_BASE_CURRENCIES = ("COP", "MXN", "PEN", "CLP", "GTQ", "USD")


class JobResult(dict):
    """Plain dict, named for readability at the call site."""


@contextmanager
def advisory_lock(conn: psycopg.Connection, job_name: str):
    """Try to take the lock for this job. Yields False when someone else has it."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (job_name,))
        found = cur.fetchone()
        acquired = bool(found and found[0])
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
        broadcast(conn, "job_run.finished", {"job": job_name, "ok": True})
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


def broadcast(conn: psycopg.Connection, event_type: str, payload: dict[str, Any]) -> int:
    """Append one event per active tenant, so every open dashboard refreshes.

    Runs in the service context the worker already declared. Never raises: an
    event nobody heard is a stale screen until the next poll, not a failed job.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tenant_id FROM core.workspace_country WHERE is_active"
            )
            tenants = [row[0] for row in cur.fetchall()]
        for tenant_id in tenants:
            emit(conn, tenant_id, event_type, payload=payload)
        conn.commit()
        return len(tenants)
    except Exception:
        conn.rollback()
        logger.warning("could not broadcast %s", event_type, exc_info=True)
        return 0


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
        found = cur.fetchone()
        linked = int(found[0]) if found else 0
    conn.commit()
    return {"movements_linked": linked}


# =============================================================================
# Job: daily FX rates
# =============================================================================


def _fx_currencies(conn: psycopg.Connection) -> tuple[str, ...]:
    """Every currency the supported countries actually bill in, plus USD.

    Read from the database rather than hardcoded so that adding a country is one
    row in core.country and nothing else - the rule this project states in
    docs/arquitectura-multipais-conectores.md. USD is always included because it
    is the currency everything converts INTO.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT currency_code FROM core.country WHERE is_supported"
            )
            found = {row[0].strip().upper() for row in cur.fetchall()}
    except Exception:
        logger.warning("no pude leer las monedas de core.country; uso la lista fija",
                       exc_info=True)
        return FX_BASE_CURRENCIES
    return tuple(sorted(found | {"USD"})) or FX_BASE_CURRENCIES


def _provider_endpoint(provider_url: str) -> str:
    """Where to ask this provider for "one dollar in every currency".

    Two conventions, and neither is guessable from the base URL alone:

        .../v6/latest        + /USD        er-api, exchangerate-api
        .../v1/currencies    + /usd.json   @fawazahmed0/currency-api

    Hardcoding either one is what tied this job to a single provider. Swapping
    providers now means changing FX_PROVIDER_URL and nothing else, which matters
    because the one that shipped originally turned out to quote the peso 20% off
    the TRM.
    """
    base = provider_url.rstrip("/")
    if base.endswith(".json") or base.endswith("/USD") or base.endswith("/usd"):
        return base  # ya apunta al recurso exacto
    if "currency-api" in base or base.endswith("/currencies"):
        return f"{base}/usd.json"
    return f"{base}/USD"


def _parse_rate_payload(payload: dict[str, Any]) -> dict[str, float]:
    """USD -> currency, out of whichever envelope the provider happens to use.

    Three shapes in the wild, and the difference is not cosmetic: picking the
    wrong key silently yields an empty dict, which the job then reports as "the
    provider was unreachable" while the request actually succeeded.

        {"rates": {...}}                 open.er-api, exchangerate-api
        {"conversion_rates": {...}}      exchangerate-api v6 (paid)
        {"date": ..., "usd": {...}}      @fawazahmed0/currency-api
    """
    rates = payload.get("rates") or payload.get("conversion_rates")
    if rates:
        return {str(k).upper(): float(v) for k, v in rates.items() if v}

    # currency-api nests under the lowercased base currency and quotes its keys
    # in lowercase too.
    nested = payload.get("usd")
    if isinstance(nested, dict):
        return {str(k).upper(): float(v) for k, v in nested.items() if v}

    return {}


def job_refresh_fx(conn: psycopg.Connection, *, provider_url: str, api_key: str | None = None
                   ) -> dict[str, Any]:
    """Fetch today's rates to USD; fall back to the last known rate on failure.

    A missing rate is never invented. The global view marks `fx_missing` and shows
    local currency instead - a made-up conversion is worse than no conversion.

    OFFICIAL RATES WIN. Where a central bank publishes its own rate - Colombia's
    TRM, Perú's BCRP, Chile's observado, Guatemala's Banguat - that number
    OVERWRITES whatever the general provider said, because it is the rate that
    country's accounting is measured against. See worker/official_rates.py, which
    also lists which currencies still fall back to the provider and why.
    """
    import httpx

    today = date.today()
    fetched: dict[str, float] = {}
    error: str | None = None
    official_sources: dict[str, str] = {}
    currencies = _fx_currencies(conn)

    try:
        url = _provider_endpoint(provider_url)
        params = {"apikey": api_key} if api_key else None
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            rates = _parse_rate_payload(response.json())

            for currency in currencies:
                # The API gives USD -> X. We store X -> USD, which is what the
                # global view multiplies by.
                usd_to_currency = rates.get(currency)
                if usd_to_currency:
                    fetched[currency] = 1.0 / float(usd_to_currency)

            # Las oficiales mandan sobre el proveedor general, moneda por
            # moneda: un banco central publica la tasa contra la que se miden
            # los libros de ese país, y el proveedor solo cotiza el mercado.
            for currency, (per_usd, nombre) in fetch_official_rates(client, currencies).items():
                fetched[currency] = 1.0 / per_usd
                official_sources[currency] = nombre
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("FX provider unreachable (%s); falling back to last known rates", error)

    written = 0
    with conn.cursor() as cur:
        for currency, rate in fetched.items():
            source = official_sources.get(currency, "api")
            cur.execute(
                """
                INSERT INTO core.fx_rate (rate_date, base_currency, quote_currency, rate, source)
                VALUES (%s, %s, 'USD', %s, %s)
                ON CONFLICT (rate_date, base_currency, quote_currency)
                DO UPDATE SET rate = EXCLUDED.rate, source = EXCLUDED.source, fetched_at = now()
                """,
                (today, currency, rate, source),
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

    if written:
        broadcast(conn, "fx.refreshed", {})

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
            delivered = (cur.fetchone() or {}).get("delivered", 0)
            if delivered < MIN_DELIVERIES_FOR_CALIBRATION:
                continue

            cur.execute(
                "SELECT core.measure_maturation_p90(%s, %s) AS p90",
                (workspace["tenant_id"], workspace["country_code"]),
            )
            p90 = (cur.fetchone() or {}).get("p90")
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
    active AND carry a consent timestamp AND are wired as `session`.

    TWO WAYS TO HOLD A SESSION, ONE WAY TO USE IT (migration 051).

      secret_ref  an env var on the server holds a session somebody pasted.
                  Safest, unbeatable against a database dump, and it needs an
                  administrator every time the session dies.

      vault       the merchant's own username and password, encrypted in
                  core.connection_credential. The connector logs in and renews
                  the session itself, which is what lets a merchant connect
                  their own account without anyone touching the server.

    `secret_ref` WINS when both are present. An operator who deliberately wired
    an env var is making a stronger security choice than the default, and a
    credential added later must not silently downgrade it.
    """
    if not enabled:
        return {"skipped": True, "reason": "TIER3_FETCH_ENABLED is false"}

    from connectors.effi.session_fetcher import (
        ConsentError,
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
                   c.consent_granted_at, c.name, c.credential_status, co.currency_code
            FROM core.connection c
            JOIN core.platform p ON p.code = c.platform_code
            JOIN core.country co ON co.code = c.country_code
            WHERE p.tier = 3 AND c.status = 'active' AND c.consent_granted_at IS NOT NULL
              -- Migration 042: an Effi connection fed by uploaded files has no
              -- session to replay. Only the ones the operator wired as one.
              AND c.source_mode = 'session'
              -- Migration 051: THE MOST IMPORTANT LINE IN THIS QUERY.
              --
              -- `invalid` means the platform already rejected this password.
              -- `locked` means it already locked the account. Trying either one
              -- again - even twelve hours later, even politely - is a retry loop
              -- against a merchant's real account, and the merchant is the one
              -- who gets locked out of their own Effi over it. Both states are
              -- terminal until a human re-enters the credential, which sets the
              -- status back to 'none' and lets this query see the row again.
              AND c.credential_status NOT IN ('invalid', 'locked')
            """
        )
        connections = cur.fetchall()

    for connection_row in connections:
        entry: dict[str, Any] = {
            "connection_id": connection_row["id"],
            "name": connection_row["name"],
        }
        try:
            fetcher = _build_tier3_fetcher(conn, connection_row)
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


def _build_tier3_fetcher(conn: psycopg.Connection, row: dict[str, Any]) -> Any:
    """Get a fetcher for one tier-3 connection, from an env var or from the vault.

    Raises FetchError with a message aimed at whoever has to fix it - which is a
    different person in each case. A missing env var is for the administrator; a
    missing credential is for the merchant.
    """
    from connectors.effi.auth import (
        AccountLocked,
        EffiAuthenticator,
        InvalidCredentials,
        LoginContractUnverified,
        LoginUnavailable,
    )
    from connectors.effi.session_fetcher import EffiSessionFetcher, FetchError, SessionExpiredError

    if row.get("secret_ref"):
        return EffiSessionFetcher.from_env(
            secret_ref=row["secret_ref"],
            consent_granted_at=row["consent_granted_at"],
        )

    from api import credentials
    from pipeline.vault import CredentialUnreadable, VaultKeyMissing

    stored = credentials.load_session(
        conn, connection_id=row["id"], tenant_id=row["tenant_id"]
    )
    try:
        with credentials.use_credential(
            conn, connection_id=row["id"], tenant_id=row["tenant_id"]
        ) as credential:
            session, did_login = EffiAuthenticator().ensure_session(
                credential,
                existing_token=stored.token,
                existing_expires_at=stored.expires_at,
            )
    except LookupError as exc:
        raise FetchError(str(exc)) from None
    except (CredentialUnreadable, VaultKeyMissing) as exc:
        raise FetchError(str(exc)) from None
    except InvalidCredentials as exc:
        # A wrong password is terminal. Writing `invalid` here is what stops the
        # next scheduled pass from trying the same password again and walking the
        # merchant's account into a lockout, one sync at a time.
        _mark_credential_status(
            conn, row["id"], row["tenant_id"], "invalid", str(exc),
            connection_name=row.get("name", ""), country_code=row.get("country_code"),
        )
        raise SessionExpiredError(str(exc)) from None
    except AccountLocked as exc:
        _mark_credential_status(
            conn, row["id"], row["tenant_id"], "locked", str(exc),
            connection_name=row.get("name", ""), country_code=row.get("country_code"),
        )
        raise SessionExpiredError(str(exc)) from None
    except LoginContractUnverified as exc:
        raise FetchError(str(exc)) from None
    except LoginUnavailable as exc:
        raise FetchError(str(exc)) from None

    if did_login:
        credentials.save_session(
            conn,
            connection_id=row["id"],
            tenant_id=row["tenant_id"],
            token=session.token,
            expires_at=session.expires_at,
        )
        conn.commit()

    return EffiSessionFetcher.from_session(
        session, consent_granted_at=row["consent_granted_at"]
    )


def _mark_credential_status(
    conn: psycopg.Connection, connection_id: UUID, tenant_id: UUID,
    credential_status: str, message: str, connection_name: str = "",
    country_code: str | None = None,
) -> None:
    """Record a terminal credential outcome AND tell the merchant about it.

    WHY THE NOTIFICATION IS NOT OPTIONAL HERE.

    The overwhelmingly common way a credential goes bad is the merchant changing
    their own password in Effi - for a perfectly good reason, having completely
    forgotten that anything else was using it. From their side nothing happens:
    no error, no email, no broken screen. The dashboard just quietly stops
    getting new guides, and it looks EXACTLY like a slow week.

    That is the worst failure this system can have, because the number on screen
    stays plausible. A merchant would go on making decisions from a dashboard
    that stopped updating a fortnight ago. So a dead credential has to announce
    itself, in the same place every other urgent finding shows up.
    """
    from ai.alerts import persist_findings
    from api import credentials

    credentials.record_login_failure(
        conn,
        connection_id=connection_id,
        tenant_id=tenant_id,
        credential_status=credential_status,
        message=message,
    )

    label = connection_name or "tu plataforma"
    if credential_status == "locked":
        title = f"{label}: la cuenta está bloqueada"
        finding = (
            "La plataforma bloqueó la cuenta que Master Data usa para descargar tus "
            "reportes. Desde este momento no entra ningún dato nuevo y el tablero "
            "se quedó con lo último que alcanzó a cargar."
        )
        action = (
            "Entra a la plataforma y desbloquea esa cuenta. Master Data no lo va a "
            "reintentar solo: insistir contra una cuenta bloqueada la mantendría "
            "bloqueada."
        )
    else:
        title = f"{label}: la contraseña dejó de servir"
        finding = (
            "La plataforma rechazó el usuario o la contraseña que Master Data tiene "
            "guardados. Suele pasar cuando alguien cambia la contraseña allá. Desde "
            "este momento no entra ningún dato nuevo, así que el tablero puede "
            "verse normal y estar desactualizado."
        )
        action = (
            "Ve a Configuración → Conexiones → Gestionar y vuelve a escribir el "
            "usuario y la contraseña. Nada más se reanuda solo."
        )

    try:
        persist_findings(
            conn,
            tenant_id,
            country_code,
            [{
                "code": "connection_credential_failed",
                "severity": "critical",
                "title": title,
                "finding": finding,
                "action": action,
                "deep_link": "/connections",
            }],
        )
    except Exception:
        # A notification that cannot be written must never swallow the status
        # write above - that one is what stops the retry loop, and it matters
        # more than telling anybody about it.
        logger.exception("no se pudo notificar la credencial caída")

    conn.commit()


def _mark_connection_error(conn: psycopg.Connection, connection_id: UUID, message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.connection SET status = 'error', last_error = %s WHERE id = %s",
            (message[:1000], connection_id),
        )
    conn.commit()


# =============================================================================
# Job: Google Sheets sync
# =============================================================================

SHEETS_PLATFORM_CODE = "google_sheets"


def job_sync_sheets(conn: psycopg.Connection, *, pii_salt: str) -> dict[str, Any]:
    """Re-read every connected Google Sheet published to the web.

    A published sheet is a public CSV URL, so there is no consent to check and
    no session to expire - but the rest is identical to the tier-3 sync: fetch
    raw bytes, hand them to the same IngestEngine an upload uses, and mark the
    connection with a readable error when it stops working. Re-reading the same
    sheet twice is free: the content hash makes the second pass a no-op.
    """
    from connectors.sheets.published_csv import SheetFetchError

    results: list[dict[str, Any]] = []

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.id, c.tenant_id, c.country_code, c.platform_code, c.name,
                   c.secret_ref, c.source_url, c.default_kind
            FROM core.connection c
            WHERE c.platform_code = %s AND c.status = 'active'
            ORDER BY c.name
            """,
            (SHEETS_PLATFORM_CODE,),
        )
        connections = cur.fetchall()

    for connection_row in connections:
        entry: dict[str, Any] = {
            "connection_id": connection_row["id"],
            "name": connection_row["name"],
        }
        try:
            entry.update(_sync_one_sheet(conn, connection_row, pii_salt=pii_salt))
            entry["status"] = "ok"
        except (SheetFetchError, ValueError) as exc:
            # ValueError covers InvalidSheetUrlError and the "no sabemos qué tipo
            # de datos son" case. Both need a person, so the connection says so.
            conn.rollback()
            _mark_connection_error(conn, connection_row["id"], str(exc))
            entry["status"] = "error"
            entry["error"] = str(exc)

        results.append(entry)

    return {"connections": results, "count": len(results)}


def _sync_one_sheet(
    conn: psycopg.Connection, connection_row: dict[str, Any], *, pii_salt: str
) -> dict[str, Any]:
    from connectors.sheets.published_csv import PublishedSheetFetcher

    raw_kind = connection_row["default_kind"]
    if not raw_kind:
        raise ValueError(
            "Esta hoja no dice qué tipo de datos trae. Elige el tipo por defecto "
            "de la conexión (guías, movimientos, pauta o servicio al cliente)."
        )
    kind = BatchKind(raw_kind)

    fetcher = PublishedSheetFetcher.from_connection(
        source_url=connection_row["source_url"],
        secret_ref=connection_row["secret_ref"],
    )
    fetch = fetcher.fetch(kind)

    country_code, currency_code = _country_for_connection(conn, connection_row, fetch)

    engine = IngestEngine(PostgresStore(conn), pii_salt=pii_salt)
    report = engine.ingest(
        payload=fetch.payload,
        source_name=fetch.filename,
        kind=kind,
        tenant_id=connection_row["tenant_id"],
        connection_id=connection_row["id"],
        country_code=country_code,
        platform_code=connection_row["platform_code"],
        default_currency=currency_code,
    )

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.connection SET last_sync_at = now(), last_error = NULL WHERE id = %s",
            (connection_row["id"],),
        )
    conn.commit()

    return {
        "kind": kind.value,
        "country_code": country_code,
        "already_loaded": report.already_loaded,
        "inserted": report.rows_inserted,
        "updated": report.rows_updated,
        "failed": report.rows_failed,
    }


def _country_for_connection(
    conn: psycopg.Connection, connection_row: dict[str, Any], fetch: Any
) -> tuple[str, str]:
    """Which country this sheet is about, and in what currency.

    Google Sheets is a GLOBAL platform (migration 012): the connection carries no
    country because the sheet itself is supposed to say. Same order the upload
    queue uses - the connection, then the file, then the workspace when it runs
    a single country - and the same refusal to guess when none of those answer.
    """
    from pipeline.profiles import detect_country, detect_profile
    from pipeline.readers import read_tabular

    country_code = connection_row["country_code"]

    if country_code is None:
        try:
            headers, rows = read_tabular(fetch.payload, fetch.filename)
            profile = detect_profile(headers, fetch.kind)
        except Exception:
            profile = None
        if profile is not None:
            country_code = detect_country(headers, rows, profile)[0]

    if country_code is None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT country_code FROM core.workspace_country "
                "WHERE tenant_id = %s AND is_active ORDER BY country_code",
                (connection_row["tenant_id"],),
            )
            active = cur.fetchall()
        if len(active) != 1:
            raise ValueError(
                "No pudimos determinar el país de esta hoja: no trae columna de país "
                "y tu workspace tiene varios activos. Agrega la columna de país a la "
                "hoja."
            )
        country_code = active[0]["country_code"]

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT currency_code FROM core.country WHERE code = %s", (country_code,))
        country = cur.fetchone()
    if country is None:
        raise ValueError(f"El país '{country_code}' no está soportado por Master Data.")

    return country_code, country["currency_code"]


# =============================================================================
# Job: daily digest
# =============================================================================

# Local hour after which a country's digest may be written. The cron fires at
# :50 of 10-13 UTC, which is 05:50-08:50 in Bogotá and 04:50-07:50 in Mexico
# City; each run writes the digest for the countries already past seven and
# leaves the rest for the next one. The fingerprint makes a second pass a no-op.
DIGEST_LOCAL_HOUR = 7

# The event feed only ever moves forward; a week is more than any cursor
# needs. Notifications are the operator's history: a quarter.
EVENT_RETENTION_DAYS = 7
NOTIFICATION_RETENTION_DAYS = 90


def job_daily_digest(
    conn: psycopg.Connection, *, settings: Any, now: datetime | None = None
) -> dict[str, Any]:
    """One digest per country per local day, plus the retention sweep.

    Per (tenant, country): detect with SQL, collect the signal view, ask the
    model for the brief IF it is on and inside budget, then write the warnings
    and criticals as urgent notifications (the fingerprint keeps the ones the
    last load already sent from repeating) and the digest itself.

    Idempotent: the digest fingerprint is `digest|CC|date`, and everything
    else dedups on its own fingerprint. Never raises for one tenant's failure -
    it is logged, counted and the loop goes on.
    """
    from ai.alerts import persist_digest, persist_findings, persist_report_ready
    from ai.client import AiUnavailable
    from ai.features import collect_alerts, generate_brief
    from ai.memory import ensure_thresholds
    from ai.recommendations import detect

    swept = _sweep_retention(conn)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT wc.tenant_id, wc.country_code, c.timezone
            FROM core.workspace_country wc
            JOIN core.country c ON c.code = wc.country_code
            WHERE wc.is_active
            ORDER BY wc.tenant_id, wc.country_code
            """
        )
        workspaces = cur.fetchall()

    moment = now or datetime.now(UTC)
    written: list[dict[str, Any]] = []
    skipped_early = 0
    already = 0
    errors = 0

    for workspace in workspaces:
        tenant_id = workspace["tenant_id"]
        country_code = workspace["country_code"]
        local = moment.astimezone(_zone(workspace["timezone"]))
        if local.hour < DIGEST_LOCAL_HOUR:
            skipped_early += 1
            continue

        # The mart views filter by core.current_tenant_id(); the service
        # context lets the writes through RLS but does not pick a tenant.
        _scope_session(conn, tenant_id)
        try:
            ensure_thresholds(conn, tenant_id, country_code)
            found = detect(conn, country_code)
            alerts = collect_alerts(conn, country_code)

            brief: str | None = None
            try:
                brief = generate_brief(conn, settings, tenant_id, country_code).get("summary")
            except AiUnavailable as exc:
                logger.info("digest brief degraded for %s: %s", country_code, exc.reason)

            urgent = [item for item in found if item["severity"] in ("warning", "critical")]
            notified = persist_findings(conn, tenant_id, country_code, urgent, kind="urgent")
            digest_id = persist_digest(
                conn, tenant_id, country_code,
                brief=brief, recommendations=found, alerts=alerts,
                local_date=local.date(),
            )
            # The printable "informe diario consolidado" (migration 040), as a
            # link with the range already set. Same fingerprint rule: once per
            # country per local day.
            persist_report_ready(conn, tenant_id, country_code, local_date=local.date())
            conn.commit()
        except Exception:
            conn.rollback()
            errors += 1
            logger.warning(
                "daily digest failed for tenant %s (%s)", tenant_id, country_code,
                exc_info=True,
            )
            continue
        finally:
            _scope_session(conn, None)

        if digest_id is None:
            already += 1
            continue
        written.append(
            {
                "tenant_id": tenant_id,
                "country_code": country_code,
                "notification_id": digest_id,
                "findings": len(found),
                "notified": len(notified),
                "brief": brief is not None,
            }
        )

    return {
        "digests": written,
        "count": len(written),
        "already_written": already,
        "before_local_hour": skipped_early,
        "errors": errors,
        **swept,
    }


def _sweep_retention(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM raw.event WHERE created_at < now() - make_interval(days => %s)",
            (EVENT_RETENTION_DAYS,),
        )
        events_deleted = cur.rowcount
        cur.execute(
            "DELETE FROM raw.notification "
            "WHERE created_at < now() - make_interval(days => %s)",
            (NOTIFICATION_RETENTION_DAYS,),
        )
        notifications_deleted = cur.rowcount
    conn.commit()
    return {"events_deleted": events_deleted, "notifications_deleted": notifications_deleted}


def _zone(name: str | None) -> ZoneInfo:
    """An unknown timezone name falls back to Bogotá rather than to a crash."""
    try:
        return ZoneInfo(name or "America/Bogota")
    except ZoneInfoNotFoundError:
        logger.warning("unknown timezone %r; using America/Bogota", name)
        return ZoneInfo("America/Bogota")


def _scope_session(conn: psycopg.Connection, tenant_id: UUID | None) -> None:
    """Session-level, not transaction-level: the body commits several times."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config('norte.tenant_id', %s, false)",
            (str(tenant_id) if tenant_id else "",),
        )
    conn.commit()


JOB_NAMES = (
    "sync_tier3",
    "sync_sheets",
    "relink_orphans",
    "refresh_fx",
    "calibrate_maturation",
    "daily_digest",
)
