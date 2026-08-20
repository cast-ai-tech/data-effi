"""Run real Effi exports through the pipeline and report what happened.

A verification harness, not a test: it reads files from wherever you point it,
loads them into a scratch tenant, and prints what Norte understood. The real
files carry customer PII and never enter the repository - this script exists so
they never have to.

    python -m scripts.check_real_effi "F:/Descargas/Reporte de Gu*.xlsx" \
                                      "F:/Descargas/Reporte de movimientos*.xls"
"""

from __future__ import annotations

import glob
import os
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.ingest import IngestEngine
from pipeline.models import BatchKind
from pipeline.profiles import detect_profile
from pipeline.readers import read_tabular, sniff_format
from pipeline.store_pg import PostgresStore

SCRATCH_TENANT = UUID("eeeeeeee-0000-4000-e000-000000000001")
SCRATCH_CONNECTION = UUID("eeeeeeee-1111-4000-e000-000000000001")
COUNTRY = "EC"


def connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)
    conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', false)")
    conn.commit()
    return conn


def prepare(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.tenant WHERE id = %s", (SCRATCH_TENANT,))
        cur.execute(
            "INSERT INTO core.tenant (id, slug, name) VALUES (%s, 'scratch-effi', 'Scratch Effi')",
            (SCRATCH_TENANT,),
        )
        cur.execute(
            "INSERT INTO core.workspace_country (tenant_id, country_code) VALUES (%s, %s)",
            (SCRATCH_TENANT, COUNTRY),
        )
        cur.execute(
            """
            INSERT INTO core.connection
                (id, tenant_id, country_code, platform_code, name, status, consent_granted_at)
            VALUES (%s, %s, %s, 'effi', 'Effi scratch', 'active', now())
            """,
            (SCRATCH_CONNECTION, SCRATCH_TENANT, COUNTRY),
        )
    conn.commit()


def describe(path: Path) -> None:
    payload = path.read_bytes()
    fmt = sniff_format(payload, path.name)
    headers, rows = read_tabular(payload, path.name)
    profile = detect_profile(headers)

    print(f"\n{'=' * 74}")
    print(f"ARCHIVO      {path.name}")
    print(f"  tamaño     {len(payload):,} bytes")
    print(f"  formato    {fmt}  (por contenido, no por extensión)")
    print(f"  columnas   {len(headers)}")
    print(f"  filas      {len(rows):,}")
    print(f"  perfil     {profile.label if profile else 'NO RECONOCIDO'}")


def load(conn: psycopg.Connection, path: Path, kind: BatchKind) -> None:
    payload = path.read_bytes()
    store = PostgresStore(conn)
    engine = IngestEngine(store, pii_salt=os.environ.get("PII_HASH_SALT", "scratch-salt"))

    report = engine.ingest(
        payload=payload,
        source_name=path.name,
        kind=kind,
        tenant_id=SCRATCH_TENANT,
        connection_id=SCRATCH_CONNECTION,
        country_code=COUNTRY,
        platform_code="effi",
        default_currency="USD",
    )
    conn.commit()

    print("\n  RESULTADO DE LA CARGA")
    print(f"    detectado como   {report.profile_label or 'sin perfil'}")
    print(f"    filas            {report.rows_total:,}")
    print(f"    insertadas       {report.rows_inserted:,}")
    print(f"    actualizadas     {report.rows_updated:,}")
    print(f"    sin cambios      {report.rows_skipped:,}")
    print(f"    con error        {report.rows_failed:,}")
    print(f"    discrepancias    {len(report.discrepancies):,}")

    if report.errors:
        print("\n    PRIMEROS ERRORES:")
        for err in report.errors[:5]:
            print(f"      fila {err.row_number}: {err.message[:110]}")

    if report.sanity_issues:
        codes = Counter(issue.code for issue in report.sanity_issues)
        print("\n    AVISOS:")
        for code, count in codes.most_common():
            example = next(i for i in report.sanity_issues if i.code == code)
            print(f"      {count:>5}  {code}: {example.message[:80]}")

    if report.unmapped_columns:
        print(f"\n    COLUMNAS IGNORADAS ({len(report.unmapped_columns)}):")
        for column in report.unmapped_columns[:8]:
            print(f"      · {column}")
        if len(report.unmapped_columns) > 8:
            print(f"      … y {len(report.unmapped_columns) - 8} más")


def verify(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(SCRATCH_TENANT),))

        print(f"\n{'=' * 74}")
        print("LO QUE QUEDÓ EN LA BASE DE DATOS\n")

        cur.execute(
            """
            SELECT sc.label, count(*) AS n
            FROM core.shipment s JOIN core.status_canon sc ON sc.code = s.status_code
            WHERE s.tenant_id = %s GROUP BY sc.label, sc.sort_order ORDER BY sc.sort_order
            """,
            (SCRATCH_TENANT,),
        )
        print("  ESTADOS")
        for row in cur.fetchall():
            print(f"    {row['n']:>6}  {row['label']}")

        cur.execute(
            "SELECT name, count(*) AS n FROM core.shipment s JOIN core.carrier c "
            "ON c.id = s.carrier_id WHERE s.tenant_id = %s GROUP BY name ORDER BY n DESC",
            (SCRATCH_TENANT,),
        )
        print("\n  TRANSPORTADORAS")
        for row in cur.fetchall():
            print(f"    {row['n']:>6}  {row['name']}")

        cur.execute(
            "SELECT p.name, count(*) AS n, sum(s.quantity) AS unidades FROM core.shipment s "
            "JOIN core.product p ON p.id = s.product_id WHERE s.tenant_id = %s "
            "GROUP BY p.name ORDER BY n DESC",
            (SCRATCH_TENANT,),
        )
        print("\n  PRODUCTOS")
        for row in cur.fetchall():
            print(f"    {row['n']:>6} guías  {row['unidades']:>6} unidades  {row['name']}")

        cur.execute(
            "SELECT g.level1_name, count(*) AS n FROM core.shipment s JOIN core.geo g "
            "ON g.id = s.geo_id WHERE s.tenant_id = %s GROUP BY g.level1_name "
            "ORDER BY n DESC LIMIT 8",
            (SCRATCH_TENANT,),
        )
        print("\n  PROVINCIAS (top 8)")
        for row in cur.fetchall():
            print(f"    {row['n']:>6}  {row['level1_name']}")

        cur.execute(
            """
            SELECT mt.label, count(*) AS n, sum(m.amount)::numeric(14,2) AS total
            FROM core.movement m JOIN core.movement_type mt ON mt.code = m.movement_type_code
            WHERE m.tenant_id = %s GROUP BY mt.label ORDER BY n DESC
            """,
            (SCRATCH_TENANT,),
        )
        rows = cur.fetchall()
        if rows:
            print("\n  MOVIMIENTOS DE DINERO")
            for row in rows:
                print(f"    {row['n']:>6}  {row['total']:>14,}  {row['label']}")

        cur.execute(
            "SELECT count(*) FILTER (WHERE shipment_id IS NOT NULL) AS ligados, "
            "count(*) FILTER (WHERE shipment_id IS NULL) AS huerfanos "
            "FROM core.movement WHERE tenant_id = %s",
            (SCRATCH_TENANT,),
        )
        row = cur.fetchone()
        if row and (row["ligados"] or row["huerfanos"]):
            total = row["ligados"] + row["huerfanos"]
            pct = row["ligados"] / total * 100 if total else 0
            print(f"\n  LIGADO GUÍA↔DINERO: {row['ligados']:,} de {total:,} ({pct:.1f}%)")

        cur.execute(
            "SELECT count(*) AS n FROM core.shipment WHERE tenant_id = %s "
            "AND customer_hash IS NOT NULL",
            (SCRATCH_TENANT,),
        )
        print(f"\n  PII: {cur.fetchone()['n']:,} guías con hash de cliente "
              f"(ningún teléfono en claro)")

        cur.execute("SELECT * FROM mart.v_problem_rate ORDER BY shipments DESC")
        rows = cur.fetchall()
        if rows:
            print("\n  TASA DE PROBLEMA (novedad + oficina + devolución)")
            for row in rows:
                print(
                    f"    {row['carrier_name']:<18} {row['shipments']:>5} guías  "
                    f"problema {row['problem_rate_pct']}%  "
                    f"(novedad {row['novedad']}, oficina {row['en_oficina']}, "
                    f"devolución {row['devolucion']})"
                )

        cur.execute("SELECT * FROM mart.v_cash_cycle")
        for row in cur.fetchall():
            print(
                f"\n  CICLO DE CAJA: p50 {row['p50_days_to_cash']} días · "
                f"p90 {row['p90_days_to_cash']} días · "
                f"{row['delivered_unsettled']} entregadas sin liquidar "
                f"({row['cash_in_transit']} {row['currency_code']} en camino)"
            )


def main() -> int:
    patterns = sys.argv[1:]
    if not patterns:
        print(__doc__)
        return 1

    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    if not paths:
        print("Ningún archivo coincide con esos patrones", file=sys.stderr)
        return 1

    conn = connect()
    try:
        prepare(conn)

        # Guides first so the movements have something to attach to; then the
        # other order is exercised by relink_orphan_movements anyway.
        for path in sorted(paths, key=lambda p: 0 if "gu" in p.name.lower() else 1):
            describe(path)
            profile = detect_profile(read_tabular(path.read_bytes(), path.name)[0])
            if profile is None:
                print("  -> sin perfil reconocido, se omite la carga")
                continue
            load(conn, path, profile.kind)

        verify(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
