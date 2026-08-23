"""The ingestion engine.

One job: turn an uploaded file into canonical rows, exactly once, with an honest
report of what happened. Everything here is storage-agnostic - it talks to a
`Store`, which is either MemoryStore (tests, dry runs) or PostgresStore
(production, `pipeline/store_pg.py`).

Three rules hold the whole thing together:

1. IDEMPOTENCE. The SHA-256 of the file bytes is the batch key. Loading the same
   file twice inserts nothing the second time.
2. MERGE, NEVER OVERWRITE. `merge_shipment` decides what a second sighting of a
   guide is allowed to change. Status only moves forward, descriptive fields only
   get filled in, money takes the newest value and leaves a discrepancy trail.
3. NOTHING FAILS SILENTLY. Unknown statuses, ignored columns and implausible
   rows all end up in the report the user reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from pipeline.crypto import encrypt_pii, pii_available
from pipeline.mapping import (
    MOVEMENT_TYPE_SIGNS,
    REQUIRED_COLUMNS,
    STATUS_CANON,
    build_header_map,
    resolve_movement_type,
    resolve_status,
)
from pipeline.models import (
    MONEY_FIELDS,
    PROGRESS_FIELDS,
    STATIC_FIELDS,
    BatchContext,
    BatchKind,
    Discrepancy,
    IngestReport,
    MovementInput,
    RowError,
    RowOutcome,
    SanityIssue,
    ShipmentInput,
    SourceRow,
    UpsertResult,
)
from pipeline.normalize import (
    clean_text,
    content_hash,
    dedupe_key,
    hash_customer,
    normalize_currency,
    normalize_tracking,
    parse_date,
    parse_datetime,
    parse_decimal,
    parse_int,
)
from pipeline.profiles import (
    apply_transform,
    build_profile_header_map,
    detect_country,
    detect_profile,
    redact_row,
)
from pipeline.readers import iter_records, read_tabular

logger = logging.getLogger(__name__)

# A guide older than this is almost certainly a mis-parsed date, not history.
MAX_BACKDATE_DAYS = 1095
# Collecting more than this multiple of the declared value means a bad column map.
COD_OVERCOLLECT_FACTOR = Decimal("1.5")


class CountryMismatch(Exception):
    """The file says it is about a different country than the connection.

    Refused rather than imported: loading Ecuadorian guides into a Colombian
    connection would price every one of them in COP, and the mistake becomes
    invisible the moment the data is in.
    """

    def __init__(self, detected: str, expected: str, raw_value: str) -> None:
        super().__init__(
            f"El archivo es de {raw_value} ({detected}) pero la conexión es de "
            f"{expected}. Súbelo a una conexión de {detected}, o revisa el archivo."
        )
        self.detected = detected
        self.expected = expected
        self.raw_value = raw_value


class BatchAlreadyExists(Exception):
    """Another load already registered this exact file. Raised by register_batch.

    This is not an error condition: it is how two concurrent uploads of the same
    bytes resolve into a single effective load.
    """


# =============================================================================
# Store contract
# =============================================================================


class Store(Protocol):
    """What the engine needs from a persistence backend.

    PostgresStore and MemoryStore implement this identically. The e2e test runs
    the same fixture through both and asserts the same outcome.
    """

    def batch_exists(self, tenant_id: UUID, connection_id: UUID, content_hash: str) -> bool:
        ...

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
        reprocess: bool = False,
    ) -> BatchContext:
        ...

    def clear_batch_rows(self, ctx: BatchContext) -> int:
        """Drop what a previous run of this same batch wrote. Returns the count."""
        ...

    def upsert_shipment(self, ctx: BatchContext, shipment: ShipmentInput) -> UpsertResult:
        ...

    def upsert_movement(self, ctx: BatchContext, movement: MovementInput) -> UpsertResult:
        ...

    def finish_batch(self, ctx: BatchContext, report: IngestReport) -> None:
        ...

    def save_source_rows(self, ctx: BatchContext, rows: list[SourceRow]) -> int:
        """Archive the original rows, every column of them.

        A column that was discarded is a metric that cannot be built later, so
        the whole row is kept. Personal identifiers arrive here already hashed -
        see profiles.redact_row.
        """
        ...


# =============================================================================
# Merge policy - the single source of truth for "what may a later file change?"
#
# PostgresStore reproduces this exact policy in its ON CONFLICT DO UPDATE clause.
# If you change a rule here, change it there, and the e2e test will tell you if
# the two ever drift apart.
# =============================================================================


@dataclass(slots=True)
class ShipmentRecord:
    """A stored shipment, as the engine sees it."""

    tracking_number: str
    connection_id: UUID
    tenant_id: UUID
    country_code: str
    created_date: date
    currency_code: str
    status_code: str
    status_raw: str | None = None
    status_detail: str | None = None
    carrier_tracking_number: str | None = None
    external_order_id: str | None = None
    customer_hash: str | None = None
    customer_name_enc: bytes | None = None
    customer_phone_enc: bytes | None = None
    customer_document_enc: bytes | None = None
    customer_address_enc: bytes | None = None
    customer_city_name: str | None = None
    carrier_name: str | None = None
    geo_level1: str | None = None
    city_name: str | None = None
    product_name: str | None = None
    supplier_name: str | None = None
    store_name: str | None = None
    quantity: int = 1
    created_at_source: datetime | None = None
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    returned_at: datetime | None = None
    last_status_at: datetime | None = None
    expected_delivery_date: date | None = None
    settled_at: datetime | None = None
    settled_with_collection: bool | None = None
    settled_any_at: datetime | None = None
    settled_return_at: datetime | None = None
    settled_return: bool | None = None
    service_level: str | None = None
    weight_kg: Decimal | None = None
    discount_pct: Decimal | None = None
    dispatch_batch_ref: str | None = None
    dispatched_batch_at: datetime | None = None
    distributor_name: str | None = None
    declared_value: Decimal | None = None
    cod_collected: Decimal | None = None
    freight_cost: Decimal | None = None
    return_freight_cost: Decimal | None = None
    product_cost: Decimal | None = None
    platform_fee: Decimal | None = None
    insurance_cost: Decimal | None = None
    collection_fee: Decimal | None = None
    freight_base: Decimal | None = None
    sale_total: Decimal | None = None
    distributor_sale_total: Decimal | None = None
    distributor_cost_total: Decimal | None = None
    supplier_sale_total: Decimal | None = None
    first_batch_id: UUID | None = None
    last_batch_id: UUID | None = None


@dataclass(slots=True)
class MergeOutcome:
    updates: dict[str, Any] = field(default_factory=dict)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    status_advanced: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.updates)


def merge_shipment(existing: ShipmentRecord, incoming: ShipmentInput) -> MergeOutcome:
    """Decide what a repeat sighting of a guide is allowed to change.

    STATUS advances only. A terminal status (delivered, returned, cancelled,
    lost) is frozen: a stale file that still says "en tránsito" must never undo a
    delivery. Non-terminal statuses move forward by `sort_order` only.

    STATIC fields fill gaps. If we already know the city, a later file does not
    get to rename it - but if we did not know it, we take it.

    MONEY takes the newest value, and records a discrepancy whenever it
    contradicts a value we already had. The dashboard shows the latest number;
    the discrepancy trail is what lets a human audit the change.
    """
    outcome = MergeOutcome()

    # --- status ---------------------------------------------------------
    current = STATUS_CANON[existing.status_code]
    proposed = STATUS_CANON[incoming.status_code]

    if not current.is_terminal and proposed.sort_order > current.sort_order:
        outcome.updates["status_code"] = incoming.status_code
        outcome.updates["status_raw"] = incoming.status_raw
        outcome.status_advanced = True

    # last_status_at always tracks the most recent sighting.
    if incoming.last_status_at is not None and (
        existing.last_status_at is None or incoming.last_status_at > existing.last_status_at
    ):
        outcome.updates["last_status_at"] = incoming.last_status_at

    # --- static fields: fill gaps only (COALESCE semantics) -------------
    for field_name in STATIC_FIELDS:
        new_value = getattr(incoming, field_name, None)
        if new_value is None:
            continue
        current_value = getattr(existing, field_name, None)
        if current_value is None:
            outcome.updates[field_name] = new_value

    # --- progress fields: newest wins, no discrepancy ---------------------
    # Settlement and the carrier's own status wording are facts about NOW, not
    # descriptions of the shipment. A guide that was unsettled yesterday and is
    # settled today must show as settled - filling a gap is not enough.
    for field_name in PROGRESS_FIELDS:
        new_value = getattr(incoming, field_name, None)
        if new_value is None:
            continue
        if getattr(existing, field_name, None) != new_value:
            outcome.updates[field_name] = new_value

    # --- money: newest wins, discrepancies recorded ---------------------
    for field_name in MONEY_FIELDS:
        new_value = getattr(incoming, field_name, None)
        if new_value is None:
            continue
        current_value = getattr(existing, field_name, None)
        if current_value is not None and Decimal(current_value) != Decimal(new_value):
            outcome.discrepancies.append(
                Discrepancy(
                    entity="shipment",
                    entity_key=existing.tracking_number,
                    field_name=field_name,
                    old_value=str(current_value),
                    new_value=str(new_value),
                )
            )
            outcome.updates[field_name] = new_value
        elif current_value is None:
            outcome.updates[field_name] = new_value

    return outcome


def apply_merge(existing: ShipmentRecord, outcome: MergeOutcome) -> ShipmentRecord:
    """Return a new record with the merge applied. Never mutates in place."""
    return replace(existing, **outcome.updates) if outcome.updates else existing


# =============================================================================
# MemoryStore - the reference implementation
# =============================================================================


@dataclass(slots=True)
class MovementRecord:
    dedupe_key: str
    connection_id: UUID
    tenant_id: UUID
    country_code: str
    movement_type_code: str
    movement_date: date
    amount: Decimal
    currency_code: str
    tracking_number_raw: str | None = None
    external_ref: str | None = None
    description: str | None = None
    shipment_id: str | None = None       # tracking number, or None while orphaned
    batch_id: UUID | None = None


class MemoryStore:
    """In-memory store. The behavioural reference PostgresStore must match."""

    def __init__(self) -> None:
        self.batches: dict[tuple[UUID, UUID, str], BatchContext] = {}
        self.batch_reports: dict[UUID, IngestReport] = {}
        self.shipments: dict[tuple[UUID, str], ShipmentRecord] = {}
        self.movements: dict[tuple[UUID, str], MovementRecord] = {}
        self.discrepancies: list[tuple[UUID, Discrepancy]] = []
        self.source_rows: dict[UUID, list[SourceRow]] = {}

    # -- batch lifecycle ------------------------------------------------
    def batch_exists(self, tenant_id: UUID, connection_id: UUID, content_hash: str) -> bool:
        return (tenant_id, connection_id, content_hash) in self.batches

    def clear_batch_rows(self, ctx) -> int:
        """In-memory twin of the Postgres store's reprocess cleanup."""
        before = len(self.movements)
        # `movements` is a dict keyed by (tenant, key). Iterating it directly
        # yields the KEYS, whose `batch_id` is always absent - so the previous
        # comprehension kept every row and turned the dict into a list.
        self.movements = {
            key: record
            for key, record in self.movements.items()
            if getattr(record, "batch_id", None) != ctx.batch_id
        }
        return before - len(self.movements)

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
        reprocess: bool = False,
    ) -> BatchContext:
        key = (tenant_id, connection_id, content_hash)
        if key in self.batches:
            if not reprocess:
                raise BatchAlreadyExists(content_hash)
            # Same behaviour as Postgres: reopen the batch that exists rather
            # than minting a second one, so its previous rows stay findable.
            return self.batches[key]
        ctx = BatchContext(
            batch_id=uuid4(),
            tenant_id=tenant_id,
            connection_id=connection_id,
            country_code=country_code,
            platform_code=platform_code,
            kind=kind,
        )
        self.batches[key] = ctx
        return ctx

    def finish_batch(self, ctx: BatchContext, report: IngestReport) -> None:
        self.batch_reports[ctx.batch_id] = report

    def save_source_rows(self, ctx: BatchContext, rows: list[SourceRow]) -> int:
        self.source_rows.setdefault(ctx.batch_id, []).extend(rows)
        return len(rows)

    # -- rows -----------------------------------------------------------
    def upsert_shipment(self, ctx: BatchContext, shipment: ShipmentInput) -> UpsertResult:
        key = (ctx.connection_id, shipment.tracking_number)
        existing = self.shipments.get(key)

        if existing is None:
            self.shipments[key] = ShipmentRecord(
                tracking_number=shipment.tracking_number,
                connection_id=ctx.connection_id,
                tenant_id=ctx.tenant_id,
                country_code=ctx.country_code,
                created_date=shipment.created_date,
                currency_code=shipment.currency_code,
                status_code=shipment.status_code,
                status_raw=shipment.status_raw,
                status_detail=shipment.status_detail,
                carrier_tracking_number=shipment.carrier_tracking_number,
                external_order_id=shipment.external_order_id,
                customer_hash=shipment.customer_hash,
                customer_name_enc=shipment.customer_name_enc,
                customer_phone_enc=shipment.customer_phone_enc,
                customer_document_enc=shipment.customer_document_enc,
                customer_address_enc=shipment.customer_address_enc,
                customer_city_name=shipment.customer_city_name,
                carrier_name=shipment.carrier_name,
                geo_level1=shipment.geo_level1,
                city_name=shipment.city_name,
                product_name=shipment.product_name,
                supplier_name=shipment.supplier_name,
                store_name=shipment.store_name,
                quantity=shipment.quantity,
                created_at_source=shipment.created_at_source,
                dispatched_at=shipment.dispatched_at,
                delivered_at=shipment.delivered_at,
                returned_at=shipment.returned_at,
                last_status_at=shipment.last_status_at,
                expected_delivery_date=shipment.expected_delivery_date,
                settled_at=shipment.settled_at,
                settled_with_collection=shipment.settled_with_collection,
                settled_any_at=shipment.settled_any_at,
                settled_return_at=shipment.settled_return_at,
                settled_return=shipment.settled_return,
                service_level=shipment.service_level,
                weight_kg=shipment.weight_kg,
                discount_pct=shipment.discount_pct,
                dispatch_batch_ref=shipment.dispatch_batch_ref,
                dispatched_batch_at=shipment.dispatched_batch_at,
                distributor_name=shipment.distributor_name,
                declared_value=shipment.declared_value,
                cod_collected=shipment.cod_collected,
                freight_cost=shipment.freight_cost,
                return_freight_cost=shipment.return_freight_cost,
                product_cost=shipment.product_cost,
                platform_fee=shipment.platform_fee,
                insurance_cost=shipment.insurance_cost,
                collection_fee=shipment.collection_fee,
                freight_base=shipment.freight_base,
                sale_total=shipment.sale_total,
                distributor_sale_total=shipment.distributor_sale_total,
                distributor_cost_total=shipment.distributor_cost_total,
                supplier_sale_total=shipment.supplier_sale_total,
                first_batch_id=ctx.batch_id,
                last_batch_id=ctx.batch_id,
            )
            self._relink_orphans(
                ctx, shipment.tracking_number, shipment.carrier_tracking_number
            )
            return UpsertResult(RowOutcome.INSERTED, shipment.tracking_number)

        outcome = merge_shipment(existing, shipment)
        for discrepancy in outcome.discrepancies:
            self.discrepancies.append((ctx.batch_id, discrepancy))

        if not outcome.changed:
            return UpsertResult(RowOutcome.SKIPPED, shipment.tracking_number,
                                discrepancies=outcome.discrepancies)

        outcome.updates["last_batch_id"] = ctx.batch_id
        self.shipments[key] = apply_merge(existing, outcome)
        return UpsertResult(RowOutcome.UPDATED, shipment.tracking_number,
                            discrepancies=outcome.discrepancies)

    def upsert_movement(self, ctx: BatchContext, movement: MovementInput) -> UpsertResult:
        key = (ctx.connection_id, movement.dedupe_key)
        entity_key = movement.external_ref or movement.dedupe_key[:12]
        existing = self.movements.get(key)

        shipment_id: str | None = None
        if movement.tracking_number_raw:
            candidate = (ctx.connection_id, movement.tracking_number_raw)
            if candidate in self.shipments:
                shipment_id = movement.tracking_number_raw
            else:
                # The number cited may be the carrier's, not the guide's own.
                for (connection_id, tracking), record in self.shipments.items():
                    if (
                        connection_id == ctx.connection_id
                        and record.carrier_tracking_number == movement.tracking_number_raw
                    ):
                        shipment_id = tracking
                        break

        if existing is None:
            self.movements[key] = MovementRecord(
                dedupe_key=movement.dedupe_key,
                connection_id=ctx.connection_id,
                tenant_id=ctx.tenant_id,
                country_code=ctx.country_code,
                movement_type_code=movement.movement_type_code,
                movement_date=movement.movement_date,
                amount=movement.amount,
                currency_code=movement.currency_code,
                tracking_number_raw=movement.tracking_number_raw,
                external_ref=movement.external_ref,
                description=movement.description,
                shipment_id=shipment_id,
                batch_id=ctx.batch_id,
            )
            return UpsertResult(RowOutcome.INSERTED, entity_key)

        discrepancies: list[Discrepancy] = []
        changed = False
        if existing.amount != movement.amount:
            discrepancies.append(
                Discrepancy(
                    entity="movement",
                    entity_key=entity_key,
                    field_name="amount",
                    old_value=str(existing.amount),
                    new_value=str(movement.amount),
                )
            )
            existing.amount = movement.amount
            changed = True
        if shipment_id and existing.shipment_id is None:
            existing.shipment_id = shipment_id
            changed = True

        for discrepancy in discrepancies:
            self.discrepancies.append((ctx.batch_id, discrepancy))

        outcome = RowOutcome.UPDATED if changed else RowOutcome.SKIPPED
        return UpsertResult(outcome, entity_key, discrepancies=discrepancies)

    def relink_orphans(self, tenant_id: UUID | None = None) -> int:
        """Attach movements that arrived before their shipment did."""
        by_carrier_number = {
            (connection_id, record.carrier_tracking_number): tracking
            for (connection_id, tracking), record in self.shipments.items()
            if record.carrier_tracking_number
        }

        linked = 0
        for (connection_id, _), movement in self.movements.items():
            if movement.shipment_id is not None or not movement.tracking_number_raw:
                continue
            if tenant_id is not None and movement.tenant_id != tenant_id:
                continue

            if (connection_id, movement.tracking_number_raw) in self.shipments:
                movement.shipment_id = movement.tracking_number_raw
                linked += 1
            elif (connection_id, movement.tracking_number_raw) in by_carrier_number:
                movement.shipment_id = by_carrier_number[
                    (connection_id, movement.tracking_number_raw)
                ]
                linked += 1
        return linked

    def _relink_orphans(
        self, ctx: BatchContext, tracking_number: str, carrier_tracking: str | None = None
    ) -> None:
        """Attach waiting movements to a guide that just arrived.

        Matches on the carrier's number as well: Effi's wallet report cites only
        that one, so without it every real movement stays an orphan.
        """
        keys = {tracking_number}
        if carrier_tracking:
            keys.add(carrier_tracking)

        for (connection_id, _), movement in self.movements.items():
            if (
                connection_id == ctx.connection_id
                and movement.shipment_id is None
                and movement.tracking_number_raw in keys
            ):
                movement.shipment_id = tracking_number


