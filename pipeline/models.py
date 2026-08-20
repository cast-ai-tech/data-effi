"""Data contracts shared by the ingestion engine and every store backend.

These are plain dataclasses on purpose: the pipeline must run without a database
(MemoryStore) so its rules can be tested in isolation from SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class BatchKind(str, Enum):
    SHIPMENTS = "shipments"
    MOVEMENTS = "movements"
    ADS = "ads"
    CS = "cs"


class RowOutcome(str, Enum):
    INSERTED = "inserted"
    UPDATED = "updated"
    SKIPPED = "skipped"   # row was identical to what we already had
    FAILED = "failed"


# Fields that describe *what the shipment is*. Once known they are never
# overwritten by a later file - a later file may only FILL IN what is missing.
STATIC_FIELDS: tuple[str, ...] = (
    "external_order_id",
    "customer_hash",
    "carrier_name",
    "geo_level1",
    "city_name",
    "product_name",
    "supplier_name",
    "store_name",
    "quantity",
    "created_date",
    "currency_code",
    "dispatched_at",
    "delivered_at",
    "returned_at",
)

# Fields that carry money. The newest file wins, but any change against a
# previously known non-null value is recorded in raw.load_discrepancy.
MONEY_FIELDS: tuple[str, ...] = (
    "declared_value",
    "cod_collected",
    "freight_cost",
    "return_freight_cost",
    "product_cost",
    "platform_fee",
)


@dataclass(slots=True)
class ShipmentInput:
    """One shipment row as it arrives from a file or a fetcher."""

    tracking_number: str
    created_date: date
    currency_code: str
    status_code: str

    status_raw: str | None = None
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

    declared_value: Decimal | None = None
    cod_collected: Decimal | None = None
    freight_cost: Decimal | None = None
    return_freight_cost: Decimal | None = None
    product_cost: Decimal | None = None
    platform_fee: Decimal | None = None

    source_row_number: int = 0


@dataclass(slots=True)
class MovementInput:
    """One money movement. It may arrive before its shipment does."""

    movement_type_code: str
    movement_date: date
    amount: Decimal            # always a positive magnitude; direction = type.sign
    currency_code: str
    dedupe_key: str            # sha256 of the identifying fields

    tracking_number_raw: str | None = None
    external_ref: str | None = None
    description: str | None = None
    source_row_number: int = 0


@dataclass(slots=True)
class BatchContext:
    """Everything a store needs to attribute rows to a load."""

    batch_id: UUID
    tenant_id: UUID
    connection_id: UUID
    country_code: str
    platform_code: str
    kind: BatchKind


@dataclass(slots=True)
class Discrepancy:
    """A money field that changed value between two loads of the same entity."""

    entity: str          # 'shipment' | 'movement'
    entity_key: str      # tracking number or movement external ref
    field_name: str
    old_value: str | None
    new_value: str | None


@dataclass(slots=True)
class UpsertResult:
    outcome: RowOutcome
    entity_key: str
    discrepancies: list[Discrepancy] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class SanityIssue:
    """A row that parsed but failed a business plausibility check."""

    row_number: int
    entity_key: str
    code: str
    message: str
    severity: str = "warning"    # 'warning' keeps the row, 'error' rejects it


@dataclass(slots=True)
class RowError:
    row_number: int
    message: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IngestReport:
    """The full outcome of one file load. Serialized into raw.load_batch.report."""

    batch_id: UUID | None
    content_hash: str
    source_name: str
    kind: BatchKind
    already_loaded: bool = False
    rows_total: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_failed: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)
    sanity_issues: list[SanityIssue] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.rows_failed == 0

    def to_json(self) -> dict[str, Any]:
        """JSON-safe payload for raw.load_batch.report."""
        return {
            "already_loaded": self.already_loaded,
            "rows": {
                "total": self.rows_total,
                "inserted": self.rows_inserted,
                "updated": self.rows_updated,
                "skipped": self.rows_skipped,
                "failed": self.rows_failed,
            },
            "discrepancies": [
                {
                    "entity": d.entity,
                    "entity_key": d.entity_key,
                    "field": d.field_name,
                    "old": d.old_value,
                    "new": d.new_value,
                }
                for d in self.discrepancies
            ],
            "sanity_issues": [
                {
                    "row": s.row_number,
                    "entity_key": s.entity_key,
                    "code": s.code,
                    "message": s.message,
                    "severity": s.severity,
                }
                for s in self.sanity_issues
            ],
            "errors": [
                {"row": e.row_number, "message": e.message} for e in self.errors
            ],
            "unmapped_columns": self.unmapped_columns,
        }
