"""Apply every migration in order, as the superuser.

Migrations are plain .sql files applied in filename order. Each one is written to
be idempotent, so re-running them is safe and is exactly what happens every time
the stack starts.

    python -m scripts.migrate
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
CONNECT_RETRIES = 30
RETRY_DELAY_SECONDS = 2


def wait_for_database(dsn: str) -> psycopg.Connection:
    """The database container may still be starting. Wait, do not crash."""
    last_error: Exception | None = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            return psycopg.connect(dsn, autocommit=True)
        except psycopg.OperationalError as exc:
            last_error = exc
            print(f"  esperando a PostgreSQL… ({attempt}/{CONNECT_RETRIES})")
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"PostgreSQL no respondió: {last_error}")


def main() -> int:
    dsn = os.environ.get("POSTGRES_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("POSTGRES_ADMIN_URL is not set", file=sys.stderr)
        return 1

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No hay migraciones en {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    conn = wait_for_database(dsn)
    try:
        for path in files:
            print(f"  aplicando {path.name}")
            try:
                conn.execute(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"\nFalló {path.name}:\n{exc}", file=sys.stderr)
                return 1
    finally:
        conn.close()

    print(f"{len(files)} migraciones aplicadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
