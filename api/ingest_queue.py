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
        with connection() as conn:
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
        with connection() as conn:
            job = fetch_one(
                conn,
                """
                SELECT j.*, c.country_code, c.platform_code, co.currency_code
                FROM raw.upload_job j
                JOIN core.connection c ON c.id = j.connection_id
                JOIN core.country co ON co.code = c.country_code
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
            with connection() as conn:
                store = PostgresStore(conn)
                engine = IngestEngine(store, pii_salt=self._settings.pii_hash_salt)
                report = engine.ingest(
                    payload=payload,
                    source_name=job["filename"],
                    kind=BatchKind(job["kind"]),
                    tenant_id=job["tenant_id"],
                    connection_id=job["connection_id"],
                    country_code=job["country_code"],
                    platform_code=job["platform_code"],
                    default_currency=job["currency_code"],
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

    def _fail(self, job_id: UUID, message: str) -> None:
        with connection() as conn:
            execute(
                conn,
                "UPDATE raw.upload_job SET status = 'failed', error = %s, finished_at = now() "
                "WHERE id = %s",
                (message[:2000], job_id),
            )


def _human_error(exc: Exception) -> str:
    """Turn a parser exception into something a non-technical user can act on."""
    from pipeline.readers import EmptyFileError, UnsupportedFileError

    if isinstance(exc, UnsupportedFileError | EmptyFileError):
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
