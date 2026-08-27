"""Database access: one pool, and a rule about tenants.

Every request that touches tenant data runs inside a transaction that has
`norte.tenant_id` set. That single SET is what makes the mart views return this
tenant's rows and nothing else - and what makes a forgotten WHERE clause return
zero rows instead of someone else's data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_readonly_pool: ConnectionPool | None = None
_ingest_pool: ConnectionPool | None = None


def _host_of(dsn: str) -> str:
    """El host de una cadena de conexión, sin usuario ni contraseña.

    Para poder nombrar en un error QUÉ no respondió sin filtrar credenciales a
    los logs.
    """
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(dsn)
        return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)
    except Exception:      # pragma: no cover - un DSN ilegible no debe tapar el error real
        return "(host ilegible)"


def init_pools(settings: Settings) -> None:
    """Open the pools, replacing any that are already open.

    Reassigning the globals without closing what they pointed at leaks every
    connection the old pool had open: `min_size` of them are established
    eagerly and never handed back, so they sit in the server holding whatever
    locks their last statement took. A later TRUNCATE then deadlocks against
    connections belonging to a pool nobody can reach any more.

    That happens whenever two app instances overlap - the test suite creates
    one TestClient per module - because the second startup overwrites the
    global and the first shutdown then closes the SECOND pool. Closing first
    makes a repeated startup idempotent instead.
    """
    global _pool, _readonly_pool, _ingest_pool

    close_pools()

    # prepare_threshold=None disables psycopg's automatic prepared statements.
    #
    # psycopg promotes a query to a named prepared statement once it has been
    # executed five times. Against a plain PostgreSQL that is free speed. Behind
    # a transaction-mode pooler - Supabase's Supavisor on port 6543, and PgBouncer
    # generally - it is a crash: the pooler hands each transaction whichever
    # backend is free, so the sixth execution meets a backend that already has
    # `_pg3_0` from a different client and raises
    #
    #     DuplicatePreparedStatement: prepared statement "_pg3_0" already exists
    #
    # Verified against this project's own Supabase pool: twelve executions of one
    # query fail on the sixth. The failure is a slow one - the API starts, serves
    # the first few requests, and only then begins throwing - which is why it has
    # to be off by construction rather than discovered in production.
    # El pooler de Supabase (Supavisor, puerto 6543) cierra por su cuenta las
    # conexiones de servidor que quedan inactivas. Sin las dos líneas de abajo el
    # pool acaba prestando una conexión que el pooler ya mató, y la primera query
    # revienta con «the connection is lost» / «SSL error: unexpected eof while
    # reading» - justo el fallo que tumbaba el login. `check` valida cada
    # conexión antes de entregarla (descarta la muerta y abre una fresca, de
    # forma transparente); `max_lifetime` la recicla antes de que el pooler
    # llegue a cerrarla por inactividad.
    _pool = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"autocommit": False, "prepare_threshold": None},
        check=ConnectionPool.check_connection,
        max_lifetime=300,
        open=True,
    )
    # `open=True` NO espera a que la conexión se establezca: el pool se declara
    # listo aunque la base sea inalcanzable, y el fallo aparece minutos después,
    # en la primera consulta, sin decir que era la base. Eso costó un despliegue
    # entero: el log decía "database pool ready" y el arranque se quedaba colgado
    # sin abrir puerto, sin un solo error.
    #
    # Esperar aquí convierte ese misterio en una frase. `wait` intenta de verdad
    # y, si no puede, se dice qué host no respondió - que es el dato que hacía
    # falta (contra Supabase, la causa habitual es apuntar a la conexión DIRECTA
    # `db.<ref>.supabase.co`, que solo tiene IPv6, en vez de al pooler).
    try:
        _pool.wait(timeout=settings.db_connect_timeout)
    except Exception as exc:
        host = _host_of(settings.database_url)
        close_pools()
        raise RuntimeError(
            f"No se pudo conectar a la base de datos en {host}: {type(exc).__name__}. "
            "Revisa DATABASE_URL. Con Supabase tiene que apuntar al POOLER "
            "(aws-0-<region>.pooler.supabase.com), no a la conexión directa "
            "db.<ref>.supabase.co, que solo responde por IPv6."
        ) from exc
    logger.info("database pool ready (min=%s max=%s)", settings.db_pool_min, settings.db_pool_max)

    if settings.database_url_readonly:
        _readonly_pool = ConnectionPool(
            settings.database_url_readonly,
            min_size=0,
            max_size=4,
            kwargs={"autocommit": True, "prepare_threshold": None},
            check=ConnectionPool.check_connection,
            max_lifetime=300,
            open=True,
        )
        logger.info("read-only pool ready (NL->SQL)")

    # Pool para la ingesta (service=True). Apunta al SESSION pooler (5432), donde
    # una carga - UNA transacción larga que sube su statement_timeout a 30min -
    # sí completa. El transaction pooler (6543) del `_pool` de arriba ni preserva
    # ese SET ni tolera la transacción larga, y mata la carga de un archivo
    # grande. Concurrencia baja a propósito (ingest_max_concurrency), para no
    # agotar el cupo del session pooler. Si no está configurada, la ingesta cae
    # al `_pool` y los archivos grandes vuelven a fallar (ver connection()).
    if settings.database_url_ingest:
        _ingest_pool = ConnectionPool(
            settings.database_url_ingest,
            min_size=0,
            max_size=max(settings.ingest_max_concurrency + 1, 2),
            kwargs={"autocommit": False, "prepare_threshold": None},
            check=ConnectionPool.check_connection,
            max_lifetime=300,
            open=True,
        )
        logger.info("ingest pool ready (session pooler)")


def close_pools() -> None:
    global _pool, _readonly_pool, _ingest_pool
    for pool in (_pool, _readonly_pool, _ingest_pool):
        if pool is not None:
            pool.close()
    _pool = None
    _readonly_pool = None
    _ingest_pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:      # pragma: no cover - startup guarantees this
        raise RuntimeError("database pool is not initialised")
    return _pool


def get_readonly_pool() -> ConnectionPool | None:
    return _readonly_pool


@contextmanager
def connection(
    tenant_id: UUID | None = None, *, service: bool = False
) -> Iterator[psycopg.Connection]:
    """Check out a connection, scoped to a tenant, committing on success.

    `SET LOCAL` means the setting dies with the transaction, so a pooled
    connection can never carry one tenant's scope into another's request.

    `service=True` is the worker/ingestion context: those processes legitimately
    touch every tenant, so RLS lets them through (see migration 007). Never set
    it on a path that serves a user request. Service work also goes to the ingest
    pool (session pooler) when one is configured: a file load is one long
    transaction that the transaction pooler would cut short. It falls back to the
    main pool when `_ingest_pool` is unset.
    """
    pool = _ingest_pool if (service and _ingest_pool is not None) else get_pool()
    with pool.connection() as conn:
        try:
            with conn.cursor() as cur:
                if tenant_id is not None:
                    cur.execute(
                        "SELECT set_config('norte.tenant_id', %s, true)", (str(tenant_id),)
                    )
                if service:
                    cur.execute("SELECT set_config('norte.service', 'on', true)")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def fetch_all(
    conn: psycopg.Connection, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def fetch_one(
    conn: psycopg.Connection, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None
) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def fetch_required(
    conn: psycopg.Connection, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None
) -> dict[str, Any]:
    """`fetch_one` for statements that MUST yield a row: INSERT ... RETURNING,
    or a SELECT on a row this same transaction just wrote.

    An empty result there is not "not found" - it is a broken invariant, and
    raising here names it instead of letting a `None[...]` TypeError surface
    three frames later as a generic 500.
    """
    row = fetch_one(conn, query, params)
    if row is None:
        raise RuntimeError("expected exactly one row, the statement returned none")
    return row


def execute(
    conn: psycopg.Connection, query: str, params: dict[str, Any] | tuple[Any, ...] | None = None
) -> int:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def check_rate_limit(
    conn: psycopg.Connection, *, scope: str, subject: str, limit: int, window_seconds: int = 60
) -> bool:
    """Returns True when the caller may proceed."""
    row = fetch_one(
        conn,
        "SELECT raw.register_rate_limit_hit(%s, %s, %s, %s) AS allowed",
        (scope, subject, window_seconds, limit),
    )
    return bool(row and row["allowed"])


def healthcheck() -> dict[str, Any]:
    settings = get_settings()
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        database_ok = True
        detail = None
    except Exception as exc:
        database_ok = False
        detail = type(exc).__name__

    return {
        "status": "ok" if database_ok else "degraded",
        "database": "ok" if database_ok else "unreachable",
        "database_error": detail,
        "environment": settings.environment,
        "ai_enabled": settings.ai_enabled,
    }
