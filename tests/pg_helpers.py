"""Helpers for the PostgreSQL-backed tests.

Creates a throwaway `norte_test` database, applies every migration in order, and
seeds the minimum a load needs: one tenant, one active country, one connection.

TWO ROLES, ON PURPOSE. Migrations and database creation run as the superuser;
everything the tests then do runs as `norte_app`, the same non-superuser,
non-owner role the API uses. That is the only way the row-level security tests
mean anything: a superuser bypasses every policy silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
# Overridable so two suites can run at once without dropping each other's
# database mid-run - which looks exactly like "PostgreSQL unreachable".
# One database PER PROCESS, not one shared by all of them.
#
# `recreate_test_database()` issues DROP DATABASE. When two pytest runs overlap -
# two developers, two agents, a CI job beside a local run - they take turns
# dropping the database out from under each other, and the failures that surface
# are deadlocks, "database norte_test does not exist", and duplicate-key errors
# on pg_database. All of them look like product bugs and none of them are.
#
# Naming the database after the process removes the shared resource instead of
# trying to schedule access to it. TEST_DB_NAME still overrides, so CI can pin a
# name when it wants one.
TEST_DB_NAME = os.environ.get("TEST_DB_NAME") or f"norte_test_{os.getpid()}"


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Minimal .env reader. Values already in the environment win."""
    env_path = path or (REPO_ROOT / ".env")
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split("#")[0].strip()
    return values


def _env() -> dict[str, str]:
    return {**load_dotenv(), **os.environ}


def _swap_database(dsn: str, database: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", rf"/{database}\1", dsn)


def _admin_url() -> str | None:
    """Superuser DSN. Creating databases and running DDL needs it."""
    env = _env()
    return env.get("POSTGRES_ADMIN_URL") or env.get("DATABASE_URL")


def _app_url() -> str | None:
    """Application DSN - the role that is subject to row-level security."""
    env = _env()
    return env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")


def admin_dsn() -> str | None:
    """Superuser DSN pointing at the maintenance database."""
    dsn = _admin_url()
    return _swap_database(dsn, "postgres") if dsn else None


def admin_test_dsn() -> str | None:
    """Superuser DSN pointing at the test database, for migrations."""
    dsn = _admin_url()
    return _swap_database(dsn, TEST_DB_NAME) if dsn else None


def resolve_test_dsn() -> str | None:
    """Application-role DSN pointing at the test database."""
    dsn = _app_url()
    return _swap_database(dsn, TEST_DB_NAME) if dsn else None


def _readonly_url() -> str | None:
    """The role behind the NL->SQL copilot: mart aggregates and nothing else.

    An EMPTY environment variable is treated as unset here, unlike everywhere
    else. The API test fixtures blank `DATABASE_URL_READONLY` on purpose, to
    prove the API never needs that role - so honouring the blank would silently
    skip every test that checks what the copilot can still read, which is the
    one thing these helpers exist to answer.
    """
    dotenv = load_dotenv()
    env = _env()
    for key in ("TEST_DATABASE_URL_READONLY", "DATABASE_URL_READONLY"):
        value = env.get(key) or dotenv.get(key)
        if value:
            return value
    return None


def resolve_readonly_test_dsn() -> str | None:
    """Read-only-role DSN pointing at the test database.

    Needed by any test that has to prove what the copilot can actually SELECT.
    Asking `has_table_privilege` is not the same question: a grant on a view can
    be perfectly in place while the view's body fails on something underneath.
    """
    dsn = _readonly_url()
    return _swap_database(dsn, TEST_DB_NAME) if dsn else None


# Arbitrary but fixed: every suite must pick the SAME number for the lock to
# mean anything.
_CLUSTER_SETUP_LOCK = 0x4E4F525445


def recreate_test_database() -> str:
    """Drop and recreate the test database, apply migrations, return the app DSN."""
    admin = admin_dsn()
    admin_target = admin_test_dsn()
    app_target = resolve_test_dsn()
    if not admin or not admin_target or not app_target:
        raise RuntimeError("No DATABASE_URL available for the PostgreSQL tests")

    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn, conn.cursor() as cur:
        # Roles and databases live in catalogs shared by the whole cluster, not
        # in the per-process database. Two suites reaching CREATE ROLE at the
        # same moment collide on pg_authid with "tuple concurrently updated" -
        # 106 errors in one run, every one of them looking like a product bug.
        # The lock is held only for the catalog work; the tests themselves stay
        # fully parallel because each has its own database.
        cur.execute("SELECT pg_advisory_lock(%s)", (_CLUSTER_SETUP_LOCK,))
        try:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (TEST_DB_NAME,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
            cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

            apply_migrations(admin_target)
            _prepare_app_role(admin_target, app_target)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_CLUSTER_SETUP_LOCK,))

    return app_target


