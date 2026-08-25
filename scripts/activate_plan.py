"""Activate (or close) a plan for an organisation - what the advisor runs after
the customer paid. Billing is manual on purpose (migration 048).

    python -m scripts.activate_plan --email dueno@empresa.com --plan master_pro
    python -m scripts.activate_plan --email dueno@empresa.com --plan custom --months 12
    python -m scripts.activate_plan --email dueno@empresa.com --plan master --months 0   # sin vencimiento
    python -m scripts.activate_plan --email dueno@empresa.com --expire
    python -m scripts.activate_plan --list

`--email` is any account of the organisation. Uses DATABASE_URL from .env.
"""

from __future__ import annotations

import argparse
import sys

from api.billing import activate_plan, expire_subscription
from api.db import fetch_all, fetch_one
from pipeline.dbconn import connect as db_connect


def main() -> int:
    parser = argparse.ArgumentParser(description="Activa o cierra el plan de una organización")
    parser.add_argument("--email", help="Correo de cualquier usuario de la organización")
    parser.add_argument("--plan", help="master | master_pro | master_elite | custom")
    parser.add_argument(
        "--months", type=int, default=1,
        help="Meses de vigencia (0 = sin vencimiento). Por defecto 1.",
    )
    parser.add_argument("--notes", help="Nota interna (quién pagó, cómo, cuánto)")
    parser.add_argument("--expire", action="store_true", help="Cerrar la suscripción")
    parser.add_argument("--list", action="store_true", help="Ver todas las organizaciones")
    args = parser.parse_args()

    with db_connect() as conn:
        if args.list:
            rows = fetch_all(
                conn,
                """
                SELECT o.name, o.slug, s.status, s.plan_code, s.requested_plan_code,
                       s.trial_ends_at::date AS trial_ends, s.current_period_end::date AS period_end,
                       (SELECT count(*) FROM core.tenant t WHERE t.org_id = o.id) AS empresas
                FROM core.org o
                LEFT JOIN core.org_subscription s ON s.org_id = o.id
                ORDER BY o.created_at
                """,
            )
            for row in rows:
                print(
                    f"{row['name']:<32} {row['status'] or '-':<8} plan={row['plan_code'] or '-':<13} "
                    f"pedido={row['requested_plan_code'] or '-':<13} prueba_hasta={row['trial_ends']} "
                    f"vence={row['period_end'] or '-'} empresas={row['empresas']}"
                )
            return 0

        if not args.email:
            parser.error("--email es obligatorio (o usa --list)")
        person = fetch_one(
            conn,
            "SELECT u.id, u.org_id, o.name FROM core.app_user u JOIN core.org o ON o.id = u.org_id "
            "WHERE lower(u.email) = lower(%s)",
            (args.email,),
        )
        if person is None or person["org_id"] is None:
            print(f"No hay una organización para {args.email}", file=sys.stderr)
            return 1

        if args.expire:
            expire_subscription(conn, person["org_id"], notes=args.notes)
            conn.commit()
            print(f"Suscripción de {person['name']} cerrada.")
            return 0

        if not args.plan:
            parser.error("--plan es obligatorio")
        state = activate_plan(
            conn, person["org_id"], args.plan,
            months=None if args.months == 0 else args.months, notes=args.notes,
        )
        conn.commit()
        print(f"{person['name']}: {state.message} Empresas permitidas: {state.max_tenants or 'sin límite'}.")
        print(f"Vence: {state.current_period_end or 'sin vencimiento'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
