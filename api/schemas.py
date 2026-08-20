"""Request and response models.

Response models stay close to the mart views: the API is a thin, typed window
onto SQL that already did the thinking.

MONEY IS `float`, NOT `Decimal`, IN RESPONSES. Pydantic serialises Decimal as a
JSON *string* ("43885700.00"), and JavaScript then happily concatenates those
instead of adding them - a bug that shows up as a dashboard total silently
reading "—". PostgreSQL keeps the exact numeric; the wire format is a number,
which is the only thing JS can add. Requests keep their precise types.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

Role = Literal["owner", "analyst", "viewer"]
WidgetState = Literal["available", "degraded", "blocked"]
CatalogueStatus = Literal["sin_costo", "sin_revisar", "costo_desactualizado", "ok"]


# =============================================================================
# Auth
# =============================================================================


class RegisterRequest(BaseModel):
    """Creates the first owner of a deployment. Everyone else joins by invitation."""

    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=120)
    tenant_name: str = Field(min_length=2, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    role: Role
    tenant_id: UUID
    tenant_name: str
    created_at: datetime


class InviteRequest(BaseModel):
    email: EmailStr
    role: Role = "viewer"


class InviteResponse(BaseModel):
    id: UUID
    email: str
    role: Role
    invitation_token: str = Field(
        description="Show once. Not recoverable: only its hash is stored."
    )
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=120)


# =============================================================================
# Configuration
# =============================================================================


class CountryResponse(BaseModel):
    code: str
    name: str
    currency_code: str
    currency_symbol: str
    decimal_places: int
    thousands_sep: str
    decimal_sep: str
    date_format: str
    locale: str
    timezone: str
    geo_level1_label: str
    is_active: bool = False
    maturation_days: int | None = None
    maturation_days_suggested: int | None = None


class ActivateCountryRequest(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    is_active: bool = True
    maturation_days: int | None = Field(default=None, ge=1, le=120)


class PlatformResponse(BaseModel):
    platform_code: str
    platform_name: str
    tier: int
    data_domains: list[str]
    requires_consent: bool
    docs_url: str | None
    connection_count: int
    active_connection_count: int
    is_connected: bool


class ConnectionCreateRequest(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    platform_code: str
    name: str = Field(min_length=1, max_length=120)
    store_name: str | None = None
    secret_ref: str | None = Field(
        default=None,
        description="NAME of the env var holding the credential. Never the credential itself.",
    )
    consent_granted: bool = Field(
        default=False,
        description="Required for tier-3 platforms. See docs/tier3-politica.md",
    )
    sync_interval_minutes: int = Field(default=720, ge=15, le=10_080)


class ConnectionUpdateRequest(BaseModel):
    name: str | None = None
    secret_ref: str | None = None
    status: Literal["pending", "active", "disabled"] | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=15, le=10_080)


class ConnectionResponse(BaseModel):
    connection_id: UUID
    connection_name: str
    country_code: str
    platform_code: str
    platform_name: str
    tier: int
    status: str
    health: str
    consent_granted_at: datetime | None
    last_sync_at: datetime | None
    last_error: str | None
    hours_since_sync: float | None
    batches_7d: int | None
    failed_batches_7d: int | None


# =============================================================================
# Ingestion
# =============================================================================


class UploadJobResponse(BaseModel):
    id: UUID
    filename: str
    kind: str
    size_bytes: int
    status: str
    batch_id: UUID | None
    error: str | None
    queued_at: datetime
    finished_at: datetime | None


class UploadAcceptedResponse(BaseModel):
    jobs: list[UploadJobResponse]
    message: str


class DetectResponse(BaseModel):
    """What Norte understood about a file, before anything is stored."""

    filename: str
    format: str = Field(description="xlsx | html | csv | xls_binary")
    profile_code: str | None
    profile_label: str | None
    detected_country_code: str | None
    detected_country_raw: str | None
    row_count: int
    column_count: int
    mapped_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Encabezado del archivo -> campo canonico de Norte.",
    )
    unmapped_columns: list[str] = Field(default_factory=list)


class BatchSummary(BaseModel):
    batch_id: UUID
    connection_id: UUID
    connection_name: str
    country_code: str
    platform_code: str
    source_name: str
    kind: str
    status: str
    rows_total: int
    rows_inserted: int
    rows_updated: int
    rows_skipped: int
    rows_failed: int
    discrepancy_count: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None


class PaginatedBatches(BaseModel):
    items: list[BatchSummary]
    total: int
    page: int
    page_size: int


class DiscrepancyResponse(BaseModel):
    entity: str
    entity_key: str
    field_name: str
    old_value: str | None
    new_value: str | None
    detected_at: datetime


class BatchDetail(BaseModel):
    batch: BatchSummary
    report: dict[str, Any]
    discrepancies: list[DiscrepancyResponse]


# =============================================================================
# KPIs
# =============================================================================


class DailyContributionRow(BaseModel):
    country_code: str
    store_id: UUID | None
    store_name: str | None
    day: date
    shipments: int
    delivered: int
    returned: int
    in_transit: int
    dead: int
    declared_value: float | None
    revenue: float | None
    freight: float | None
    cogs: float | None
    fees: float | None
    adjustments: float | None
    ad_spend: float | None
    contribution: float | None
    contribution_margin_pct: float | None
    delivery_rate_terminal_pct: float | None
    delivery_rate_dispatched_pct: float | None
    currency_code: str | None
    ad_spend_missing: bool


class CarrierRow(BaseModel):
    country_code: str
    carrier_id: UUID | None
    carrier_name: str
    shipments: int
    delivered: int
    returned: int
    in_transit: int
    delivery_rate_pct: float | None
    return_rate_pct: float | None
    avg_days_to_deliver: float | None
    p90_days_to_deliver: float | None
    freight_total: float | None
    avg_freight_per_shipment: float | None
    revenue: float | None
    contribution: float | None
    currency_code: str | None


class GeoRow(BaseModel):
    country_code: str
    geo_id: UUID | None
    level1_name: str
    city_name: str
    shipments: int
    delivered: int
    returned: int
    in_transit: int
    delivery_rate_pct: float | None
    revenue: float | None
    contribution: float | None
    avg_days_to_deliver: float | None
    traffic_light: str
    currency_code: str | None


class ProductRow(BaseModel):
    country_code: str
    product_id: UUID | None
    product_name: str
    sku: str | None
    supplier_name: str | None
    shipments: int
    units: int | None
    delivered: int
    returned: int
    delivery_rate_pct: float | None
    revenue: float | None
    cogs: float | None
    freight: float | None
    contribution: float | None
    contribution_per_shipment: float | None
    margin_pct: float | None
    currency_code: str | None


class CohortRow(BaseModel):
    country_code: str
    cohort_date: date
    cohort_size: int
    days_since: int
    delivered_by_day: int
    delivery_rate_pct: float | None
    is_observable: bool
    is_mature: bool
    maturation_days: int


class AgingRow(BaseModel):
    country_code: str
    aging_bucket: str
    bucket_order: int
    shipments: int
    value_at_risk: float | None
    avg_days_open: float | None
    currency_code: str | None


class CsRow(BaseModel):
    country_code: str
    day: date
    interactions: int
    confirmed: int
    rejected: int
    no_answer: int
    pending: int
    confirmation_rate_pct: float | None
    avg_attempts: float | None


class CpaRow(BaseModel):
    country_code: str
    day: date
    ad_spend: float
    impressions: int
    clicks: int
    shipments: int
    delivered: int
    revenue: float
    cpa_dispatched: float | None
    cpa_delivered: float | None
    roas: float | None
    currency_code: str | None


class GlobalRow(BaseModel):
    country_code: str
    country_name: str
    currency_code: str | None
    shipments: int
    delivered: int
    returned: int
    in_transit: int
    delivery_rate_pct: float | None
    revenue: float | None
    ad_spend: float | None
    contribution: float | None
    fx_rate_to_usd: float | None
    fx_rate_date: date | None
    contribution_usd: float | None
    fx_missing: bool
    last_shipment_date: date | None


class DropshippingMarginRow(BaseModel):
    """mart.v_dropshipping_margin - charged, paid, and what is left, per product."""

    country_code: str
    product_id: UUID | None
    product_name: str
    sku: str | None
    supplier_name: str | None
    shipments: int
    delivered: int
    units: int | None
    revenue: float | None
    supplier_cost: float | None
    freight: float | None
    gross_margin: float | None
    gross_margin_pct: float | None
    net_contribution: float | None
    contribution_per_shipment: float | None
    cost_of_undelivered: float | None
    breakeven_delivery_pct: float | None
    delivery_rate_pct: float | None
    catalogue_cost: float | None
    catalogue_price: float | None
    catalogue_reviewed: bool
    observed_unit_cost: float | None
    currency_code: str | None


class FulfillmentRow(BaseModel):
    """mart.v_fulfillment_sla - the merchant's half of the clock, and the carrier's."""

    country_code: str
    carrier_id: UUID | None
    carrier_name: str
    service_level: str
    shipments: int
    delivered: int
    avg_prep_days: float | None
    p50_prep_days: float | None
    p90_prep_days: float | None
    avg_transit_days: float | None
    p90_transit_days: float | None
    avg_total_days: float | None
    prep_share_pct: float | None
    on_time_count: int
    measurable_count: int
    on_time_pct: float | None


class OfficeRescueRow(BaseModel):
    """mart.v_office_rescue - parcels at an agency, by how long they have waited."""

    country_code: str
    carrier_name: str
    level1_name: str
    city_name: str
    shipments: int
    value_waiting: float | None
    avg_days_waiting: float | None
    fresh_0_7: int
    aging_8_14: int
    urgent_15_21: int
    probably_lost: int
    value_still_recoverable: float | None
    currency_code: str | None


class FreightRow(BaseModel):
    """mart.v_freight_analysis - freight per kilo, by component, and the discount."""

    country_code: str
    carrier_id: UUID | None
    carrier_name: str
    service_level: str
    shipments: int
    avg_weight_kg: float | None
    total_weight_kg: float | None
    freight_total: float | None
    avg_freight: float | None
    freight_per_kg: float | None
    avg_freight_base: float | None
    avg_handling: float | None
    avg_collection_fee: float | None
    avg_discount_pct: float | None
    discount_value: float | None
    freight_share_of_value_pct: float | None
    return_freight_total: float | None
    currency_code: str | None


class CashCycleRow(BaseModel):
    """mart.v_cash_cycle - days from dispatch to money you can actually spend."""

    country_code: str
    settled: int
    delivered_unsettled: int
    avg_days_to_cash: float | None
    p50_days_to_cash: float | None
    p90_days_to_cash: float | None
    cash_in_transit: float | None
    currency_code: str | None


class ProblemRateRow(BaseModel):
    """mart.v_problem_rate - novedad + oficina + devolucion as one number."""

    country_code: str
    carrier_id: UUID | None
    carrier_name: str
    shipments: int
    novedad: int
    en_oficina: int
    devolucion: int
    con_problema: int
    problem_rate_pct: float | None
    value_in_office: float | None
    currency_code: str | None


class LayoutWidget(BaseModel):
    widget_code: str
    tab: str
    title: str
    description: str
    sort_order: int
    state: WidgetState
    state_message: str | None
    required_domains: list[str]
    optional_domains: list[str]
    missing_required: list[str]
    missing_optional: list[str]
    awaiting_data: list[str]


class LayoutResponse(BaseModel):
    country_code: str
    widgets: list[LayoutWidget]


# =============================================================================
# Product catalogue
#
# A product exists two ways: ingestion saw its name in a report, or a person
# typed it here. `reviewed_at` is the difference, and the catalogue view reports
# it, so the UI can ask for the half nobody has confirmed yet.
# =============================================================================


class ProductCatalogueRow(BaseModel):
    """mart.v_product_catalogue - the catalogue next to what the reports observed."""

    product_id: UUID
    product_name: str
    sku: str | None
    category: str | None
    supplier_name: str | None
    unit_cost: float | None
    list_price: float | None
    target_margin_pct: float | None
    weight_kg: float | None
    currency_code: str | None
    is_active: bool
    reviewed_at: datetime | None
    notes: str | None
    shipments: int
    delivered: int
    last_shipment_date: date | None
    observed_unit_cost: float | None
    catalogue_status: CatalogueStatus
    catalogue_margin_pct: float | None


class ProductCostHistoryRow(BaseModel):
    """One row of core.product_cost_history. 'import' means a file wrote it."""

    id: int
    unit_cost: float
    currency_code: str | None
    source: Literal["manual", "import", "observed"]
    changed_by: UUID | None
    changed_at: datetime


class ProductDetail(BaseModel):
    product: ProductCatalogueRow
    cost_history: list[ProductCostHistoryRow]


class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=120)
    supplier_name: str | None = Field(
        default=None,
        max_length=200,
        description="Se crea el proveedor si no existe, igual que en la ingesta.",
    )
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    list_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    target_margin_pct: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=6, decimal_places=2
    )
    weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)


class ProductUpdateRequest(BaseModel):
    """Every field optional. Omitting one leaves it exactly as it was."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=120)
    supplier_name: str | None = Field(default=None, max_length=200)
    unit_cost: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
        description="Al enviarlo, el producto queda marcado como revisado por ti.",
    )
    list_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    target_margin_pct: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=6, decimal_places=2
    )
    weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)


# =============================================================================
# AI
# =============================================================================


class BriefResponse(BaseModel):
    country_code: str
    generated_at: datetime
    summary: str
    cached: bool
    degraded: bool = False
    degraded_reason: str | None = None


class AlertResponse(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    title: str
    finding: str
    impact_amount: float | None
    impact_currency: str | None
    action: str
    deep_link: str
    detected_at: datetime


class AlertsResponse(BaseModel):
    country_code: str | None
    alerts: list[AlertResponse]
    degraded: bool = False
    degraded_reason: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)


class AskResponse(BaseModel):
    answer: str
    sql: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    rejected: bool = False
    rejection_reason: str | None = None
    suggestions: list[str] = Field(default_factory=list)