# =============================================================================
# Sanity checks
# =============================================================================


def check_shipment(shipment: ShipmentInput, today: date) -> list[SanityIssue]:
    """Business plausibility. Severity 'error' rejects the row, 'warning' keeps it."""
    issues: list[SanityIssue] = []
    key = shipment.tracking_number
    row = shipment.source_row_number

    if shipment.created_date > today:
        issues.append(SanityIssue(row, key, "future_created_date",
                                  f"Fecha de creación en el futuro: {shipment.created_date}",
                                  "error"))
    elif (today - shipment.created_date).days > MAX_BACKDATE_DAYS:
        issues.append(SanityIssue(row, key, "very_old_created_date",
                                  f"Fecha de creación muy antigua: {shipment.created_date}. "
                                  "Suele indicar una columna de fecha mal interpretada."))

    if shipment.delivered_at and shipment.delivered_at.date() < shipment.created_date:
        issues.append(SanityIssue(row, key, "delivered_before_created",
                                  "La fecha de entrega es anterior a la de creación"))

    status = STATUS_CANON[shipment.status_code]
    if status.is_delivered and shipment.delivered_at is None:
        issues.append(SanityIssue(row, key, "delivered_without_date",
                                  "Guía entregada sin fecha de entrega: no entrará en la "
                                  "curva de maduración"))

    if shipment.quantity <= 0:
        issues.append(SanityIssue(row, key, "invalid_quantity",
                                  f"Cantidad inválida ({shipment.quantity}), se asume 1"))

    if shipment.declared_value is not None and shipment.declared_value < 0:
        issues.append(SanityIssue(row, key, "negative_declared_value",
                                  "Valor declarado negativo", "error"))

    if status.is_delivered and (shipment.declared_value or 0) <= 0:
        issues.append(SanityIssue(row, key, "delivered_without_value",
                                  "Guía entregada con valor cero: revisa la columna de valor"))

    if (
        shipment.cod_collected is not None
        and shipment.declared_value
        and shipment.declared_value > 0
        and shipment.cod_collected > shipment.declared_value * COD_OVERCOLLECT_FACTOR
    ):
        issues.append(SanityIssue(row, key, "overcollected",
                                  f"Recaudo ({shipment.cod_collected}) muy por encima del "
                                  f"valor declarado ({shipment.declared_value})"))

    return issues


