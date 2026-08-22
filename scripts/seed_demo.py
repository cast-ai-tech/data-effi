"""Load a demonstration workspace.

Builds a believable three-country operation so every screen has something true
to say on a fresh install:

  Ecuador (USD)   - full data: guides, money, ads, customer service
  Colombia (COP)  - guides and money, no ads connection -> CPA widgets BLOCKED
  Guatemala (GTQ) - guides only, thin volume -> several widgets degraded

The Ecuador figures reproduce a real operation's shape: carrier delivery rates
of 45-53%, four products with very different margins, and provinces that range
from acceptable to bad. The point is that the demo shows a business with real
problems, because a demo where everything is green teaches nothing.

    python -m scripts.seed_demo [--reset]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from pipeline.dbconn import connect as db_connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_TENANT = UUID("d0000000-0000-4000-a000-000000000001")
DEMO_EMAIL = "demo@dataeffi.co"
DEMO_PASSWORD = "demo-dataeffi-2026"

# Deterministic: the same seed produces the same demo every time, so a bug
# someone reports on the demo data is reproducible.
RNG = random.Random(20260820)

TODAY = date(2026, 8, 20)
DAYS_OF_HISTORY = 75

# --- Ecuador -----------------------------------------------------------------
EC_CARRIERS = [
    # name, share of volume, delivery rate, return rate
    ("Servientrega", 0.60, 0.451, 0.290),
    ("Gintracom", 0.27, 0.512, 0.324),
    ("Laarcourier", 0.13, 0.527, 0.253),
]
EC_PRODUCTS = [
    # name, share, delivery rate, price, cost, supplier
    ("Drenaje Linfático", 0.42, 0.449, Decimal("32.30"), Decimal("11.50"), "Proveedor Quito"),
    ("Clorofila Detox", 0.25, 0.467, Decimal("28.10"), Decimal("9.80"), "Proveedor Quito"),
    ("Zooone", 0.28, 0.339, Decimal("24.60"), Decimal("12.40"), "Importadora Sur"),
    ("Beevena Apitoxina", 0.05, 0.539, Decimal("25.10"), Decimal("8.90"), "Importadora Sur"),
]
EC_GEO = [
    ("Manabí", "Portoviejo", 0.18, 0.526),
    ("Guayas", "Guayaquil", 0.34, 0.418),
    ("El Oro", "Machala", 0.12, 0.351),
    ("Tungurahua", "Ambato", 0.11, 0.500),
    ("Pichincha", "Quito", 0.25, 0.548),
]

# --- Colombia ----------------------------------------------------------------
CO_CARRIERS = [
    ("Interrapidisimo", 0.45, 0.712, 0.244),
    ("Envia", 0.33, 0.681, 0.279),
    ("Coordinadora", 0.22, 0.744, 0.201),
]
CO_PRODUCTS = [
    ("Faja Reductora", 0.38, 0.703, Decimal("89900"), Decimal("35000"), "Bodega Bogotá"),
    ("Reloj Inteligente", 0.31, 0.688, Decimal("149900"), Decimal("62000"), "Bodega Bogotá"),
    ("Kit Cuidado Facial", 0.20, 0.735, Decimal("119900"), Decimal("41000"), "Dropi Medellín"),
    ("Lámpara Solar", 0.11, 0.612, Decimal("79900"), Decimal("38000"), "Dropi Medellín"),
]
CO_GEO = [
    ("Cundinamarca", "Bogotá", 0.31, 0.742),
    ("Antioquia", "Medellín", 0.21, 0.718),
    ("Valle del Cauca", "Cali", 0.17, 0.665),
    ("Atlántico", "Barranquilla", 0.13, 0.588),
    ("Santander", "Bucaramanga", 0.10, 0.701),
    ("Bolívar", "Cartagena", 0.08, 0.554),
]

# --- Guatemala ---------------------------------------------------------------
GT_CARRIERS = [("Cargo Express", 0.70, 0.612, 0.310), ("Guatex", 0.30, 0.588, 0.335)]
GT_PRODUCTS = [
    ("Faja Reductora", 0.55, 0.605, Decimal("230.00"), Decimal("95.00"), "Bodega Guatemala"),
    ("Clorofila Detox", 0.45, 0.598, Decimal("195.00"), Decimal("78.00"), "Bodega Guatemala"),
]
GT_GEO = [
    ("Guatemala", "Ciudad de Guatemala", 0.62, 0.641),
    ("Quetzaltenango", "Quetzaltenango", 0.23, 0.572),
    ("Escuintla", "Escuintla", 0.15, 0.549),
]

COUNTRIES = {
    "EC": {
        "carriers": EC_CARRIERS,
        "products": EC_PRODUCTS,
        "geo": EC_GEO,
        "daily_volume": (18, 34),
        "freight": Decimal("2.95"),
        "return_freight": Decimal("2.40"),
        "has_ads": True,
        "has_cs": True,
        "daily_ad_spend": (140, 320),
    },
    "CO": {
        "carriers": CO_CARRIERS,
        "products": CO_PRODUCTS,
        "geo": CO_GEO,
        "daily_volume": (26, 52),
        "freight": Decimal("12000"),
        "return_freight": Decimal("9500"),
        # No ads connection on purpose: this is what makes the CPA widget show
        # up BLOCKED in the demo, which is a feature, not a gap.
        "has_ads": False,
        "has_cs": False,
        "daily_ad_spend": (0, 0),
    },
    "GT": {
        "carriers": GT_CARRIERS,
        "products": GT_PRODUCTS,
        "geo": GT_GEO,
        "daily_volume": (4, 11),
        "freight": Decimal("35.00"),
        "return_freight": Decimal("28.00"),
        "has_ads": False,
        "has_cs": False,
        "daily_ad_spend": (0, 0),
    },
}

FX_RATES = {"USD": 1.0, "COP": 1 / 3980.0, "GTQ": 1 / 7.78}


def pick(options: list[tuple], weight_index: int = 1):
    """Weighted choice over (name, weight, ...) tuples."""
    total = sum(option[weight_index] for option in options)
    threshold = RNG.random() * total
    running = 0.0
    for option in options:
        running += option[weight_index]
        if running >= threshold:
            return option
    return options[-1]


def customer_hash(seed: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{seed}".encode()).hexdigest()


def connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)
    conn = db_connect(dsn, autocommit=False, row_factory=dict_row)
    with conn.cursor() as cur:
        # Seeding legitimately writes across tenants; see migration 007.
        cur.execute("SELECT set_config('norte.service', 'on', false)")
    return conn


def reset(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.tenant WHERE id = %s", (DEMO_TENANT,))
    conn.commit()
    print("Previous demo data removed.")


def seed(conn: psycopg.Connection) -> None:
    salt = os.environ.get("PII_HASH_SALT", "demo-salt-not-a-real-secret")

    try:
        from argon2 import PasswordHasher

        password_hash = PasswordHasher().hash(DEMO_PASSWORD)
    except ImportError:
        print("argon2-cffi is required to seed the demo user", file=sys.stderr)
        raise SystemExit(1) from None

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.tenant (id, slug, name) VALUES (%s, 'demo', 'Operación Demo') "
            "ON CONFLICT (id) DO NOTHING",
            (DEMO_TENANT,),
        )
        cur.execute(
            """
            INSERT INTO core.app_user (tenant_id, email, password_hash, full_name, role)
            VALUES (%s, %s, %s, 'Cuenta de demostración', 'owner')
            ON CONFLICT (tenant_id, email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            """,
            (DEMO_TENANT, DEMO_EMAIL, password_hash),
        )

        for code, config in COUNTRIES.items():
            cur.execute(
                "INSERT INTO core.workspace_country (tenant_id, country_code, maturation_days) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (DEMO_TENANT, code, 21 if code != "EC" else 11),
            )
            cur.execute(
                "SELECT currency_code FROM core.country WHERE code = %s", (code,)
            )
            currency = cur.fetchone()["currency_code"]
            config["currency"] = currency

            cur.execute(
                """
                INSERT INTO core.store (tenant_id, country_code, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (tenant_id, country_code, name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                (DEMO_TENANT, code, f"Tienda {code}"),
            )
            config["store_id"] = cur.fetchone()["id"]

            # One global manual-upload connection for the whole workspace: the
            # file declares its own country, so three would be two too many.
            config["connection_id"] = _connection(
                cur, code, "manual_xlsx", "Carga manual", None
            )
            if config["has_ads"]:
                config["ads_connection_id"] = _connection(
                    cur, code, "ads_manual", f"Pauta {code}", config["store_id"]
                )
            if config["has_cs"]:
                config["cs_connection_id"] = _connection(
                    cur, code, "cs_sheet", "Confirmación CS", None
                )

        conn.commit()

        for code, config in COUNTRIES.items():
            _seed_country(cur, code, config, salt)
            conn.commit()
            print(f"  {code}: listo")

        for currency, rate in FX_RATES.items():
            cur.execute(
                """
                INSERT INTO core.fx_rate (rate_date, base_currency, quote_currency, rate, source)
                VALUES (%s, %s, 'USD', %s, 'demo')
                ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                SET rate = EXCLUDED.rate
                """,
                (TODAY, currency, rate),
            )
        conn.commit()

        cur.execute("SELECT core.relink_orphan_movements(%s)", (DEMO_TENANT,))
        conn.commit()


def _connection(cur, country_code: str, platform: str, name: str, store_id) -> UUID:
    """Create a connection, honouring the platform's scope.

    Manual upload is global (migration 012): the file says which country it is
    about, so pinning the connection to one would be asking for information the
    system already has. The database refuses the wrong shape either way.
    """
    cur.execute("SELECT scope FROM core.platform WHERE code = %s", (platform,))
    row = cur.fetchone()
    scope = row["scope"] if row else "country"
    connection_country = None if scope == "global" else country_code

    if connection_country is None:
        # Global connections are unique on (tenant, platform, name), so the demo
        # reuses one across every country instead of creating three.
        cur.execute(
            "SELECT id FROM core.connection "
            "WHERE tenant_id = %s AND platform_code = %s AND name = %s AND country_code IS NULL",
            (DEMO_TENANT, platform, name),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]

    cur.execute(
        """
        INSERT INTO core.connection
            (tenant_id, country_code, platform_code, store_id, name, status, last_sync_at)
        VALUES (%s, %s, %s, %s, %s, 'active', now() - interval '2 hours')
        RETURNING id
        """,
        (DEMO_TENANT, connection_country, platform, store_id, name),
    )
    return cur.fetchone()["id"]


def _dimension(cur, table: str, columns: tuple[str, ...], values: tuple, conflict: str) -> UUID:
    placeholders = ", ".join(["%s"] * len(values))
    cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO NOTHING RETURNING id",
        values,
    )
    row = cur.fetchone()
    if row:
        return row["id"]

    where = " AND ".join(f"{column} = %s" for column in conflict.split(", "))
    lookup = [values[columns.index(column)] for column in conflict.split(", ")]
    cur.execute(f"SELECT id FROM {table} WHERE {where}", lookup)
    return cur.fetchone()["id"]


def _norm(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.strip())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _seed_country(cur, code: str, config: dict, salt: str) -> None:
    currency = config["currency"]
    connection_id = config["connection_id"]

    carrier_ids = {
        name: _dimension(
            cur,
            "core.carrier",
            ("tenant_id", "country_code", "name", "name_norm"),
            (DEMO_TENANT, code, name, _norm(name)),
            "tenant_id, country_code, name_norm",
        )
        for name, *_ in config["carriers"]
    }

    supplier_ids: dict[str, UUID] = {}
    product_ids: dict[str, UUID] = {}
    for name, _share, _rate, _price, cost, supplier in config["products"]:
        if supplier not in supplier_ids:
            supplier_ids[supplier] = _dimension(
                cur,
                "core.supplier",
                ("tenant_id", "country_code", "name", "name_norm"),
                (DEMO_TENANT, code, supplier, _norm(supplier)),
                "tenant_id, name_norm",
            )
        product_ids[name] = _dimension(
            cur,
            "core.product",
            ("tenant_id", "name", "name_norm", "supplier_id", "unit_cost", "currency_code"),
            (DEMO_TENANT, name, _norm(name), supplier_ids[supplier], cost, currency),
            "tenant_id, name_norm",
        )

    geo_ids = {
        city: _dimension(
            cur,
            "core.geo",
            (
                "tenant_id", "country_code", "level1_name",
                "level1_norm", "city_name", "city_normalized",
            ),
            (DEMO_TENANT, code, level1, _norm(level1), city, _norm(city)),
            "tenant_id, country_code, level1_norm, city_normalized",
        )
        for level1, city, *_ in config["geo"]
    }

    cur.execute(
        """
        INSERT INTO raw.load_batch
            (tenant_id, connection_id, source_name, kind, content_hash, status,
             rows_total, rows_inserted, finished_at)
        VALUES (%s, %s, %s, 'shipments', %s, 'ok', 0, 0, now())
        ON CONFLICT (tenant_id, connection_id, content_hash) DO UPDATE SET status = 'ok'
        RETURNING id
        """,
        (
            DEMO_TENANT,
            connection_id,
            f"demo_{code}_historico.csv",
            hashlib.sha256(f"demo-{code}".encode()).hexdigest(),
        ),
    )
    batch_id = cur.fetchone()["id"]

    shipments: list[tuple] = []
    movements: list[tuple] = []
    cs_rows: list[tuple] = []
    ad_rows: list[tuple] = []
    counter = 0

    for offset in range(DAYS_OF_HISTORY, -1, -1):
        created = TODAY - timedelta(days=offset)
        low, high = config["daily_volume"]
        # Weekends dip; end of month picks up.
        weekday_factor = 0.6 if created.weekday() >= 5 else 1.0
        volume = int(RNG.randint(low, high) * weekday_factor)

        for _ in range(volume):
            counter += 1
            tracking = f"{code}-{created:%y%m%d}-{counter:05d}"

            carrier_name, _, carrier_rate, carrier_return = pick(config["carriers"])
            product_name, _, product_rate, price, cost, _ = pick(config["products"])
            level1, city, _, geo_rate = pick(config["geo"], weight_index=2)

            # Blend the three effects rather than letting one dominate.
            delivery_probability = (carrier_rate * 0.45) + (product_rate * 0.3) + (geo_rate * 0.25)
            age = (TODAY - created).days
            transit_days = RNG.randint(2, 14)

            status = "in_transit"
            delivered_at = None
            returned_at = None

            if age >= transit_days:
                roll = RNG.random()
                if roll < delivery_probability:
                    status = "delivered"
                    delivered_at = datetime.combine(
                        created + timedelta(days=transit_days),
                        datetime.min.time(),
                    ) + timedelta(hours=RNG.randint(9, 19))
                elif roll < delivery_probability + carrier_return:
                    status = "returned"
                    returned_at = datetime.combine(
                        created + timedelta(days=transit_days + RNG.randint(3, 9)),
                        datetime.min.time(),
                    )
                elif roll < delivery_probability + carrier_return + 0.03:
                    status = "cancelled"
                else:
                    status = "delivery_issue" if age < transit_days + 12 else "returned"
                    if status == "returned":
                        returned_at = datetime.combine(
                            created + timedelta(days=transit_days + 10), datetime.min.time()
                        )
            elif age > 2:
                status = RNG.choice(["in_transit", "in_transit", "out_for_delivery"])

            quantity = 1 if RNG.random() > 0.12 else 2
            declared = price * quantity
            collected = declared if status == "delivered" else None
            return_freight = config["return_freight"] if status == "returned" else None

            shipments.append(
                (
                    DEMO_TENANT, connection_id, code, config["store_id"], tracking,
                    customer_hash(f"{code}{counter}", salt),
                    carrier_ids[carrier_name], geo_ids[city], product_ids[product_name],
                    quantity, status, created, delivered_at, returned_at, currency,
                    declared, collected, config["freight"], return_freight,
                    cost * quantity, batch_id,
                )
            )

            if status == "delivered":
                movements.append(
                    (
                        DEMO_TENANT, connection_id, code, tracking, "cod_collected",
                        delivered_at.date(), declared, currency, f"MOV-{tracking}-R",
                        hashlib.sha256(f"{tracking}-cod".encode()).hexdigest(), batch_id,
                    )
                )
            movements.append(
                (
                    DEMO_TENANT, connection_id, code, tracking, "freight_out",
                    created, config["freight"], currency, f"MOV-{tracking}-F",
                    hashlib.sha256(f"{tracking}-freight".encode()).hexdigest(), batch_id,
                )
            )

            if config["has_cs"] and RNG.random() < 0.7:
                outcome = RNG.choices(
                    ["confirmed", "no_answer", "rejected", "pending"],
                    weights=[62, 22, 11, 5],
                )[0]
                cs_rows.append(
                    (
                        DEMO_TENANT, config["cs_connection_id"], code, tracking, created,
                        outcome, RNG.randint(1, 3),
                        RNG.choice(["WhatsApp", "Llamada", "Bot IA"]),
                        hashlib.sha256(f"{tracking}-cs".encode()).hexdigest(),
                    )
                )

        if config["has_ads"]:
            low_spend, high_spend = config["daily_ad_spend"]
            spend = Decimal(RNG.randint(low_spend, high_spend))
            ad_rows.append(
                (
                    DEMO_TENANT, config["ads_connection_id"], code, config["store_id"],
                    created, "Prospección · Advantage+", spend,
                    RNG.randint(18000, 52000), RNG.randint(400, 1400),
                    RNG.randint(10, 40), currency,
                    hashlib.sha256(f"{code}-ads-{created}".encode()).hexdigest(),
                )
            )

    cur.executemany(
        """
        INSERT INTO core.shipment
            (tenant_id, connection_id, country_code, store_id, tracking_number, customer_hash,
             carrier_id, geo_id, product_id, quantity, status_code, created_date,
             delivered_at, returned_at, currency_code, declared_value, cod_collected,
             freight_cost, return_freight_cost, product_cost, first_batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (connection_id, tracking_number) DO NOTHING
        """,
        shipments,
    )

    cur.executemany(
        """
        INSERT INTO core.movement
            (tenant_id, connection_id, country_code, tracking_number_raw, movement_type_code,
             movement_date, amount, currency_code, external_ref, dedupe_key, batch_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (connection_id, dedupe_key) DO NOTHING
        """,
        movements,
    )

    if cs_rows:
        cur.executemany(
            """
            INSERT INTO core.cs_interaction
                (tenant_id, connection_id, country_code, tracking_number_raw, interaction_date,
                 outcome, attempts, agent_label, dedupe_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, dedupe_key) DO NOTHING
            """,
            cs_rows,
        )

    if ad_rows:
        cur.executemany(
            """
            INSERT INTO core.ad_spend
                (tenant_id, connection_id, country_code, store_id, spend_date, campaign_name,
                 spend, impressions, clicks, conversions, currency_code, dedupe_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (connection_id, dedupe_key) DO NOTHING
            """,
            ad_rows,
        )

    cur.execute(
        "UPDATE raw.load_batch SET rows_total = %s, rows_inserted = %s WHERE id = %s",
        (len(shipments), len(shipments), batch_id),
    )


def summarise(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(DEMO_TENANT),))
        cur.execute(
            """
            SELECT country_code, sum(shipments) AS shipments, sum(delivered) AS delivered,
                   sum(returned) AS returned, round(sum(contribution)) AS contribution,
                   min(currency_code) AS currency
            FROM mart.v_daily_contribution GROUP BY country_code ORDER BY country_code
            """
        )
        print("\n  País   Guías  Entregadas  Devueltas   Contribución")
        print("  " + "-" * 52)
        for row in cur.fetchall():
            print(
                f"  {row['country_code']}   {row['shipments']:>6}  {row['delivered']:>10}  "
                f"{row['returned']:>9}   {row['contribution']:>12,} {row['currency']}"
            )

        cur.execute(
            "SELECT country_code, count(*) FILTER (WHERE state = 'blocked') AS blocked, "
            "count(*) AS total FROM mart.v_country_dashboard_layout GROUP BY country_code"
        )
        print()
        for row in cur.fetchall():
            print(
                f"  {row['country_code']}: {row['blocked']} de {row['total']} widgets bloqueados "
                f"(por falta de conector)"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga datos de demostración en Data Effi")
    parser.add_argument("--reset", action="store_true", help="Borra la demo anterior primero")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.reset:
            reset(conn)

        print("Cargando datos de demostración…")
        seed(conn)
        summarise(conn)

        print(
            f"\nListo. Entra en http://localhost:3000 con:\n"
            f"  Correo:     {DEMO_EMAIL}\n"
            f"  Contraseña: {DEMO_PASSWORD}\n"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
