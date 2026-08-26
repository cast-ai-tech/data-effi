"""Conceder o quitar el rol de operador de la plataforma.

    python -m scripts.grant_platform_admin tu@correo.com
    python -m scripts.grant_platform_admin tu@correo.com --quitar
    python -m scripts.grant_platform_admin --listar

POR QUÉ ES UN SCRIPT Y NO UNA PANTALLA
--------------------------------------
Es el único rol que cruza organizaciones, y lo que lo hace seguro es que no
existe ningún camino desde la web para obtenerlo. No hay endpoint que lo
conceda, no hay casilla en un formulario, no hay campo en el registro que
alguien pueda mandar de más. Para volverse operador hay que tener acceso al
servidor, y eso ya es la frontera que separa a quien puede de quien no.

Una pantalla de "hacer admin a alguien" sería cómoda y abriría exactamente la
puerta que este diseño mantiene cerrada.

NO CREA CUENTAS, A PROPÓSITO
----------------------------
La persona tiene que registrarse antes, como cualquiera. Una contraseña puesta
por un script es una contraseña que nadie eligió, que acaba en el historial de
la terminal y que casi nunca se cambia. Igual que scripts/setup_distrilatam.py.

QUÉ ABRE Y QUÉ NO
-----------------
ABRE     las capturas de conexión, el catálogo de plataformas, y la lista de
         organizaciones con su plan y su salud.
NO ABRE  los datos de ningún comerciante. Ni guías, ni movimientos, ni
         compradores, ni dinero. Este rol no aparece en ninguna política de RLS
         (migración 053), así que no es una promesa del código de la aplicación:
         es el motor el que no se los entrega.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.dbconn import connect


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Concede o quita el rol de operador de la plataforma"
    )
    parser.add_argument("email", nargs="?", help="Correo de una cuenta que YA existe")
    parser.add_argument("--quitar", action="store_true", help="Retirar el rol")
    parser.add_argument("--listar", action="store_true", help="Quién lo tiene hoy")
    args = parser.parse_args()

    dsn = os.environ.get("POSTGRES_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("Falta POSTGRES_ADMIN_URL (o DATABASE_URL) en el entorno.", file=sys.stderr)
        return 1

    conn = connect(dsn, autocommit=True)
    try:
        if args.listar or not args.email:
            filas = conn.execute(
                "SELECT email, full_name FROM core.app_user "
                "WHERE is_platform_admin ORDER BY email"
            ).fetchall()
            if filas:
                print("\n  Operadores de la plataforma:")
                for email, nombre in filas:
                    print(f"    · {email}  ({nombre})")
            else:
                print("\n  Todavía no hay ningún operador.")
            if not args.email:
                print("\n  Para conceder el rol:")
                print("    python -m scripts.grant_platform_admin correo@ejemplo.com\n")
            return 0

        correo = args.email.strip().lower()
        persona = conn.execute(
            "SELECT id, full_name, is_platform_admin FROM core.app_user "
            "WHERE lower(email) = %s",
            (correo,),
        ).fetchone()

        if persona is None:
            print(
                f"\n  No hay ninguna cuenta con el correo «{correo}».\n\n"
                "  Este script no crea cuentas: una contraseña puesta por un script\n"
                "  es una contraseña que nadie eligió. Regístrate primero en la app\n"
                "  con ese correo y vuelve a ejecutar esto.\n",
                file=sys.stderr,
            )
            return 1

        user_id, nombre, ya_lo_tiene = persona
        nuevo = not args.quitar

        if ya_lo_tiene == nuevo:
            estado = "ya es" if nuevo else "ya no era"
            print(f"\n  Sin cambios: {correo} {estado} operador de la plataforma.\n")
            return 0

        conn.execute(
            "UPDATE core.app_user SET is_platform_admin = %s WHERE id = %s",
            (nuevo, user_id),
        )

        if nuevo:
            print(f"\n  Listo. {nombre} ({correo}) ya es operador de la plataforma.")
            print("\n  Puede ver: capturas de conexión, catálogo de plataformas y la")
            print("  lista de organizaciones con su plan y su estado.")
            print("  NO puede ver: guías, movimientos, compradores ni plata de nadie.")
            print("\n  Si tenía la sesión abierta, no hace falta que vuelva a entrar:")
            print("  el rol se lee de la base en cada petición, no del token.\n")
        else:
            print(f"\n  Retirado. {nombre} ({correo}) ya no opera la plataforma.")
            print("  Tiene efecto inmediato, incluso con la sesión abierta.\n")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