def check_movement(movement: MovementInput, today: date) -> list[SanityIssue]:
    issues: list[SanityIssue] = []
    key = movement.external_ref or movement.dedupe_key[:12]
    row = movement.source_row_number

    if movement.amount == 0:
        issues.append(SanityIssue(row, key, "zero_amount", "Movimiento con valor cero"))
    if movement.movement_date > today:
        issues.append(SanityIssue(row, key, "future_movement_date",
                                  f"Fecha de movimiento en el futuro: {movement.movement_date}",
                                  "error"))
    if not movement.tracking_number_raw:
        issues.append(SanityIssue(row, key, "movement_without_guide",
                                  "Movimiento sin número de guía: no podrá ligarse a un envío"))
    return issues


# =============================================================================
# Contact data
#
# Two representations of the same four columns, produced side by side because
# they answer different questions and neither can answer the other's:
#
#   customer_hash    "is this the same person?"  -> grouping, metrics, joins
#   customer_*_enc   "who do I call back?"       -> the orders table, the card
#
# The hash is computed from the phone whether or not encryption is available;
# it needs no key. The ciphertext needs one, and when there is none the load
# still runs - it simply carries no contact data. That is a degradation the
# batch report states out loud rather than a failure, because refusing to
# ingest would cost the operator every metric in the file over a setting they
# can fix afterwards.
# =============================================================================

