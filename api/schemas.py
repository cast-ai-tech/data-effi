"""Request and response models.

Response models stay close to the mart views: the API is a thin, typed window
onto SQL that already did the thinking.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

Role = Literal["owner", "analyst", "viewer"]
WidgetState = Literal["available", "degraded", "blocked"]


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
    duration_seconds: Decimal | None


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
    declared_value: Decimal | None
    revenue: Decimal | None
    freight: Decimal | None
    cogs: Decimal | None
    fees: Decimal | None
    adjustments: Decimal | None
    ad_spend: Decimal | None
    contribution: Decimal | None
    contribution_margin_pct: Decimal | None
    delivery_rate_terminal_pct: Decimal | None
    delivery_rate_dispatched_pct: Decimal | None
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
    delivery_rate_pct: Decimal | None
    return_rate_pct: Decimal | None
    avg_days_to_deliver: Decimal | None
    p90_days_to_deliver: Decimal | None
    freight_total: Decimal | None
    avg_freight_per_shipment: Decimal | None
    revenue: Decimal | None
    contribution: Decimal | None
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
    delivery_rate_pct: Decimal | None
    revenue: Decimal | None
    contribution: Decimal | None
    avg_days_to_deliver: Decimal | None
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
    delivery_rate_pct: Decimal | None
    revenue: Decimal | None
    cogs: Decimal | None
    freight: Decimal | None
    contribution: Decimal | None
    contribution_per_shipment: Decimal | None
    margin_pct: Decimal | None
    currency_code: str | None


class CohortRow(BaseModel):
    country_code: str
    cohort_date: date
    cohort_size: int
    days_since: int
    delivered_by_day: int
    delivery_rate_pct: Decimal | None
    is_observable: bool
    is_mature: bool
    maturation_days: int


class AgingRow(BaseModel):
    country_code: str
    aging_bucket: str
    bucket_order: int
    shipments: int
    value_at_risk: Decimal | None
    avg_days_open: Decimal | None
    currency_code: str | None


class CsRow(BaseModel):
    country_code: str
    day: date
    interactions: int
    confirmed: int
    rejected: int
    no_answer: int
    pending: int
    confirmation_rate_pct: Decimal | None
    avg_attempts: Decimal | None


class CpaRow(BaseModel):
    country_code: str
    day: date
    ad_spend: Decimal
    impressions: int
    clicks: int
    shipments: int
    delivered: int
    revenue: Decimal
    cpa_dispatched: Decimal | None
    cpa_delivered: Decimal | None
    roas: Decimal | None
    currency_code: str | None


class GlobalRow(BaseModel):
    country_code: str
    country_name: str
    currency_code: str | None
    shipments: int
    delivered: int
    returned: int
    in_transit: int
    delivery_rate_pct: Decimal | None
    revenue: Decimal | None
    ad_spend: Decimal | None
    contribution: Decimal | None
    fx_rate_to_usd: Decimal | None
    fx_rate_date: date | None
    contribution_usd: Decimal | None
    fx_missing: bool
    last_shipment_date: date | None


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
    impact_amount: Decimal | None
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
