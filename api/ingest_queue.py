"""Bounded queue for file ingestion.

Two rules, both learned the hard way:

1. BOUNDED CONCURRENCY. A user dropping forty files must not open forty database
   connections. A semaphore caps how many run at once; the rest wait their turn.
2. THE QUEUE IS IN THE DATABASE. `raw.upload_job` is the source of truth, not a
   Python list. If the API restarts mid-upload, the jobs are still there and get
   picked back up instead of vanishing with the process.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from api.db import connection, execute, fetch_all, fetch_one
from api.settings import Settings
from pipeline.ingest import IngestEngine
from pipeline.models import BatchKind
from pipeline.store_pg import PostgresStore

logger = logging.getLogger(__name__)


class CountryUndeterminedError(ValueError):
    """A global connection received a file that does not say which country it is."""


class IngestQueue:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.ingest_max_concurrency)
        self._tasks: set[asyncio.Task] = set()

    def submit(self, job_id: UUID) -> None:
        task = asyncio.create_task(self._run(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover_pending(self) -> int:
        """Re-queue jobs left behind by a restart."""
        with connection(service=True) as conn:
            rows = fetch_all(
                conn,
                "SELECT id FROM raw.upload_job WHERE status IN ('queued', 'processing') "
                "ORDER BY queued_at",
            )
        for row in rows:
            self.submit(row["id"])
        if rows:
            logger.info("recovered %d pending upload jobs after restart", len(rows))
        return len(rows)

    async def drain(self, timeout: float = 30.0) -> None:
        """Let in-flight work finish on shutdown instead of killing it."""
        if not self._tasks:
            return
        await asyncio.wait(self._tasks, timeout=timeout)

    async def _run(self, job_id: UUID) -> None:
        async with self._semaphore:
            try:
                await asyncio.to_thread(self._process, job_id)
            except Exception:
                logger.exception("upload job %s failed", job_id)

    def _process(self, job_id: UUID) -> None:
        """Synchronous body, run in a worker thread (psycopg here is sync)."""
        with connection(service=True) as conn:
            job = fetch_one(
                conn,
                """
                SELECT j.*, c.country_code, c.platform_code
                FROM raw.upload_job j
                JOIN core.connection c ON c.id = j.connection_id
                WHERE j.id = %s
                """,
                (job_id,),
            )
            if job is None:
                logger.warning("upload job %s disappeared before processing", job_id)
                return
            if job["status"] in ("done", "failed", "duplicate"):
                return

            execute(
                conn,
                "UPDATE raw.upload_job SET status = 'processing', started_at = now() WHERE id = %s",
                (job_id,),
            )

        path = Path(job["storage_path"])
        try:
            payload = path.read_bytes()
        except OSError as exc:
            self._fail(job_id, f"No se pudo leer el archivo subido: {exc}")
            return

        try:
            with connection(service=True) as conn:
                country_code, currency_code = self._resolve_country(conn, job, payload)
                store = PostgresStore(conn)
                engine = IngestEngine(store, pii_salt=self._settings.pii_hash_salt)
                report = engine.ingest(
                    payload=payload,
                    source_name=job["filename"],
                    kind=BatchKind(job["kind"]),
                    tenant_id=job["tenant_id"],
                    connection_id=job["connection_id"],
                    country_code=country_code,
                    platform_code=job["platform_code"],
                    default_currency=currency_code,
                    reprocess=bool(job.get("reprocess")),
                )

                status = "duplicate" if report.already_loaded else (
                    "done" if report.rows_failed == 0 else "failed"
                )
                error = report.errors[0].message if report.errors else None
                execute(
                    conn,
                    """
                    UPDATE raw.upload_job SET
                        status = %s, batch_id = %s, content_hash = %s,
                        error = %s, finished_at = now()
                    WHERE id = %s
                    """,
                    (status, report.batch_id, report.content_hash, error, job_id),
                )
        except Exception as exc:
            logger.exception("ingestion failed for job %s", job_id)
            self._fail(job_id, _human_error(exc))
            return
        finally:
            # The uploaded file has served its purpose; raw rows live in the DB.
            path.unlink(missing_ok=True)

        logger.info(
            "job %s finished: %s inserted, %s updated, %s skipped, %s failed",
            job_id, report.rows_inserted, report.rows_updated,
            report.rows_skipped, report.rows_failed,
        )

        if status == "done":
            self._refresh_recommendations(job["tenant_id"], country_code)

    def _resolve_country(
        self, conn, job: dict, payload: bytes
    ) -> tuple[str, str]:
        """Work out which country this load belongs to, and in what currency.

        A country-scoped connection answers this by existing. A GLOBAL one -
        manual upload, a webhook, a published sheet (migration 012) - does not,
        by design: the file itself says where it is from, so making the operator
        pick first would ask for something the system already has.

        Order: the connection, then the file, then the workspace when it runs a
        single country. Anything else is a question only a person can answer,
        so the job fails saying exactly that instead of guessing a country and
        silently filing a week of Ecuadorian guides under Colombia.
        """
        country_code = job["country_code"] or self._country_from_file(job, payload)

        if country_code is None:
            active = fetch_all(
                conn,
                "SELECT country_code FROM core.workspace_country "
                "WHERE tenant_id = %s AND is_active ORDER BY country_code",
                (job["tenant_id"],),
            )
            if len(active) == 1:
                country_code = active[0]["country_code"]
            else:
                raise CountryUndeterminedError(
                    "No pudimos determinar el país de este archivo: no trae una "
                    "columna de país y tu workspace tiene varios activos. Agrega la "
                    "columna de país al reporte o cárgalo desde una conexión de ese país."
                )

        row = fetch_one(
            conn, "SELECT currency_code FROM core.country WHERE code = %s", (country_code,)
        )
        if row is None:
            raise CountryUndeterminedError(
                f"El país '{country_code}' no está soportado por Data Effi."
            )
        return country_code, row["currency_code"]

    @staticmethod
    def _country_from_file(job: dict, payload: bytes) -> str | None:
        """Peek at the file just to read its country column.

        Yes, this parses the file a second time - the engine parses it again to
        actually load it. A few milliseconds is a fair price for not needing a
        country before the file has been opened.
        """
        from pipeline.profiles import detect_country, detect_profile
        from pipeline.readers import read_tabular

        try:
            headers, rows = read_tabular(payload, job["filename"])
            profile = detect_profile(headers, BatchKind(job["kind"]))
        except Exception:
            # Unreadable is the engine's error to report, with its own message.
            return None
        if profile is None:
            return None

        detected, _raw = detect_country(headers, rows, profile)
        return detected

    def _refresh_recommendations(self, tenant_id: UUID, country_code: str) -> None:
        """Re-derive the operation's own normals now that new guides landed.

        DELIBERATELY NO LLM CALL. Ingestion is allowed to depend on the database
        and nothing else: a file upload that fails because a model was
        unreachable is a broken product. The detection is SQL, so the
        recommendations an operator sees straight after an upload already
        reflect the file they just uploaded.

        Runs in a TENANT context, not a service one: the mart views it reads
        filter by `core.current_tenant_id()`, and the memory rows it writes
        belong to this tenant alone.
        """
        from ai.recommendations import refresh_after_batch

        try:
            with connection(tenant_id) as conn:
                refresh_after_batch(conn, tenant_id, country_code)
        except Exception:
            # An ingestion that succeeded stays succeeded. This is a side effect.
            logger.warning(
                "post-ingestion refresh failed for tenant %s", tenant_id, exc_info=True
            )

    def _fail(self, job_id: UUID, message: str) -> None:
        with connection(service=True) as conn:
            execute(
                conn,
                "UPDATE raw.upload_job SET status = 'failed', error = %s, finished_at = now() "
                "WHERE id = %s",
                (message[:2000], job_id),
            )


def _human_error(exc: Exception) -> str:
    """Turn a parser exception into something a non-technical user can act on."""
    from pipeline.readers import EmptyFileError, UnsupportedFileError

    if isinstance(exc, UnsupportedFileError | EmptyFileError | CountryUndeterminedError):
        return str(exc)
    return f"No se pudo procesar el archivo ({type(exc).__name__}). Revisa el formato."


_queue: IngestQueue | None = None


def init_queue(settings: Settings) -> IngestQueue:
    global _queue
    _queue = IngestQueue(settings)
    return _queue


def get_queue() -> IngestQueue:
    if _queue is None:      # pragma: no cover - startup guarantees this
        raise RuntimeError("ingest queue is not initialised")
    return _queue