def drop_test_database() -> None:
    """Remove this process's database at the end of the session.

    Per-process names solve the collisions but would otherwise leave one dead
    database behind per run. Failures here are swallowed on purpose: a leftover
    database is untidy, a crash during teardown hides the real test results.
    """
    admin = admin_dsn()
    if not admin:
        return
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (TEST_DB_NAME,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
    except Exception as exc:
        # Swallowed on purpose: a leftover database is untidy, but a crash in
        # teardown would hide the real test results.
        print(f"aviso: no se pudo borrar {TEST_DB_NAME}: {exc}")


def sweep_abandoned_test_databases() -> int:
    """Delete per-process databases whose process is gone.

    A run killed with Ctrl-C never reaches teardown. Without this the server
    slowly fills with norte_test_* from crashed sessions.
    """
    admin = admin_dsn()
    if not admin:
        return 0
    dropped = 0
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                r"SELECT datname FROM pg_database WHERE datname LIKE 'norte\_test\_%%'"
            )
            names = [row[0] for row in cur.fetchall()]
            for name in names:
                if name == TEST_DB_NAME:
                    continue
                suffix = name.rsplit("_", 1)[-1]
                if not suffix.isdigit() or _process_alive(int(suffix)):
                    continue
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
                dropped += 1
    except Exception:
        return dropped

    return dropped


def _process_alive(pid: int) -> bool:
    """True when a process with that id still exists.

    Errs on the side of "alive": a database we are unsure about is left alone
    rather than dropped under a running suite.
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def apply_migrations(dsn: str) -> None:
    """Apply every migration to a fresh database, recording them in the ledger.

    The database is new here, so everything is pending - but the ledger is still
    written so this database behaves exactly like a production one on the next
    run of scripts.migrate.
    """
    import hashlib

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migration (
                filename   text PRIMARY KEY,
                checksum   char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO public.schema_migration (filename, checksum) VALUES (%s, %s) "
                "ON CONFLICT (filename) DO NOTHING",
                (path.name, hashlib.sha256(path.read_bytes()).hexdigest()),
            )


def _prepare_app_role(admin_dsn_value: str, app_dsn: str) -> None:
    """Give the two non-superuser roles the passwords the test DSNs expect.

    `norte_readonly` gets no grants here on purpose. Migration 007 already gave
    it exactly what it should have - SELECT on mart and nothing else - and the
    point of connecting as that role in a test is to find out whether that is
    still enough.
    """
    env = _env()
    password = env.get("POSTGRES_APP_PASSWORD") or urlparse(app_dsn).password
    if not password:
        raise RuntimeError("POSTGRES_APP_PASSWORD is not set")

    readonly_password = env.get("POSTGRES_READONLY_PASSWORD")
    if readonly_password:
        with psycopg.connect(admin_dsn_value, autocommit=True, connect_timeout=5) as ro, ro.cursor() as ro_cur:
            ro_cur.execute(
                sql.SQL("ALTER ROLE norte_readonly WITH PASSWORD {}").format(
                    sql.Literal(readonly_password)
                )
            )

    with psycopg.connect(admin_dsn_value, autocommit=True, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER ROLE norte_app WITH PASSWORD {}").format(sql.Literal(password))
        )
        cur.execute("ALTER ROLE norte_app WITH NOSUPERUSER NOBYPASSRLS")
        cur.execute("GRANT USAGE ON SCHEMA core, raw, stg, mart TO norte_app")
        cur.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, raw TO norte_app"
        )
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA stg, mart TO norte_app")
        cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, raw TO norte_app")
        cur.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, raw TO norte_app")


def seed_workspace(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    country_code: str = "CO",
    platform_code: str = "manual_xlsx",
    slug: str = "test",
) -> None:
    """Insert the minimum rows an ingestion run needs.

    Runs in the service context so RLS does not block the setup itself.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.tenant (id, slug, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (tenant_id, slug, f"Tenant {slug}"),
        )
        cur.execute(
            "INSERT INTO core.workspace_country (tenant_id, country_code) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (tenant_id, country_code),
        )
        # Migration 012 made scope explicit: a global platform (manual upload)
        # must be created WITHOUT a country, a country one WITH. The database
        # enforces it, so the helper has to ask rather than assume.
        cur.execute("SELECT scope FROM core.platform WHERE code = %s", (platform_code,))
        row = cur.fetchone()
        scope = row[0] if row else "country"
        connection_country = None if scope == "global" else country_code

        cur.execute(
            """
            INSERT INTO core.connection
                (id, tenant_id, country_code, platform_code, name, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON CONFLICT (id) DO NOTHING
            """,
            (
                connection_id, tenant_id, connection_country, platform_code,
                f"conn-{connection_id}",
            ),
        )
    conn.commit()


def truncate_data(conn: psycopg.Connection) -> None:
    """Wipe ingested data but keep catalogs and the seeded workspace.

    TRUNCATE needs table ownership, which norte_app does not have - and should
    not. Reconnect as the superuser for this one administrative act.
    """
    conn.commit()
    admin = admin_test_dsn()
    if not admin:
        raise RuntimeError("POSTGRES_ADMIN_URL is not available")

    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as admin_conn, admin_conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE core.movement, core.shipment, core.cs_interaction, core.ad_spend,
                     raw.load_discrepancy, raw.source_row, raw.load_batch, raw.upload_job,
                     core.carrier, core.geo, core.product_alias, core.product, core.supplier
            RESTART IDENTITY CASCADE
            """
        )
