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

from pipeline.mapping import (
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
    detect_profile,
)
from pipeline.readers import iter_records, read_tabular

logger = logging.getLogger(__name__)

# A guide older than this is almost certainly a mis-parsed date, not history.
MAX_BACKDATE_DAYS = 1095
# Collecting more than this multiple of the declared value means a bad column map.
COD_OVERCOLLECT_FACTOR = Decimal("1.5")


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
    ) -> BatchContext:
        ...

    def upsert_shipment(self, ctx: BatchContext, shipment: ShipmentInput) -> UpsertResult:
        ...

    def upsert_movement(self, ctx: BatchContext, movement: MovementInput) -> UpsertResult:
        ...

    def finish_batch(self, ctx: BatchContext, report: IngestReport) -> None:
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
    carrier_name: str | None = None
    geo_level1: str | None = None
    city_name: str | None = None
    product_name: str | None = None
    supplier_name: str | None = None
    store_name: str | None = None
    quantity: int = 1
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    returned_at: datetime | None = None
    last_status_at: datetime | None = None
    expected_delivery_date: date | None = None
    settled_at: datetime | None = None
    settled_with_collection: bool | None = None
    service_level: str | None = None
    declared_value: Decimal | None = None
    cod_collected: Decimal | None = None
    freight_cost: Decimal | None = None
    return_freight_cost: Decimal | None = None
    product_cost: Decimal | None = None
    platform_fee: Decimal | None = None
    insurance_cost: Decimal | None = None
    collection_fee: Decimal | None = None
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

    # -- batch lifecycle ------------------------------------------------
    def batch_exists(self, tenant_id: UUID, connection_id: UUID, content_hash: str) -> bool:
        return (tenant_id, connection_id, content_hash) in self.batches

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
        key = (tenant_id, connection_id, content_hash)
        if key in self.batches:
            raise BatchAlreadyExists(content_hash)
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
                carrier_name=shipment.carrier_name,
                geo_level1=shipment.geo_level1,
                city_name=shipment.city_name,
                product_name=shipment.product_name,
                supplier_name=shipment.supplier_name,
                store_name=shipment.store_name,
                quantity=shipment.quantity,
                dispatched_at=shipment.dispatched_at,
                delivered_at=shipment.delivered_at,
                returned_at=shipment.returned_at,
                last_status_at=shipment.last_status_at,
                expected_delivery_date=shipment.expected_delivery_date,
                settled_at=shipment.settled_at,
                settled_with_collection=shipment.settled_with_collection,
                service_level=shipment.service_level,
                declared_value=shipment.declared_value,
                cod_collected=shipment.cod_collected,
                freight_cost=shipment.freight_cost,
                return_freight_cost=shipment.return_freight_cost,
                product_cost=shipment.product_cost,
                platform_fee=shipment.platform_fee,
                insurance_cost=shipment.insurance_cost,
                collection_fee=shipment.collection_fee,
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
# Engine
# =============================================================================


class IngestEngine:
    """Runs one file through parse -> validate -> merge -> report."""

    def __init__(self, store: Store, *, pii_salt: str, today: date | None = None) -> None:
        if not pii_salt:
            raise ValueError("pii_salt is required: customer identifiers are never stored raw")
        self._store = store
        self._pii_salt = pii_salt
        self._today = today or datetime.now(UTC).date()

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
    ) -> IngestReport:
        digest = content_hash(payload)
        report = IngestReport(
            batch_id=None,
            content_hash=digest,
            source_name=source_name,
            kind=kind,
            started_at=datetime.now(UTC),
        )

        if self._store.batch_exists(tenant_id, connection_id, digest):
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
            )
        except BatchAlreadyExists:
            # Lost a race against a concurrent upload of the same bytes. That is
            # the correct outcome, not a failure.
            report.already_loaded = True
            report.finished_at = datetime.now(UTC)
            return report

        report.batch_id = ctx.batch_id

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

        for row_number, mapped, raw in iter_records(headers, rows, header_map):
            report.rows_total += 1
            try:
                fields = apply_transform(profile, mapped) if profile else mapped
                handler(ctx, report, row_number, fields, default_currency)
            except Exception as exc:
                report.rows_failed += 1
                report.errors.append(RowError(row_number, str(exc), raw))
                logger.warning("row %s failed: %s", row_number, exc)

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
            carrier_name=clean_text(mapped.get("carrier_name")),
            geo_level1=clean_text(mapped.get("geo_level1")),
            city_name=clean_text(mapped.get("city_name")),
            product_name=clean_text(mapped.get("product_name")),
            supplier_name=clean_text(mapped.get("supplier_name")),
            store_name=clean_text(mapped.get("store_name")),
            quantity=max(quantity, 1),
            dispatched_at=parse_datetime(mapped.get("dispatched_at")),
            delivered_at=parse_datetime(mapped.get("delivered_at")),
            returned_at=parse_datetime(mapped.get("returned_at")),
            last_status_at=parse_datetime(mapped.get("last_status_at")),
            expected_delivery_date=parse_date(mapped.get("expected_delivery_date")),
            settled_at=parse_datetime(mapped.get("settled_at")),
            settled_with_collection=mapped.get("settled_with_collection")
            if isinstance(mapped.get("settled_with_collection"), bool)
            else None,
            service_level=clean_text(mapped.get("service_level")),
            declared_value=_abs_or_none(parse_decimal(mapped.get("declared_value"))),
            cod_collected=_abs_or_none(parse_decimal(mapped.get("cod_collected"))),
            freight_cost=_abs_or_none(parse_decimal(mapped.get("freight_cost"))),
            return_freight_cost=_abs_or_none(parse_decimal(mapped.get("return_freight_cost"))),
            product_cost=_abs_or_none(parse_decimal(mapped.get("product_cost"))),
            platform_fee=_abs_or_none(parse_decimal(mapped.get("platform_fee"))),
            insurance_cost=_abs_or_none(parse_decimal(mapped.get("insurance_cost"))),
            collection_fee=_abs_or_none(parse_decimal(mapped.get("collection_fee"))),
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
        magnitude = abs(amount)

        movement = MovementInput(
            movement_type_code=type_code,
            movement_date=movement_date,
            amount=magnitude,
            currency_code=normalize_currency(mapped.get("currency_code"), default_currency),
            dedupe_key=dedupe_key(
                ctx.connection_id, tracking, external_ref, type_code, movement_date, magnitude
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
