"""Set the passwords for the database roles Data Effi connects with.

Kept out of the migrations on purpose: a password written into a .sql file is a
password in git history forever. This reads them from the environment.

    python -m scripts.setup_roles

Two roles, and the distinction matters:

  norte_app       what the API and worker connect as. NOT a superuser and NOT the
                  owner of any table, because PostgreSQL exempts both from
                  row-level security - a superuser connection would silently
                  bypass every tenant policy.
  norte_readonly  what generated NL->SQL runs as. SELECT on mart, nothing else.
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql

ROLES = {
    "norte_app": "POSTGRES_APP_PASSWORD",
    "norte_readonly": "POSTGRES_READONLY_PASSWORD",
}

MIN_PASSWORD_LENGTH = 16


def main() -> int:
    dsn = os.environ.get("POSTGRES_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("POSTGRES_ADMIN_URL is not set", file=sys.stderr)
        return 1

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for role, env_var in ROLES.items():
            password = os.environ.get(env_var)
            if not password:
                print(
                    f"{env_var} is not set. "
                    "Generate one with: openssl rand -hex 24",
                    file=sys.stderr,
                )
                return 1
            if len(password) < MIN_PASSWORD_LENGTH:
                print(f"{env_var} is too short (minimum {MIN_PASSWORD_LENGTH})", file=sys.stderr)
                return 1

            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                print(f"Role {role} does not exist. Apply migration 007 first.", file=sys.stderr)
                return 1

            # ALTER ROLE does not accept bind parameters, so the password has to
            # be rendered into the statement. sql.Literal does the quoting and
            # escaping; never build this string with an f-string.
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )

            # Belt and braces: neither role may ever bypass row-level security.
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH NOSUPERUSER NOBYPASSRLS").format(
                    sql.Identifier(role)
                )
            )
            print(f"  {role}: password set, NOSUPERUSER NOBYPASSRLS confirmed")

        # Re-assert grants in case new tables or views appeared since 007.
        cur.execute("GRANT USAGE ON SCHEMA mart TO norte_readonly")
        # A blanket grant plus the ALTER DEFAULT PRIVILEGES in migration 007 is
        # what silently handed the copilot every row-level view created after
        # it. Migration 025 withdrew both; re-granting here would reopen the
        # hole on every fresh database, so the explicit list is the source of
        # truth and this only removes what should never have been given.
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA mart TO norte_readonly")
        cur.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA mart "
            "REVOKE SELECT ON TABLES FROM norte_readonly"
        )
        cur.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA mart FROM norte_readonly")
        # Grants mirror the validator's allow list exactly. Importing it here is
        # deliberate: two hand-maintained lists drift, and the drift is silent
        # in the direction that matters.
        from ai.nl2sql import ALLOWED_VIEWS

        for view in sorted(ALLOWED_VIEWS):
            cur.execute(f"GRANT SELECT ON mart.{view} TO norte_readonly")
        cur.execute("GRANT USAGE ON SCHEMA core, raw, stg, mart TO norte_app")
        cur.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, raw TO norte_app"
        )
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA stg, mart TO norte_app")
        cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, raw TO norte_app")
        cur.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, raw TO norte_app")

    print("Roles listos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
