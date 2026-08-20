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
TEST_DB_NAME = "norte_test"


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


def recreate_test_database() -> str:
    """Drop and recreate the test database, apply migrations, return the app DSN."""
    admin = admin_dsn()
    admin_target = admin_test_dsn()
    app_target = resolve_test_dsn()
    if not admin or not admin_target or not app_target:
        raise RuntimeError("No DATABASE_URL available for the PostgreSQL tests")

    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

    apply_migrations(admin_target)
    _prepare_app_role(admin_target, app_target)
    return app_target


def apply_migrations(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


def _prepare_app_role(admin_dsn_value: str, app_dsn: str) -> None:
    """Give norte_app the password the test DSN expects, and confirm its grants."""
    env = _env()
    password = env.get("POSTGRES_APP_PASSWORD") or urlparse(app_dsn).password
    if not password:
        raise RuntimeError("POSTGRES_APP_PASSWORD is not set")

    with psycopg.connect(admin_dsn_value, autocommit=True) as conn, conn.cursor() as cur:
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
        cur.execute(
            """
            INSERT INTO core.connection
                (id, tenant_id, country_code, platform_code, name, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON CONFLICT (id) DO NOTHING
            """,
            (connection_id, tenant_id, country_code, platform_code, f"conn-{connection_id}"),
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

    with psycopg.connect(admin, autocommit=True) as admin_conn, admin_conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE core.movement, core.shipment, core.cs_interaction, core.ad_spend,
                     raw.load_discrepancy, raw.source_row, raw.load_batch, raw.upload_job,
                     core.carrier, core.geo, core.product_alias, core.product, core.supplier
            RESTART IDENTITY CASCADE
            """
        )
