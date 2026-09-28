"""Volumen de prueba de carga: varias empresas, un año de historia, ~200k guías.

SOLO PARA UNA BASE DESECHABLE DE DESARROLLO. Reutiliza el generador de
`scripts.seed_demo` (mismas proporciones de transportadoras, productos y
zonas) pero multiplicado, con varias empresas y usuarios de distintos roles:

    empresa     volumen     usuarios (contraseña común: STRESS_PASSWORD)
    stress-a    x6          owner, analyst, viewer (solo CO), uploader
    stress-b    x1.5        owner
    stress-c    x1.5        owner

    python -m scripts.seed_stress [--scale 1.0] [--days 365]

`--scale` multiplica todo el volumen (0.1 para una prueba rápida).
"""

from __future__ import annotations

import argparse
import copy
import time
from datetime import date
from uuid import UUID

from scripts import seed_demo as sd

STRESS_PASSWORD = "stress-masterdata-2026"

TENANTS = [
    # id, slug, volumen relativo, usuarios extra (rol, país o None)
    (
        UUID("5e000000-0000-4000-a000-00000000000a"), "stress-a", 6.0,
        [("analyst", None), ("viewer", "CO"), ("uploader", None)],
    ),
    (UUID("5e000000-0000-4000-a000-00000000000b"), "stress-b", 1.5, []),
    (UUID("5e000000-0000-4000-a000-00000000000c"), "stress-c", 1.5, []),
]


def _hash(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def _seed_tenant(conn, tenant_id: UUID, slug: str, factor: float, extras, days: int) -> None:
    password_hash = _hash(STRESS_PASSWORD)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.org (slug, name) VALUES (%s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (slug, f"Org {slug}"),
        )
        org_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO core.tenant (id, slug, name, org_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (tenant_id, slug, f"Empresa {slug}", org_id),
        )
        cur.execute(
            "INSERT INTO core.org_subscription (org_id, status, trial_ends_at) "
            "VALUES (%s, 'trial', now() + interval '365 days') ON CONFLICT (org_id) DO NOTHING",
            (org_id,),
        )
    conn.commit()

    # El generador de la demo trabaja sobre variables de módulo: se apuntan a
    # esta empresa y se escala el volumen diario de cada país.
    sd.DEMO_TENANT = tenant_id
    sd.DEMO_EMAIL = f"owner@{slug}.masterdata.dev"
    sd.DEMO_PASSWORD = STRESS_PASSWORD
    sd.TODAY = date.today()
    sd.DAYS_OF_HISTORY = days
    countries = copy.deepcopy(_ORIGINAL_COUNTRIES)
    for config in countries.values():
        low, high = config["daily_volume"]
        config["daily_volume"] = (max(1, round(low * factor)), max(1, round(high * factor)))
    sd.COUNTRIES = countries
    sd.seed(conn)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.app_user SET org_id = %s, is_org_admin = true "
            "WHERE lower(email) = %s RETURNING id",
            (org_id, sd.DEMO_EMAIL),
        )
        owner_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO core.membership (user_id, tenant_id, role) VALUES (%s, %s, 'owner') "
            "ON CONFLICT (user_id, tenant_id) DO NOTHING",
            (owner_id, tenant_id),
        )
        cur.execute(
            "INSERT INTO core.org_membership (user_id, org_id, role) VALUES (%s, %s, 'admin') "
            "ON CONFLICT (user_id, org_id) DO NOTHING",
            (owner_id, org_id),
        )
        for role, country in extras:
            email = f"{role}@{slug}.masterdata.dev"
            cur.execute(
                """
                INSERT INTO core.app_user
                    (tenant_id, org_id, email, password_hash, full_name, role)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, email) DO UPDATE SET password_hash = EXCLUDED.password_hash
                RETURNING id
                """,
                (tenant_id, org_id, email, password_hash, f"Usuario {role}", role),
            )
            user_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO core.membership (user_id, tenant_id, role, country_scope) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, tenant_id) DO NOTHING",
                (user_id, tenant_id, role, [country] if country else None),
            )
    conn.commit()


_ORIGINAL_COUNTRIES = copy.deepcopy(sd.COUNTRIES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    conn = sd.connect()
    try:
        for tenant_id, slug, factor, extras in TENANTS:
            started = time.monotonic()
            _seed_tenant(conn, tenant_id, slug, factor * args.scale, extras, args.days)
            print(f"{slug}: {time.monotonic() - started:.0f}s")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM core.shipment")
            shipments = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM core.movement")
            movements = cur.fetchone()["n"]
            cur.execute("ANALYZE")
        conn.commit()
        print(f"Guías: {shipments:,}  Movimientos: {movements:,}")
        print(f"Usuarios: owner@stress-a.masterdata.dev ... contraseña {STRESS_PASSWORD}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