# Canonical field the profile produced -> the column its ciphertext lands in.
CONTACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("customer_name", "customer_name_enc"),
    ("customer_identifier", "customer_phone_enc"),
    ("customer_document", "customer_document_enc"),
    ("customer_address", "customer_address_enc"),
)

PII_KEY_MISSING_WARNING = (
    "Sin PII_ENCRYPTION_KEY: se guardó el hash del cliente pero no los datos de "
    "contacto. Los pedidos se cargaron completos; la tabla de órdenes mostrará "
    "el cliente como referencia (#A1B2C3) hasta que configures la llave."
)


def _encrypt_contact(mapped: dict[str, Any], *, enabled: bool) -> dict[str, bytes | None]:
    """Ciphertext for the four contact columns, or four Nones without a key.

    Never returns, logs or raises with a plaintext value in it: a traceback is
    the last place a customer's phone number should turn up.
    """
    if not enabled:
        return {column: None for _, column in CONTACT_FIELDS}
    return {
        column: encrypt_pii(clean_text(mapped.get(source)))
        for source, column in CONTACT_FIELDS
    }


# =============================================================================
# Engine
# =============================================================================


class IngestEngine:
    """Runs one file through parse -> validate -> merge -> report."""

    def __init__(
        self,
        store: Store,
        *,
        pii_salt: str,
        today: date | None = None,
        archive_rows: bool = True,
    ) -> None:
        if not pii_salt:
            raise ValueError("pii_salt is required: customer identifiers are never stored raw")
        self._store = store
        self._pii_salt = pii_salt
        self._today = today or datetime.now(UTC).date()
        # Asked once per engine, not once per row: the answer cannot change
        # mid-file, and a missing key would otherwise raise and be swallowed
        # 1,649 times over. Engines are built per job (worker/jobs.py), so a key
        # added to the environment takes effect on the next upload.
        self._encrypt_contact = pii_available()
        # Keep every original column. Costs storage, and buys the ability to
        # answer a question about a column nobody mapped - without asking the
        # user to upload the file again.
        self._archive_rows = archive_rows

    def ingest(
        self,
        *,
        payload: bytes,
        source_name: str,
        kind: BatchKind,
        tenant_id: UUID,
        connection_id: UUID,
        country_code: str,
        platform_code: str,
        default_currency: str,
        reprocess: bool = False,
    ) -> IngestReport:
        digest = content_hash(payload)
        report = IngestReport(
            batch_id=None,
            content_hash=digest,
            source_name=source_name,
            kind=kind,
            started_at=datetime.now(UTC),
        )

        # Refusing the same bytes twice is what keeps an accidental double
        # upload from doubling somebody's revenue. But the engine itself
        # changes: encryption arrived, a movement date turned out to be
        # inverted. Then the SAME file legitimately yields different rows, and
        # the operator has no way to say so - the guard that protects them
        # becomes the thing standing between them and correct data.
        #
        # `reprocess` is that way. The merge rules do the rest: status only
        # advances, money keeps the newest value and records a discrepancy,
        # static fields fill gaps. Running a file through twice cannot
        # double-count, which is why this is safe to offer.
        if not reprocess and self._store.batch_exists(tenant_id, connection_id, digest):
            report.already_loaded = True
            report.finished_at = datetime.now(UTC)
            logger.info("batch already loaded, skipping: %s (%s)", source_name, digest[:12])
            return report

        try:
            ctx = self._store.register_batch(
                tenant_id=tenant_id,
                connection_id=connection_id,
                country_code=country_code,
                platform_code=platform_code,
                source_name=source_name,
                kind=kind,
                content_hash=digest,
                reprocess=reprocess,
            )
        except BatchAlreadyExists:
            # Lost a race against a concurrent upload of the same bytes. That is
            # the correct outcome, not a failure.
            report.already_loaded = True
            report.finished_at = datetime.now(UTC)
            return report

        report.batch_id = ctx.batch_id

        # Reprocessing reopens the same batch, so whatever the previous run
        # wrote is still attached to it. Rows keyed by a stable source id would
        # simply match again, but a source without ids has no such key - and
        # then re-running would append a second copy of every row. Clearing the
        # batch first makes reprocessing mean "replace", which is what the
        # operator asked for.
        if reprocess:
            removed = self._store.clear_batch_rows(ctx)
            if removed:
                logger.info("reprocess cleared %s previous rows of batch %s",
                            removed, ctx.batch_id)

        headers, rows = read_tabular(payload, source_name)

        # A recognised report (Effi's exports, for now) is mapped column by
        # column, by exact name. Anything else falls back to alias matching,
        # which is right for a spreadsheet a human assembled by hand.
        profile = detect_profile(headers, kind)
        if profile is not None:
            header_map, unmapped = build_profile_header_map(headers, profile)
            logger.info("recognised source profile: %s", profile.code)
        else:
            header_map, unmapped = build_header_map(headers, kind)

        report.unmapped_columns = unmapped
        report.profile_code = profile.code if profile else None
        report.profile_label = profile.label if profile else None

        # The file carries contact columns and there is no key to encrypt them
        # with. Said once, about the batch, and only for a file that actually
        # has something to encrypt - warning about a spreadsheet with no
        # customer column would be noise.
        if not self._encrypt_contact and any(
            source in header_map.values() for source, _ in CONTACT_FIELDS
        ):
            report.warnings.append(PII_KEY_MISSING_WARNING)
            logger.warning(
                "PII_ENCRYPTION_KEY is not configured: %s loaded with customer "
                "hashes only, contact columns left NULL", source_name,
            )

        # The file usually says what country it is about. Believe it, and refuse
        # to import it into a connection for somewhere else.
        if profile is not None:
            detected, raw_value = detect_country(headers, rows, profile)
            report.detected_country_code = detected
            report.detected_country_raw = raw_value

            if detected and detected != country_code.upper():
                report.finished_at = datetime.now(UTC)
                mismatch = CountryMismatch(detected, country_code.upper(), raw_value or detected)
                report.errors.append(RowError(0, str(mismatch)))
                report.rows_total = len(rows)
                report.rows_failed = len(rows)
                self._store.finish_batch(ctx, report)
                logger.warning("country mismatch: file=%s connection=%s", detected, country_code)
                return report

        required = (
            ("tracking_number",)
            if profile is not None and profile.kind is BatchKind.SHIPMENTS
            else REQUIRED_COLUMNS[kind]
        )
        mapped_fields = set(header_map.values())
        if profile is not None and profile.kind is BatchKind.SHIPMENTS:
            # The profile derives tracking_number from two possible columns.
            mapped_fields |= {"tracking_number"} if (
                "carrier_tracking_number" in mapped_fields
                or "external_order_id" in mapped_fields
            ) else set()

        missing = [column for column in required if column not in mapped_fields]
        if missing:
            report.finished_at = datetime.now(UTC)
            report.errors.append(
                RowError(0, f"Faltan columnas obligatorias: {', '.join(missing)}. "
                            f"Encabezados encontrados: {', '.join(headers)}")
            )
            report.rows_failed = len(rows)
            report.rows_total = len(rows)
            self._store.finish_batch(ctx, report)
            return report

        handler = self._handle_shipment_row if kind is BatchKind.SHIPMENTS else self._handle_movement_row

        pii_headers = profile.pii_columns_norm if profile else frozenset()
        archive: list[SourceRow] = []

        for row_number, mapped, raw in iter_records(headers, rows, header_map):
            report.rows_total += 1
            fields: dict[str, Any] = mapped
            try:
                fields = apply_transform(profile, mapped) if profile else mapped
                handler(ctx, report, row_number, fields, default_currency)
            except Exception as exc:
                report.rows_failed += 1
                report.errors.append(RowError(row_number, str(exc), raw))
                logger.warning("row %s failed: %s", row_number, exc)

            # Archive the original row whatever happened to it. A row that
            # failed to parse is precisely the one somebody will want to read.
            if self._archive_rows:
                row_payload, redacted = redact_row(raw, pii_headers, self._pii_salt)
                archive.append(
                    SourceRow(
                        row_number=row_number,
                        entity_key=_entity_key_of(fields),
                        payload=row_payload,
                        redacted_fields=redacted,
                    )
                )

        if archive:
            report.rows_stored = self._store.save_source_rows(ctx, archive)

        report.finished_at = datetime.now(UTC)
        self._store.finish_batch(ctx, report)
        return report

    # -- row handlers ---------------------------------------------------
    def _handle_shipment_row(
        self,
        ctx: BatchContext,
        report: IngestReport,
        row_number: int,
        mapped: dict[str, Any],
        default_currency: str,
    ) -> None:
        tracking = normalize_tracking(mapped.get("tracking_number"))
        if not tracking:
            report.rows_failed += 1
            report.errors.append(RowError(row_number, "Fila sin número de guía"))
            return

        created = parse_date(mapped.get("created_date"))
        if created is None:
            report.rows_failed += 1
            report.errors.append(
                RowError(row_number, f"Guía {tracking}: fecha de creación ilegible "
                                     f"({mapped.get('created_date')!r})")
            )
            return

        status_code, recognized = resolve_status(mapped.get("status_raw"))
        if not recognized and mapped.get("status_raw"):
            report.sanity_issues.append(
                SanityIssue(row_number, tracking, "unknown_status",
                            f"Estado no reconocido: {mapped.get('status_raw')!r}. "
                            f"Se registró como '{status_code}'.")
            )

        quantity = parse_int(mapped.get("quantity"), default=1) or 1
        contact = _encrypt_contact(mapped, enabled=self._encrypt_contact)

        shipment = ShipmentInput(
            tracking_number=tracking,
            created_date=created,
            currency_code=normalize_currency(mapped.get("currency_code"), default_currency),
            status_code=status_code,
            status_raw=clean_text(mapped.get("status_raw")),
            status_detail=clean_text(mapped.get("status_detail")),
            carrier_tracking_number=normalize_tracking(
                mapped.get("carrier_tracking_number")
            ),
            external_order_id=clean_text(mapped.get("external_order_id")),
            customer_hash=hash_customer(mapped.get("customer_identifier"), self._pii_salt),
            **contact,
            # The city as the guide wrote it, kept next to the shipment so the
            # orders table can label a row even when the geo dimension could not
            # resolve the spelling into a known city.
            customer_city_name=clean_text(mapped.get("city_name")),
            carrier_name=clean_text(mapped.get("carrier_name")),
            geo_level1=clean_text(mapped.get("geo_level1")),
            city_name=clean_text(mapped.get("city_name")),
            product_name=clean_text(mapped.get("product_name")),
            supplier_name=clean_text(mapped.get("supplier_name")),
            store_name=clean_text(mapped.get("store_name")),
            quantity=max(quantity, 1),
            created_at_source=parse_datetime(mapped.get("created_at_source")),
            dispatched_at=parse_datetime(mapped.get("dispatched_at")),
            delivered_at=parse_datetime(mapped.get("delivered_at")),
            returned_at=parse_datetime(mapped.get("returned_at")),
            last_status_at=parse_datetime(mapped.get("last_status_at")),
            expected_delivery_date=parse_date(mapped.get("expected_delivery_date")),
            settled_at=parse_datetime(mapped.get("settled_at")),
            settled_with_collection=mapped.get("settled_with_collection")
            if isinstance(mapped.get("settled_with_collection"), bool)
            else None,
            settled_any_at=parse_datetime(mapped.get("settled_any_at")),
            settled_return_at=parse_datetime(mapped.get("settled_return_at")),
            settled_return=mapped.get("settled_return")
            if isinstance(mapped.get("settled_return"), bool)
            else None,
            service_level=clean_text(mapped.get("service_level")),
            weight_kg=parse_decimal(mapped.get("weight_kg")),
            discount_pct=parse_decimal(mapped.get("discount_pct")),
            dispatch_batch_ref=clean_text(mapped.get("dispatch_batch_ref")),
            dispatched_batch_at=parse_datetime(mapped.get("dispatched_batch_at")),
            distributor_name=clean_text(mapped.get("distributor_name")),
            declared_value=_abs_or_none(parse_decimal(mapped.get("declared_value"))),
            cod_collected=_abs_or_none(parse_decimal(mapped.get("cod_collected"))),
            freight_cost=_abs_or_none(parse_decimal(mapped.get("freight_cost"))),
            return_freight_cost=_abs_or_none(parse_decimal(mapped.get("return_freight_cost"))),
            product_cost=_abs_or_none(parse_decimal(mapped.get("product_cost"))),
            platform_fee=_abs_or_none(parse_decimal(mapped.get("platform_fee"))),
            insurance_cost=_abs_or_none(parse_decimal(mapped.get("insurance_cost"))),
            collection_fee=_abs_or_none(parse_decimal(mapped.get("collection_fee"))),
            freight_base=_abs_or_none(parse_decimal(mapped.get("freight_base"))),
            sale_total=_abs_or_none(parse_decimal(mapped.get("sale_total"))),
            distributor_sale_total=_abs_or_none(
                parse_decimal(mapped.get("distributor_sale_total"))
            ),
            distributor_cost_total=_abs_or_none(
                parse_decimal(mapped.get("distributor_cost_total"))
            ),
            supplier_sale_total=_abs_or_none(parse_decimal(mapped.get("supplier_sale_total"))),
            source_row_number=row_number,
        )

        # A guide holding several products cannot be represented by a model with
        # one product per shipment. Say so instead of storing the first one and
        # letting the product report quietly under-count.
        extra_products = mapped.get("_extra_products") or 0
        if extra_products:
            report.sanity_issues.append(
                SanityIssue(
                    row_number, tracking, "multi_product_guide",
                    f"La guía lleva {extra_products + 1} productos distintos; se registró "
                    f"solo '{shipment.product_name}'. El resto no entra en el reporte "
                    f"por producto.",
                )
            )

        issues = check_shipment(shipment, self._today)
        report.sanity_issues.extend(issues)
        if any(issue.severity == "error" for issue in issues):
            report.rows_failed += 1
            blocking = next(i for i in issues if i.severity == "error")
            report.errors.append(RowError(row_number, f"Guía {tracking}: {blocking.message}"))
            return

        result = self._store.upsert_shipment(ctx, shipment)
        _tally(report, result)

    def _handle_movement_row(
        self,
        ctx: BatchContext,
        report: IngestReport,
        row_number: int,
        mapped: dict[str, Any],
        default_currency: str,
    ) -> None:
        amount = parse_decimal(mapped.get("amount"))
        if amount is None:
            report.rows_failed += 1
            report.errors.append(RowError(row_number, "Movimiento sin valor legible"))
            return

        movement_date = parse_date(mapped.get("movement_date")) or self._today

        # A recognised profile already resolved the type against that platform's
        # own vocabulary; the generic alias table is the fallback.
        if "_movement_type_code" in mapped:
            type_code = mapped["_movement_type_code"]
            recognized = bool(mapped.get("_movement_type_recognized"))
        else:
            type_code, recognized = resolve_movement_type(mapped.get("movement_type_raw"))

        if type_code is None:
            # Infer from the sign when the concept column is missing or unknown.
            type_code = "cod_collected" if amount > 0 else "freight_out"
            report.sanity_issues.append(
                SanityIssue(row_number, str(mapped.get("external_ref") or row_number),
                            "unknown_movement_type",
                            f"Tipo de movimiento no reconocido "
                            f"({mapped.get('movement_type_raw')!r}); se asumió '{type_code}' "
                            f"por el signo del valor.")
            )
        elif not recognized:
            report.sanity_issues.append(
                SanityIssue(row_number, str(row_number), "unknown_movement_type",
                            f"Tipo no reconocido: {mapped.get('movement_type_raw')!r}")
            )

        tracking = normalize_tracking(mapped.get("tracking_number_raw"))
        external_ref = clean_text(mapped.get("external_ref"))

        # The schema stores a positive magnitude and takes direction from the
        # type's sign, so the file's own sign has to be dropped - but not
        # silently. When the two disagree the row is a reversal, a correction or
        # a misclassification, and abs() turns all three into the opposite of
        # what happened: a refunded collection would be recorded as revenue.
        # Nothing in the operator's current data triggers this; the point is
        # that the day it does, it is reported instead of absorbed.
        expected_sign = MOVEMENT_TYPE_SIGNS.get(type_code or "")
        if expected_sign is not None and amount != 0:
            file_sign = 1 if amount > 0 else -1
            if file_sign != expected_sign:
                report.sanity_issues.append(
                    SanityIssue(
                        row_number, external_ref or str(row_number),
                        "movement_sign_conflict",
                        f"El archivo trae {amount} para «{mapped.get('movement_type_raw')}», "
                        "que normalmente va en sentido contrario. Puede ser una "
                        "reversión o una corrección: se guardó por su magnitud.",
                    )
                )
        magnitude = abs(amount)

        movement = MovementInput(
            movement_type_code=type_code,
            movement_date=movement_date,
            amount=magnitude,
            currency_code=normalize_currency(mapped.get("currency_code"), default_currency),
            # When the source gives the movement its own id, THAT is the
            # identity - nothing else. Including the date looked harmless and
            # was not: the day a date mapping is corrected, every movement gets
            # a new key, re-ingestion inserts instead of matching, and revenue
            # doubles. That happened here - 20,918.25 became 41,814.51 - and it
            # is precisely the failure the dedupe key exists to prevent.
            #
            # The fallback still uses the date, because a row with no id has
            # nothing else to be identified by.
            dedupe_key=(
                dedupe_key(ctx.connection_id, external_ref)
                if external_ref
                else dedupe_key(
                    ctx.connection_id, tracking, None, type_code, movement_date, magnitude
                )
            ),
            tracking_number_raw=tracking,
            external_ref=external_ref,
            description=clean_text(mapped.get("description")),
            source_row_number=row_number,
        )

        issues = check_movement(movement, self._today)
        report.sanity_issues.extend(issues)
        if any(issue.severity == "error" for issue in issues):
            report.rows_failed += 1
            blocking = next(i for i in issues if i.severity == "error")
            report.errors.append(RowError(row_number, blocking.message))
            return

        result = self._store.upsert_movement(ctx, movement)
        _tally(report, result)


