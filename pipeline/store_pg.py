"""PostgresStore: the production backend for the ingestion engine.

Same contract as MemoryStore, same merge policy. The difference is that the
policy is executed by PostgreSQL inside a single `INSERT ... ON CONFLICT DO
UPDATE`, so two concurrent loads of overlapping data cannot interleave into a
corrupt row.

HOW THE TWO IMPLEMENTATIONS ARE KEPT IN SYNC
--------------------------------------------
The SQL is not hand-written per column. It is GENERATED from the same
`STATIC_COLUMNS` / `MONEY_COLUMNS` tuples that `merge_shipment` iterates over.
Adding a money field in one place therefore changes both behaviours at once, and
`tests/test_store_pg.py` runs the identical fixture through both stores and
compares the outcome row by row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row, tuple_row

from pipeline.ingest import BatchAlreadyExists
from pipeline.models import (
    BatchContext,
    BatchKind,
    Discrepancy,
    IngestReport,
    MovementInput,
    RowOutcome,
    ShipmentInput,
    UpsertResult,
)
from pipeline.normalize import normalize_text

logger = logging.getLogger(__name__)

# Table columns that follow COALESCE semantics: a later file may fill a gap but
# never overwrite something we already know. Mirrors models.STATIC_FIELDS, after
# dimension resolution turns names into ids.
STATIC_COLUMNS: tuple[str, ...] = (
    "external_order_id",
    "carrier_tracking_number",
    "customer_hash",
    "carrier_id",
    "geo_id",
    "product_id",
    "store_id",
    "quantity",
    "created_date",
    "currency_code",
    "dispatched_at",
    "delivered_at",
    "returned_at",
    "expected_delivery_date",
    "service_level",
)

# Money columns: newest wins, discrepancies recorded. Same list as MONEY_FIELDS.
MONEY_COLUMNS: tuple[str, ...] = (
    "declared_value",
    "cod_collected",
    "freight_cost",
    "return_freight_cost",
    "product_cost",
    "platform_fee",
    "insurance_cost",
    "collection_fee",
)

# Columns that describe the CURRENT state rather than the shipment itself.
# Refreshed on every load: a guide settled since the last file must show as
# settled, and COALESCE-on-null would never let that through.
PROGRESS_COLUMNS: tuple[str, ...] = (
    "settled_at",
    "settled_with_collection",
    "status_detail",
)

ALL_SHIPMENT_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "connection_id",
    "country_code",
    "tracking_number",
    "status_code",
    "status_raw",
    "last_status_at",
    *STATIC_COLUMNS,
    *MONEY_COLUMNS,
    *PROGRESS_COLUMNS,
    "first_batch_id",
    "last_batch_id",
)


def _build_shipment_upsert() -> str:
    """Generate the upsert statement from the column policy tuples.

    Generated rather than written out so the SQL can never drift from the Python
    merge policy. Read the produced statement with EXPLAIN if you need to debug
    it; the shape is:

        INSERT ... VALUES ...
        ON CONFLICT (connection_id, tracking_number) DO UPDATE
           SET <status advance>, <coalesce statics>, <newest money>
         WHERE <something would actually change>
        RETURNING (xmax = 0) AS inserted, id

    The WHERE on the DO UPDATE is what makes an unchanged row report SKIPPED:
    PostgreSQL performs no write and RETURNING yields no row.
    """
    insert_columns = ", ".join(ALL_SHIPMENT_COLUMNS)
    placeholders = ", ".join(f"%({column})s" for column in ALL_SHIPMENT_COLUMNS)

    assignments = [
        "status_code = core.status_advance(core.shipment.status_code, EXCLUDED.status_code)",
        # status_raw travels with the status it describes.
        "status_raw = CASE WHEN core.status_advance(core.shipment.status_code, "
        "EXCLUDED.status_code) IS DISTINCT FROM core.shipment.status_code "
        "THEN EXCLUDED.status_raw ELSE core.shipment.status_raw END",
        "last_status_at = GREATEST(core.shipment.last_status_at, EXCLUDED.last_status_at)",
        "last_batch_id = EXCLUDED.last_batch_id",
    ]
    assignments += [
        f"{column} = COALESCE(core.shipment.{column}, EXCLUDED.{column})"
        for column in STATIC_COLUMNS
    ]
    assignments += [
        f"{column} = COALESCE(EXCLUDED.{column}, core.shipment.{column})"
        for column in MONEY_COLUMNS
    ]
    assignments += [
        f"{column} = COALESCE(EXCLUDED.{column}, core.shipment.{column})"
        for column in PROGRESS_COLUMNS
    ]

    change_conditions = [
        "core.status_advance(core.shipment.status_code, EXCLUDED.status_code) "
        "IS DISTINCT FROM core.shipment.status_code",
        "(EXCLUDED.last_status_at IS NOT NULL AND (core.shipment.last_status_at IS NULL "
        "OR EXCLUDED.last_status_at > core.shipment.last_status_at))",
    ]
    change_conditions += [
        f"(core.shipment.{column} IS NULL AND EXCLUDED.{column} IS NOT NULL)"
        for column in STATIC_COLUMNS
    ]
    change_conditions += [
        f"(EXCLUDED.{column} IS NOT NULL "
        f"AND core.shipment.{column} IS DISTINCT FROM EXCLUDED.{column})"
        for column in (*MONEY_COLUMNS, *PROGRESS_COLUMNS)
    ]

    return (
        f"INSERT INTO core.shipment ({insert_columns})\n"
        f"VALUES ({placeholders})\n"
        f"ON CONFLICT (connection_id, tracking_number) DO UPDATE SET\n    "
        + ",\n    ".join(assignments)
        + "\nWHERE "
        + "\n   OR ".join(change_conditions)
        + "\nRETURNING (xmax = 0) AS inserted, id"
    )


SHIPMENT_UPSERT_SQL = _build_shipment_upsert()

# Money values that contradict a previously known value, captured before the
# upsert overwrites them.
DISCREPANCY_PROBE_SQL = """
SELECT {columns}
FROM core.shipment
WHERE connection_id = %(connection_id)s AND tracking_number = %(tracking_number)s
""".format(columns=", ".join(MONEY_COLUMNS))


class PostgresStore:
    """Ingestion backend backed by PostgreSQL.

    Owns no connection lifecycle: it is handed an open psycopg connection and
    uses SAVEPOINTs per row so a single bad row aborts itself and nothing else.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        # Every cursor below states its own row_factory. Inheriting the
        # connection's would make this class silently depend on how the caller
        # opened it - a connection created with dict_row used to fail here with
        # `KeyError: 0`, which tells you nothing about what went wrong.
        self._conn = conn
        # Per-run dimension caches. Cleared with reset_cache() between batches
        # when the same store instance is reused by a long-lived worker.
        self._carrier_cache: dict[tuple[UUID, str, str], UUID] = {}
        self._geo_cache: dict[tuple[UUID, str, str, str], UUID] = {}
        self._product_cache: dict[tuple[UUID, str], UUID] = {}
        self._supplier_cache: dict[tuple[UUID, str], UUID] = {}
        self._store_cache: dict[tuple[UUID, str, str], UUID] = {}

    def reset_cache(self) -> None:
        for cache in (
            self._carrier_cache,
            self._geo_cache,
            self._product_cache,
            self._supplier_cache,
            self._store_cache,
        ):
            cache.clear()

    # =====================================================================
    # Batch lifecycle
    # =====================================================================

    def batch_exists(self, tenant_id: UUID, connection_id: UUID, content_hash: str) -> bool:
        with self._conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                """
                SELECT 1 FROM raw.load_batch
                WHERE tenant_id = %s AND connection_id = %s AND content_hash = %s
                """,
                (tenant_id, connection_id, content_hash),
            )
            return cur.fetchone() is not None

    def register_batch(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        country_code: str,
        platform_code: str,
        source_name: str,
        kind: BatchKind,
        content_hash: str,
    ) -> BatchContext:
        """Claim this file. The UNIQUE constraint is what resolves a race.

        Two uploads of identical bytes both reach this point; exactly one INSERT
        succeeds and the loser gets BatchAlreadyExists, which the engine turns
        into an honest "already loaded" report rather than an error.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO raw.load_batch
                    (tenant_id, connection_id, source_name, kind, content_hash, status)
                VALUES (%s, %s, %s, %s, %s, 'running')
                ON CONFLICT (tenant_id, connection_id, content_hash) DO NOTHING
                RETURNING id
                """,
                (tenant_id, connection_id, source_name, kind.value, content_hash),
            )
            row = cur.fetchone()

        if row is None:
            raise BatchAlreadyExists(content_hash)

        return BatchContext(
            batch_id=row["id"],
            tenant_id=tenant_id,
            connection_id=connection_id,
            country_code=country_code,
            platform_code=platform_code,
            kind=kind,
        )

    def finish_batch(self, ctx: BatchContext, report: IngestReport) -> None:
        status = "ok" if report.rows_failed == 0 else "failed"
        error = report.errors[0].message if report.errors else None

        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE raw.load_batch SET
                    status        = %s,
                    rows_total    = %s,
                    rows_inserted = %s,
                    rows_updated  = %s,
                    rows_skipped  = %s,
                    rows_failed   = %s,
                    report        = %s,
                    error         = %s,
                    finished_at   = now()
                WHERE id = %s
                """,
                (
                    status,
                    report.rows_total,
                    report.rows_inserted,
                    report.rows_updated,
                    report.rows_skipped,
                    report.rows_failed,
                    psycopg.types.json.Json(report.to_json()),
                    error,
                    ctx.batch_id,
                ),
            )
            # A load is only useful once its orphans are attached.
            cur.execute("SELECT core.relink_orphan_movements(%s)", (ctx.tenant_id,))
            cur.execute(
                "UPDATE core.connection SET last_sync_at = now(), status = 'active' WHERE id = %s",
                (ctx.connection_id,),
            )

    # =====================================================================
    # Shipments
    # =====================================================================

    def upsert_shipment(self, ctx: BatchContext, shipment: ShipmentInput) -> UpsertResult:
        params = self._shipment_params(ctx, shipment)

        with self._conn.transaction():      # SAVEPOINT: isolates this row only
            previous = self._probe_money(ctx, shipment.tracking_number)

            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SHIPMENT_UPSERT_SQL, params)
                row = cur.fetchone()

            if row is None:
                # DO UPDATE ... WHERE matched nothing: identical data, no write.
                return UpsertResult(RowOutcome.SKIPPED, shipment.tracking_number)

            discrepancies = self._collect_discrepancies(
                ctx, shipment.tracking_number, previous, params
            )
            outcome = RowOutcome.INSERTED if row["inserted"] else RowOutcome.UPDATED

        return UpsertResult(outcome, shipment.tracking_number, discrepancies=discrepancies)

    def _shipment_params(self, ctx: BatchContext, shipment: ShipmentInput) -> dict[str, Any]:
        carrier_id = self._resolve_carrier(ctx, shipment.carrier_name)
        geo_id = self._resolve_geo(ctx, shipment.geo_level1, shipment.city_name)
        supplier_id = self._resolve_supplier(ctx, shipment.supplier_name)
        product_id = self._resolve_product(ctx, shipment.product_name, supplier_id)
        store_id = self._resolve_store(ctx, shipment.store_name)

        return {
            "tenant_id": ctx.tenant_id,
            "connection_id": ctx.connection_id,
            "country_code": ctx.country_code,
            "tracking_number": shipment.tracking_number,
            "status_code": shipment.status_code,
            "status_raw": shipment.status_raw,
            "status_detail": shipment.status_detail,
            "last_status_at": shipment.last_status_at,
            "external_order_id": shipment.external_order_id,
            "carrier_tracking_number": shipment.carrier_tracking_number,
            "customer_hash": shipment.customer_hash,
            "carrier_id": carrier_id,
            "geo_id": geo_id,
            "product_id": product_id,
            "store_id": store_id,
            "quantity": shipment.quantity,
            "created_date": shipment.created_date,
            "currency_code": shipment.currency_code,
            "dispatched_at": shipment.dispatched_at,
            "delivered_at": shipment.delivered_at,
            "returned_at": shipment.returned_at,
            "expected_delivery_date": shipment.expected_delivery_date,
            "settled_at": shipment.settled_at,
            "settled_with_collection": shipment.settled_with_collection,
            "service_level": shipment.service_level,
            "declared_value": shipment.declared_value,
            "cod_collected": shipment.cod_collected,
            "freight_cost": shipment.freight_cost,
            "return_freight_cost": shipment.return_freight_cost,
            "product_cost": shipment.product_cost,
            "platform_fee": shipment.platform_fee,
            "insurance_cost": shipment.insurance_cost,
            "collection_fee": shipment.collection_fee,
            "first_batch_id": ctx.batch_id,
            "last_batch_id": ctx.batch_id,
        }

    def _probe_money(self, ctx: BatchContext, tracking_number: str) -> dict[str, Any] | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                DISCREPANCY_PROBE_SQL,
                {"connection_id": ctx.connection_id, "tracking_number": tracking_number},
            )
            return cur.fetchone()

    def _collect_discrepancies(
        self,
        ctx: BatchContext,
        tracking_number: str,
        previous: dict[str, Any] | None,
        params: dict[str, Any],
    ) -> list[Discrepancy]:
        if previous is None:
            return []

        found: list[Discrepancy] = []
        for column in MONEY_COLUMNS:
            old_value = previous.get(column)
            new_value = params.get(column)
            if old_value is None or new_value is None:
                continue
            if Decimal(old_value) == Decimal(new_value):
                continue
            found.append(
                Discrepancy(
                    entity="shipment",
                    entity_key=tracking_number,
                    field_name=column,
                    old_value=_money_text(old_value),
                    new_value=_money_text(new_value),
                )
            )

        if found:
            with self._conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO raw.load_discrepancy
                        (tenant_id, batch_id, entity, entity_key, field_name, old_value, new_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            ctx.tenant_id,
                            ctx.batch_id,
                            d.entity,
                            d.entity_key,
                            d.field_name,
                            d.old_value,
                            d.new_value,
                        )
                        for d in found
                    ],
                )
        return found

    # =====================================================================
    # Movements
    # =====================================================================

    def upsert_movement(self, ctx: BatchContext, movement: MovementInput) -> UpsertResult:
        entity_key = movement.external_ref or movement.dedupe_key[:12]

        with self._conn.transaction():
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, amount, shipment_id FROM core.movement
                    WHERE connection_id = %s AND dedupe_key = %s
                    """,
                    (ctx.connection_id, movement.dedupe_key),
                )
                existing = cur.fetchone()

                cur.execute(
                    """
                    INSERT INTO core.movement (
                        tenant_id, connection_id, country_code, shipment_id,
                        tracking_number_raw, movement_type_code, movement_date,
                        amount, currency_code, external_ref, description,
                        dedupe_key, batch_id
                    )
                    VALUES (
                        %(tenant_id)s, %(connection_id)s, %(country_code)s,
                        -- Effi's wallet cites the CARRIER's number, not the
                        -- guide's own, so both are candidates.
                        (SELECT id FROM core.shipment
                          WHERE connection_id = %(connection_id)s
                            AND (tracking_number = %(tracking_number_raw)s
                                 OR carrier_tracking_number = %(tracking_number_raw)s)
                          LIMIT 1),
                        %(tracking_number_raw)s, %(movement_type_code)s, %(movement_date)s,
                        %(amount)s, %(currency_code)s, %(external_ref)s, %(description)s,
                        %(dedupe_key)s, %(batch_id)s
                    )
                    ON CONFLICT (connection_id, dedupe_key) DO UPDATE SET
                        amount      = EXCLUDED.amount,
                        -- An orphan that finds its shipment gets linked; a linked
                        -- movement is never un-linked by a later file.
                        shipment_id = COALESCE(core.movement.shipment_id, EXCLUDED.shipment_id),
                        batch_id    = EXCLUDED.batch_id
                    WHERE core.movement.amount IS DISTINCT FROM EXCLUDED.amount
                       OR (core.movement.shipment_id IS NULL AND EXCLUDED.shipment_id IS NOT NULL)
                    RETURNING (xmax = 0) AS inserted
                    """,
                    {
                        "tenant_id": ctx.tenant_id,
                        "connection_id": ctx.connection_id,
                        "country_code": ctx.country_code,
                        "tracking_number_raw": movement.tracking_number_raw,
                        "movement_type_code": movement.movement_type_code,
                        "movement_date": movement.movement_date,
                        "amount": movement.amount,
                        "currency_code": movement.currency_code,
                        "external_ref": movement.external_ref,
                        "description": movement.description,
                        "dedupe_key": movement.dedupe_key,
                        "batch_id": ctx.batch_id,
                    },
                )
                row = cur.fetchone()

            if row is None:
                return UpsertResult(RowOutcome.SKIPPED, entity_key)

            discrepancies: list[Discrepancy] = []
            if existing is not None and Decimal(existing["amount"]) != Decimal(movement.amount):
                discrepancies.append(
                    Discrepancy(
                        entity="movement",
                        entity_key=entity_key,
                        field_name="amount",
                        old_value=_money_text(existing["amount"]),
                        new_value=_money_text(movement.amount),
                    )
                )
                with self._conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO raw.load_discrepancy
                            (tenant_id, batch_id, entity, entity_key,
                             field_name, old_value, new_value)
                        VALUES (%s, %s, 'movement', %s, 'amount', %s, %s)
                        """,
                        (
                            ctx.tenant_id,
                            ctx.batch_id,
                            entity_key,
                            discrepancies[0].old_value,
                            discrepancies[0].new_value,
                        ),
                    )

            outcome = RowOutcome.INSERTED if row["inserted"] else RowOutcome.UPDATED

        return UpsertResult(outcome, entity_key, discrepancies=discrepancies)

    def relink_orphans(self, tenant_id: UUID | None = None) -> int:
        with self._conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT core.relink_orphan_movements(%s)", (tenant_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    # =====================================================================
    # Dimension resolution (get-or-create, cached per run)
    # =====================================================================

    def _resolve_carrier(self, ctx: BatchContext, name: str | None) -> UUID | None:
        norm = normalize_text(name)
        if not norm or not name:
            return None
        key = (ctx.tenant_id, ctx.country_code, norm)
        if key in self._carrier_cache:
            return self._carrier_cache[key]

        carrier_id = self._get_or_create(
            table="core.carrier",
            insert_columns=("tenant_id", "country_code", "name", "name_norm"),
            insert_values=(ctx.tenant_id, ctx.country_code, name, norm),
            conflict_columns=("tenant_id", "country_code", "name_norm"),
            lookup_where="tenant_id = %s AND country_code = %s AND name_norm = %s",
            lookup_values=(ctx.tenant_id, ctx.country_code, norm),
        )
        self._carrier_cache[key] = carrier_id
        return carrier_id

    def _resolve_geo(
        self, ctx: BatchContext, level1: str | None, city: str | None
    ) -> UUID | None:
        city_norm = normalize_text(city)
        if not city_norm or not city:
            return None
        level1_norm = normalize_text(level1) or ""
        key = (ctx.tenant_id, ctx.country_code, level1_norm, city_norm)
        if key in self._geo_cache:
            return self._geo_cache[key]

        geo_id = self._get_or_create(
            table="core.geo",
            insert_columns=(
                "tenant_id", "country_code", "level1_name",
                "level1_norm", "city_name", "city_normalized",
            ),
            insert_values=(
                ctx.tenant_id, ctx.country_code, level1, level1_norm, city, city_norm,
            ),
            conflict_columns=("tenant_id", "country_code", "level1_norm", "city_normalized"),
            lookup_where=(
                "tenant_id = %s AND country_code = %s "
                "AND level1_norm = %s AND city_normalized = %s"
            ),
            lookup_values=(ctx.tenant_id, ctx.country_code, level1_norm, city_norm),
        )
        self._geo_cache[key] = geo_id
        return geo_id

    def _resolve_supplier(self, ctx: BatchContext, name: str | None) -> UUID | None:
        norm = normalize_text(name)
        if not norm or not name:
            return None
        key = (ctx.tenant_id, norm)
        if key in self._supplier_cache:
            return self._supplier_cache[key]

        supplier_id = self._get_or_create(
            table="core.supplier",
            insert_columns=("tenant_id", "country_code", "name", "name_norm"),
            insert_values=(ctx.tenant_id, ctx.country_code, name, norm),
            conflict_columns=("tenant_id", "name_norm"),
            lookup_where="tenant_id = %s AND name_norm = %s",
            lookup_values=(ctx.tenant_id, norm),
        )
        self._supplier_cache[key] = supplier_id
        return supplier_id

    def _resolve_product(
        self, ctx: BatchContext, name: str | None, supplier_id: UUID | None
    ) -> UUID | None:
        """Resolve a product name, honouring core.product_alias first.

        The alias table is how "FAJA REDUCTORA X2" and "faja-reductora" end up on
        the same product without a human renaming anything.
        """
        norm = normalize_text(name)
        if not norm or not name:
            return None
        key = (ctx.tenant_id, norm)
        if key in self._product_cache:
            return self._product_cache[key]

        with self._conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT product_id FROM core.product_alias WHERE tenant_id = %s AND alias_norm = %s",
                (ctx.tenant_id, norm),
            )
            row = cur.fetchone()
            if row:
                self._product_cache[key] = row[0]
                return row[0]

        product_id = self._get_or_create(
            table="core.product",
            insert_columns=("tenant_id", "name", "name_norm", "supplier_id", "currency_code"),
            insert_values=(ctx.tenant_id, name, norm, supplier_id, None),
            conflict_columns=("tenant_id", "name_norm"),
            lookup_where="tenant_id = %s AND name_norm = %s",
            lookup_values=(ctx.tenant_id, norm),
        )
        self._product_cache[key] = product_id
        return product_id

    def _resolve_store(self, ctx: BatchContext, name: str | None) -> UUID | None:
        if not name:
            return None
        key = (ctx.tenant_id, ctx.country_code, name)
        if key in self._store_cache:
            return self._store_cache[key]

        store_id = self._get_or_create(
            table="core.store",
            insert_columns=("tenant_id", "country_code", "name"),
            insert_values=(ctx.tenant_id, ctx.country_code, name),
            conflict_columns=("tenant_id", "country_code", "name"),
            lookup_where="tenant_id = %s AND country_code = %s AND name = %s",
            lookup_values=(ctx.tenant_id, ctx.country_code, name),
        )
        self._store_cache[key] = store_id
        return store_id

    def _get_or_create(
        self,
        *,
        table: str,
        insert_columns: tuple[str, ...],
        insert_values: tuple[Any, ...],
        conflict_columns: tuple[str, ...],
        lookup_where: str,
        lookup_values: tuple[Any, ...],
    ) -> UUID:
        """INSERT ... ON CONFLICT DO NOTHING, then SELECT.

        The follow-up SELECT is what makes this safe under concurrency: if
        another connection created the row first, DO NOTHING returns no id and we
        read theirs instead of raising.
        """
        schema, table_name = table.split(".")
        statement = sql.SQL(
            "INSERT INTO {table} ({columns}) VALUES ({values}) "
            "ON CONFLICT ({conflict}) DO NOTHING RETURNING id"
        ).format(
            table=sql.Identifier(schema, table_name),
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in insert_columns),
            values=sql.SQL(", ").join(sql.Placeholder() * len(insert_values)),
            conflict=sql.SQL(", ").join(sql.Identifier(c) for c in conflict_columns),
        )

        with self._conn.transaction(), self._conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(statement, insert_values)
            row = cur.fetchone()
            if row:
                return row[0]

            cur.execute(
                # Safe: `lookup_where` is a module-private constant defined
                # above in this file, never user input. Values are still
                # bound as parameters.
                sql.SQL("SELECT id FROM {table} WHERE " + lookup_where).format(  # noqa: S608
                    table=sql.Identifier(schema, table_name)
                ),
                lookup_values,
            )
            existing = cur.fetchone()

        if existing is None:      # pragma: no cover - would mean a broken constraint
            raise RuntimeError(f"could not resolve or create a row in {table}")
        return existing[0]


def _money_text(value: Any) -> str:
    """Render a money value without trailing zeros, so "149900.00" and "149900"
    compare equal in the discrepancy trail regardless of which store produced it."""
    decimal_value = Decimal(value)
    normalized = decimal_value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return str(normalized)


def connect(dsn: str, *, tenant_id: UUID | None = None) -> psycopg.Connection:
    """Open a connection configured for Norte.

    Setting `norte.tenant_id` is what makes every mart.* view return this
    tenant's rows and nothing else. Without it the views are empty by design.
    """
    conn = psycopg.connect(dsn, autocommit=False)
    if tenant_id is not None:
        set_tenant(conn, tenant_id)
    return conn


def set_tenant(conn: psycopg.Connection, tenant_id: UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(tenant_id),))


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "MONEY_COLUMNS",
    "SHIPMENT_UPSERT_SQL",
    "STATIC_COLUMNS",
    "PostgresStore",
    "connect",
    "set_tenant",
]
