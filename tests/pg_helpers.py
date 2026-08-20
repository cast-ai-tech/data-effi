"""Helpers for the PostgreSQL-backed tests.

Creates a throwaway `norte_test` database, applies every migration in order, and
seeds the minimum a load needs: one tenant, one active country, one connection.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import UUID

import psycopg

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


def admin_dsn() -> str | None:
    """DSN pointing at the maintenance database, used to create the test DB."""
    env = {**load_dotenv(), **os.environ}
    dsn = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
    if not dsn:
        return None
    return re.sub(r"/[^/?]+(\?|$)", r"/postgres\1", dsn)


def resolve_test_dsn() -> str | None:
    env = {**load_dotenv(), **os.environ}
    dsn = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
    if not dsn:
        return None
    return re.sub(r"/[^/?]+(\?|$)", rf"/{TEST_DB_NAME}\1", dsn)


def recreate_test_database() -> str:
    """Drop and recreate the test database, then apply all migrations."""
    admin = admin_dsn()
    target = resolve_test_dsn()
    if not admin or not target:
        raise RuntimeError("No DATABASE_URL available for the PostgreSQL tests")

    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

    apply_migrations(target)
    return target


def apply_migrations(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


def seed_workspace(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    country_code: str = "CO",
    platform_code: str = "manual_xlsx",
    slug: str = "test",
) -> None:
    """Insert the minimum rows an ingestion run needs."""
    with conn.cursor() as cur:
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
    """Wipe ingested data but keep catalogs and the seeded workspace."""
    with conn.cursor() as cur:
        cur.execute(
            """
            TRUNCATE core.movement, core.shipment, core.cs_interaction, core.ad_spend,
                     raw.load_discrepancy, raw.source_row, raw.load_batch,
                     core.carrier, core.geo, core.product_alias, core.product, core.supplier
            RESTART IDENTITY CASCADE
            """
        )
    conn.commit()
