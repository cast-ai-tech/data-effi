"""Stand up the Distrilatam organisation and hand it to its owner.

WHAT THIS DOES, AND WHY IT IS A SCRIPT AND NOT A MIGRATION
A migration describes the SHAPE of the database and runs on every deployment,
including someone else's. This is one operator's data: an org, its first company,
the countries it sells in, and the person who runs it. Putting it in migrations/
would create Distrilatam on every install of Master Data, which is nonsense.

    python -m scripts.setup_distrilatam [--dry-run]

Idempotent: run it twice and the second run changes nothing. It never creates an
account - the person must already have one - because a password set by a script
is a password nobody chose.

WHAT IT DELIBERATELY DOES NOT TOUCH
The demo org and its company stay exactly as they are. The only thing it removes
is the owner's MEMBERSHIP in the demo company, so that logging in lands on their
own operation instead of a sandbox they have to look past.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

from pipeline.dbconn import connect as db_connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORG_SLUG = "distrilatam"
ORG_NAME = "Distrilatam"
COMPANY_SLUG = "distrilatam"
COMPANY_NAME = "Distrilatam"
OWNER_EMAIL = "donkey192@hotmail.com"

# The countries Distrilatam sells in. CR arrived with migration 034.
COUNTRIES = ["CO", "EC", "GT", "CR"]

DEMO_COMPANY_SLUG = "demo"


def _dsn() -> str:
    dsn = os.environ.get("POSTGRES_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "Falta DATABASE_URL (o POSTGRES_ADMIN_URL) en el entorno.\n"
            "Es la cadena de conexión de la base: la encuentras en Supabase, "
            "en Settings > Database."
        )
    return dsn


def run(conn: psycopg.Connection, *, dry_run: bool) -> None:
    # The service GUC lets this script see and write tenant tables before any
    # tenant context exists. It dies with the transaction, like everywhere else.
    conn.execute("SELECT set_config('norte.service', 'on', true)")

    owner = conn.execute(
        "SELECT id, email, full_name, tenant_id, org_id FROM core.app_user "
        "WHERE lower(email) = lower(%s)",
        (OWNER_EMAIL,),
    ).fetchone()
    if owner is None:
        raise SystemExit(
            f"No existe una cuenta con el correo {OWNER_EMAIL}.\n"
            "Créala primero desde la aplicación (invitación) y vuelve a correr esto."
        )
    print(f"  dueño        : {owner['full_name'] or '(sin nombre)'} <{owner['email']}>")

    org = conn.execute(
        "SELECT id, name FROM core.org WHERE slug = %s", (ORG_SLUG,)
    ).fetchone()
    if org is None:
        print(f"  organización : crear '{ORG_NAME}'")
        if not dry_run:
            org = conn.execute(
                "INSERT INTO core.org (slug, name, base_currency) VALUES (%s, %s, 'USD') "
                "RETURNING id, name",
                (ORG_SLUG, ORG_NAME),
            ).fetchone()
    else:
        print(f"  organización : ya existe '{org['name']}'")

    if dry_run and org is None:
        print("\n(dry-run) nada más que mostrar: la organización aún no existe.")
        return

    company = conn.execute(
        "SELECT id, name FROM core.tenant WHERE slug = %s", (COMPANY_SLUG,)
    ).fetchone()
    if company is None:
        print(f"  sociedad     : crear '{COMPANY_NAME}'")
        if not dry_run:
            company = conn.execute(
                "INSERT INTO core.tenant (slug, name, org_id) VALUES (%s, %s, %s) "
                "RETURNING id, name",
                (COMPANY_SLUG, COMPANY_NAME, org["id"]),
            ).fetchone()
    else:
        print(f"  sociedad     : ya existe '{company['name']}'")
        if not dry_run:
            conn.execute(
                "UPDATE core.tenant SET org_id = %s WHERE id = %s AND org_id IS DISTINCT FROM %s",
                (org["id"], company["id"], org["id"]),
            )

    if dry_run and company is None:
        print("\n(dry-run) la sociedad aún no existe; el resto depende de ella.")
        return

    unsupported = conn.execute(
        "SELECT code FROM core.country WHERE code = ANY(%s) AND NOT is_supported",
        (COUNTRIES,),
    ).fetchall()
    missing = set(COUNTRIES) - {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM core.country WHERE code = ANY(%s)", (COUNTRIES,)
        ).fetchall()
    }
    if missing or unsupported:
        raise SystemExit(
            f"Estos países no están en el catálogo: {', '.join(sorted(missing)) or '-'}. "
            "Agrégalos con una migración, como hizo la 034 con Costa Rica."
        )

    print(f"  países       : {', '.join(COUNTRIES)}")
    if not dry_run:
        for code in COUNTRIES:
            conn.execute(
                "INSERT INTO core.workspace_country (tenant_id, country_code) "
                "VALUES (%s, %s) "
                "ON CONFLICT (tenant_id, country_code) DO UPDATE SET is_active = true",
                (company["id"], code),
            )

    print("  accesos      : owner de la sociedad y admin de la organización")
    if not dry_run:
        conn.execute(
            "UPDATE core.app_user SET org_id = %s, tenant_id = %s, role = 'owner' WHERE id = %s",
            (org["id"], company["id"], owner["id"]),
        )
        conn.execute(
            "INSERT INTO core.membership (user_id, tenant_id, role) VALUES (%s, %s, 'owner') "
            "ON CONFLICT (user_id, tenant_id) DO UPDATE SET role = 'owner', is_active = true",
            (owner["id"], company["id"]),
        )
        conn.execute(
            "INSERT INTO core.org_membership (user_id, org_id, role) VALUES (%s, %s, 'admin') "
            "ON CONFLICT (user_id, org_id) DO UPDATE SET role = 'admin', is_active = true",
            (owner["id"], org["id"]),
        )

    demo = conn.execute(
        "SELECT id, name FROM core.tenant WHERE slug = %s", (DEMO_COMPANY_SLUG,)
    ).fetchone()
    if demo is not None:
        removed = conn.execute(
            "SELECT 1 FROM core.membership WHERE user_id = %s AND tenant_id = %s",
            (owner["id"], demo["id"]),
        ).fetchone()
        if removed:
            print(f"  demo         : quitar su acceso a '{demo['name']}'")
            if not dry_run:
                conn.execute(
                    "DELETE FROM core.membership WHERE user_id = %s AND tenant_id = %s",
                    (owner["id"], demo["id"]),
                )
        else:
            print(f"  demo         : ya no tiene acceso a '{demo['name']}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crear la organización Distrilatam")
    parser.add_argument(
        "--dry-run", action="store_true", help="Mostrar qué haría, sin escribir nada"
    )
    args = parser.parse_args()

    print("Distrilatam" + (" (dry-run)" if args.dry_run else ""))
    with db_connect(_dsn(), row_factory=dict_row) as conn:
        run(conn, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
            print("\n(dry-run) nada fue escrito.")
        else:
            conn.commit()
            print("\nListo. Entra con ese correo: abre directo en Distrilatam.")


if __name__ == "__main__":
    main()
