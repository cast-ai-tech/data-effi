"""Apply pending migrations, as the superuser.

WHY THERE IS A LEDGER AND NOT JUST "RE-RUN EVERYTHING". Each migration is written
to be idempotent on its own, and for a while re-applying all of them on every
boot worked fine. It stopped working the moment migration 008 ADDED columns to a
view that 003 creates: re-running 003 then tries to recreate that view with
fewer columns, and PostgreSQL refuses with "cannot drop columns from view".

That is not a flaw in 003 or in 008 - it is what happens when a schema evolves.
So the ledger below records which files have been applied, and only new ones
run. A file that changes after it was applied is reported loudly rather than
silently skipped, because an edited migration means the database and the
repository disagree about what the schema is.

    python -m scripts.migrate
    python -m scripts.migrate --status     # list without applying
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg

from pipeline.dbconn import connect as db_connect

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
CONNECT_RETRIES = 30
RETRY_DELAY_SECONDS = 2

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migration (
    filename   text PRIMARY KEY,
    checksum   char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def wait_for_database(dsn: str) -> psycopg.Connection:
    """The database container may still be starting. Wait, do not crash."""
    last_error: Exception | None = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            return db_connect(dsn, autocommit=True)
        except psycopg.OperationalError as exc:
            last_error = exc
            print(f"  esperando a PostgreSQL… ({attempt}/{CONNECT_RETRIES})")
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"PostgreSQL no respondió: {last_error}")


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    conn.execute(LEDGER_DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM public.schema_migration")
        return dict(cur.fetchall())


def adopt_existing_schema(conn: psycopg.Connection, files: list[Path]) -> bool:
    """Baseline a database that predates the ledger.

    If core.shipment already exists but nothing is recorded, this database was
    migrated before the ledger existed. Re-running those files would fail on the
    view-column problem described above, so they are recorded as applied. Said
    out loud rather than done quietly - it is the one case where this script
    trusts the database over the files.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('core.shipment') IS NOT NULL")
        has_schema = bool(cur.fetchone()[0])

    if not has_schema:
        return False

    print("  base de datos existente sin registro de migraciones:")
    print("  se marcan las actuales como aplicadas (baseline)")
    with conn.cursor() as cur:
        for path in files:
            cur.execute(
                "INSERT INTO public.schema_migration (filename, checksum) VALUES (%s, %s) "
                "ON CONFLICT (filename) DO NOTHING",
                (path.name, checksum(path)),
            )
    return True


def main() -> int:
    dsn = os.environ.get("POSTGRES_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("POSTGRES_ADMIN_URL is not set", file=sys.stderr)
        return 1

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No hay migraciones en {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    status_only = "--status" in sys.argv

    conn = wait_for_database(dsn)
    try:
        applied = applied_migrations(conn)

        if not applied and adopt_existing_schema(conn, files):
            applied = applied_migrations(conn)

        pending: list[Path] = []
        changed: list[str] = []

        for path in files:
            digest = checksum(path)
            if path.name not in applied:
                pending.append(path)
            elif applied[path.name] != digest:
                changed.append(path.name)

        if changed:
            print("\n  AVISO: estas migraciones cambiaron después de aplicarse:")
            for name in changed:
                print(f"    · {name}")
            print("  La base de datos NO se modificó. Crea una migración nueva en vez")
            print("  de editar una ya aplicada.\n")

        if status_only:
            print(f"  aplicadas: {len(applied)}   pendientes: {len(pending)}")
            for path in pending:
                print(f"    pendiente  {path.name}")
            return 1 if changed else 0

        if not pending:
            print(f"Sin migraciones pendientes ({len(applied)} ya aplicadas).")
            return 0

        for path in pending:
            print(f"  aplicando {path.name}")
            try:
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO public.schema_migration (filename, checksum) VALUES (%s, %s)",
                    (path.name, checksum(path)),
                )
            except Exception as exc:
                print(f"\nFalló {path.name}:\n{exc}", file=sys.stderr)
                return 1

        print(f"{len(pending)} migraciones aplicadas.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