def _tally(report: IngestReport, result: UpsertResult) -> None:
    report.discrepancies.extend(result.discrepancies)
    if result.outcome is RowOutcome.INSERTED:
        report.rows_inserted += 1
    elif result.outcome is RowOutcome.UPDATED:
        report.rows_updated += 1
    elif result.outcome is RowOutcome.SKIPPED:
        report.rows_skipped += 1
    else:
        report.rows_failed += 1
        if result.error:
            report.errors.append(RowError(0, result.error))


def _abs_or_none(value: Decimal | None) -> Decimal | None:
    """Money columns are stored as magnitudes; a leading minus is a formatting quirk."""
    return None if value is None else abs(value)


__all__ = [
    "BatchAlreadyExists",
    "IngestEngine",
    "MemoryStore",
    "MergeOutcome",
    "MovementRecord",
    "ShipmentRecord",
    "Store",
    "apply_merge",
    "check_movement",
    "check_shipment",
    "merge_shipment",
]


def _entity_key_of(fields: dict[str, Any]) -> str | None:
    """Best identifier available for an archived row, for later lookup."""
    for key in ("tracking_number", "carrier_tracking_number", "external_ref",
                "tracking_number_raw", "external_order_id"):
        value = fields.get(key)
        if value:
            return str(value)[:120]
    return None
