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
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints

# `uploader` is not a rung below `viewer`: it may write loads and may not read a
# single number. See api/deps.CAPABILITIES for what each one actually unlocks.
Role = Literal["owner", "analyst", "viewer", "uploader"]
Capability = Literal["read", "ingest", "config", "manage"]

# Roles ACROSS the companies of one org. None of them opens a company's own
# data - core.membership decides that. See migration 036.
OrgRole = Literal["admin", "analyst", "viewer"]
CountryCode = Annotated[str, StringConstraints(min_length=2, max_length=2, to_upper=True)]
WidgetState = Literal["available", "degraded", "blocked"]
CatalogueStatus = Literal["sin_costo", "sin_revisar", "costo_desactualizado", "ok"]

# Whether a percentage was measured or merely computed. Below ten terminal
# guides a delivery rate is arithmetic, not evidence - see migration 021.
SampleQuality = Literal["suficiente", "muestra_corta"]

# Which date a number is anchored to. Defined up here rather than beside the KPI
# models because the organization roll-up - the first models in this file - also
# reports the basis it applied, and Pydantic resolves these aliases when the
# class is built, not when it is used.
DateBasis = Literal[
    "creacion", "despacho", "entrega", "movimiento", "interaccion", "pauta"
]

# What the caller may ASK to filter on. A subset of the bases above: a guide has
# these three dates, while "interaccion" and "pauta" belong to other sources and
# are never a choice - the endpoints that use them have no other option.
DateField = Literal["creacion", "despacho", "entrega"]
DATE_FIELDS: tuple[str, ...] = ("creacion", "despacho", "entrega")


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


class WorkspaceSummary(BaseModel):
    """One company the caller may open, and the grant that applies there."""

    tenant_id: UUID
    name: str
    slug: str
    role: Role
    countries: list[str] = Field(
        default_factory=list, description="Países que opera la sociedad"
    )
    country_scope: list[str] | None = Field(
        default=None,
        description="Si no es null, los únicos países que esta persona puede ver",
    )
    share_pct: float | None = Field(
        default=None, description="Participación del socio, solo informativa"
    )


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    # Which company this token stands in, and everything the UI needs to render
    # the switcher without a second round trip.
    tenant_id: UUID | None = None
    tenant_name: str | None = None
    role: Role | None = None
    is_org_admin: bool = False
    org_role: OrgRole | None = None
    countries: list[str] | None = None
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)


class SwitchWorkspaceRequest(BaseModel):
    tenant_id: UUID


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    role: Role
    tenant_id: UUID | None
    tenant_name: str | None
    created_at: datetime
    org_id: UUID | None = None
    org_name: str | None = None
    is_org_admin: bool = False
    # admin | analyst | viewer over the holding, or None. Distinct from `role`,
    # which is what this person may do INSIDE the company they are standing in.
    org_role: OrgRole | None = None
    countries: list[str] | None = None
    capabilities: list[Capability] = Field(default_factory=list)
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)


class FxRateRow(BaseModel):
    """Una moneda contra el dólar y contra el peso colombiano.

    `to_usd` y `to_cop` son cuánto vale UNA unidad de esta moneda. `per_usd` es
    el camino de vuelta - cuántas unidades cuesta un dólar - porque así es como
    se cotiza hablando: "el dólar está a 3.900".
    """

    currency_code: str
    currency_symbol: str
    decimal_places: int
    country_codes: list[str]
    country_names: list[str]
    to_usd: float | None = None
    to_cop: float | None = None
    per_usd: float | None = None
    rate_date: date | None = None
    source: str | None = None
    has_rate: bool = False
    # Más de tres días sin actualizar. No se oculta la tasa: se marca.
    is_stale: bool | None = None


class FxRateUpsertRequest(BaseModel):
    """Fijar una tasa a mano, que queda con source='manual'.

    Existe por el bolívar: VES se mueve más rápido de lo que una tasa diaria
    puede seguir, y la oficial no es la que usa la calle. Quien opera allí sabe
    cuál aplicar mejor que cualquier proveedor.
    """

    currency_code: Annotated[
        str, StringConstraints(min_length=3, max_length=3, to_upper=True)
    ]
    # Cuántas unidades de esa moneda cuesta un dólar: 3900 para el peso, no 0.00025.
    per_usd: float = Field(gt=0, description="Cuántas unidades equivalen a 1 USD")
    rate_date: date | None = None


class OrgRow(BaseModel):
    id: UUID
    slug: str
    name: str
    base_currency: str
    is_active: bool = True


class OrgUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # The currency the roll-up converts INTO. Never changes what a company billed.
    base_currency: Annotated[
        str, StringConstraints(min_length=3, max_length=3, to_upper=True)
    ] | None = None


class OrgMemberRow(BaseModel):
    user_id: UUID
    email: str
    full_name: str | None
    role: OrgRole
    is_active: bool = True
    last_login_at: datetime | None = None


class OrgMemberGrantRequest(BaseModel):
    """Promotes an existing account. Creating one is an invitation to a company."""

    email: EmailStr
    role: OrgRole = "viewer"


class OrgMemberUpdateRequest(BaseModel):
    role: OrgRole | None = None
    is_active: bool | None = None


class ProfileUpdateRequest(BaseModel):
    """The part of your own account you may change. Email is not in it.

    Changing an email would move an identity that other companies already granted
    access to, so it is an administrator's action, not a self-service one.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=120)


class PasswordChangeRequest(BaseModel):
    """The current password is required even though the caller is authenticated.

    A token left open on a borrowed laptop is exactly the case this stops: it
    proves the person at the keyboard is the owner of the account, not merely the
    holder of a session.
    """

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class SessionRow(BaseModel):
    """One live refresh token: a device or browser that can still come back."""

    id: UUID
    tenant_id: UUID | None = None
    tenant_name: str | None = None
    created_at: datetime
    expires_at: datetime
    is_current: bool = False


class BranchResponse(BaseModel):
    id: UUID
    country_code: str
    name: str
    cost_center: str | None = None
    address: str | None = None
    city: str | None = None
    manager_name: str | None = None
    phone: str | None = None
    is_warehouse: bool = False
    is_active: bool = True
    notes: str | None = None
    created_at: datetime
    # How many stores ship from here. Shown so deactivating a branch that still
    # has stores is a decision and not a surprise.
    store_count: int = 0


class BranchCreateRequest(BaseModel):
    country_code: CountryCode
    name: str = Field(min_length=1, max_length=120)
    cost_center: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    manager_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    is_warehouse: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class BranchUpdateRequest(BaseModel):
    """Every field optional: this is a patch, and omitted means unchanged.

    `country_code` is absent on purpose. Moving a branch to another country would
    silently move the stores hanging off it, so that is a new branch.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    cost_center: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    manager_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    is_warehouse: bool | None = None
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


class InviteRequest(BaseModel):
    email: EmailStr
    role: Role = "viewer"
    country_scope: list[CountryCode] | None = Field(
        default=None,
        description="Países que podrá ver. Null = toda la sociedad.",
        min_length=1,
    )
    share_pct: float | None = Field(
        default=None, gt=0, le=100, description="Participación del socio (informativa)"
    )


class InviteResponse(BaseModel):
    id: UUID
    email: str
    role: Role
    invitation_token: str | None = Field(
        default=None,
        description="Se muestra una sola vez. Null si la persona ya tenía cuenta.",
    )
    expires_at: datetime
    already_registered: bool = Field(
        default=False,
        description="True cuando ya existía la cuenta y el acceso quedó activo al instante.",
    )
    country_scope: list[str] | None = None
    share_pct: float | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=120)


# =============================================================================
# Organization: the holding above the companies
#
# Money here is ALWAYS in the org's base currency (USD by default). Per-company
# figures keep their own currency; only the roll-up converts, and `fx_missing`
# says when a company had to be left out of a total instead of being counted at
# a rate nobody has.
# =============================================================================


class OrgTenantRow(BaseModel):
    """One company's contribution to the consolidated picture."""

    tenant_id: UUID
    name: str
    slug: str
    countries: list[str] = Field(default_factory=list)
    shipments: int = 0
    delivered: int = 0
    delivery_rate_pct: float | None = None
    revenue_usd: float | None = None
    ad_spend_usd: float | None = None
    contribution_usd: float | None = None
    share_pct: float | None = None
    my_share_usd: float | None = Field(
        default=None, description="contribution_usd x share_pct, cuando hay participación"
    )
    fx_missing: bool = False
    last_shipment_date: date | None = None


class OrgCountryRow(BaseModel):
    """The same countries can appear in several companies; here they are added up."""

    country_code: str
    country_name: str
    shipments: int = 0
    delivered: int = 0
    delivery_rate_pct: float | None = None
    revenue_usd: float | None = None
    contribution_usd: float | None = None
    tenants: list[str] = Field(
        default_factory=list, description="Sociedades que operan este país"
    )


class OrgTotals(BaseModel):
    shipments: int = 0
    delivered: int = 0
    delivery_rate_pct: float | None = None
    revenue_usd: float | None = None
    ad_spend_usd: float | None = None
    contribution_usd: float | None = None
    my_share_usd: float | None = None


class OrgSummaryResponse(BaseModel):
    org_id: UUID
    org_name: str
    base_currency: str
    date_from: date | None = None
    date_to: date | None = None
    date_basis: DateField | None = None
    totals: OrgTotals
    by_tenant: list[OrgTenantRow] = Field(default_factory=list)
    by_country: list[OrgCountryRow] = Field(default_factory=list)
    unavailable: list[str] = Field(
        default_factory=list,
        description="Sociedades que no pudieron leerse; sus números NO están en los totales.",
    )


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    countries: list[CountryCode] = Field(
        default_factory=list, description="Países en los que opera la sociedad"
    )
    notes: str | None = Field(default=None, max_length=500)


class TenantRow(BaseModel):
    tenant_id: UUID
    name: str
    slug: str
    countries: list[str] = Field(default_factory=list)
    member_count: int = 0
    notes: str | None = None
    created_at: datetime


class MemberRow(BaseModel):
    user_id: UUID
    email: str
    full_name: str | None
    role: Role
    country_scope: list[str] | None = None
    share_pct: float | None = None
    is_active: bool = True
    last_login_at: datetime | None = None


class MemberUpdateRequest(BaseModel):
    """Every field optional: this is a patch, and omitted means unchanged."""

    role: Role | None = None
    country_scope: list[CountryCode] | None = Field(default=None, min_length=1)
    # Explicitly widening a limited membership back to the whole company.
    clear_country_scope: bool = False
    share_pct: float | None = Field(default=None, gt=0, le=100)
    is_active: bool | None = None


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


PlatformScope = Literal["country", "global"]
PlatformAvailability = Literal["available", "beta", "planned"]
BatchKindName = Literal["shipments", "movements", "ads", "cs"]


class PlatformResponse(BaseModel):
    """One row of the integration catalogue.

    Serves two views. `mart.v_available_platforms` knows how many connections a
    country already has; `mart.v_platform_catalogue` is the full list including
    what does not work yet, and carries no counts. The count fields therefore
    default instead of being required - a catalogue row is not a broken row.
    """

    platform_code: str
    platform_name: str
    tier: int
    data_domains: list[str]
    requires_consent: bool
    docs_url: str | None = None
    connection_count: int = 0
    active_connection_count: int = 0
    is_connected: bool = False
    # Migration 012: what the integration is, and whether it works today.
    scope: PlatformScope
    category: str
    auth_type: str
    availability: PlatformAvailability
    direction: str
    setup_hint: str | None = None
    available_countries: list[str] = Field(
        default_factory=list,
        description="Países donde se puede conectar. Vacío en las plataformas globales.",
    )


class ConnectionCreateRequest(BaseModel):
    country_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Obligatorio en plataformas de alcance 'country'. Debe omitirse en las "
            "globales: ahí el archivo dice de qué país es cada carga."
        ),
    )
    platform_code: str
    name: str = Field(min_length=1, max_length=120)
    store_name: str | None = None
    secret_ref: str | None = Field(
        default=None,
        description="NAME of the env var holding the credential. Never the credential itself.",
    )
    source_url: str | None = Field(
        default=None,
        description=(
            "Solo para Google Sheets: la URL pública de la hoja publicada como CSV. "
            "No es un secreto y no se guarda como tal."
        ),
    )
    default_kind: BatchKindName | None = Field(
        default=None,
        description="Tipo de datos por defecto cuando la fuente no lo declara.",
    )
    consent_granted: bool = Field(
        default=False,
        description="Required for tier-3 platforms. See docs/tier3-politica.md",
    )
    sync_interval_minutes: int = Field(default=720, ge=15, le=10_080)


class ConnectionUpdateRequest(BaseModel):
    name: str | None = None
    secret_ref: str | None = None
    source_url: str | None = None
    default_kind: BatchKindName | None = None
    status: Literal["pending", "active", "disabled"] | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=15, le=10_080)


class ConnectionResponse(BaseModel):
    connection_id: UUID
    connection_name: str
    # NULL on a global connection: it is not tied to any one country.
    country_code: str | None = None
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
    # Migration 012.
    scope: PlatformScope
    category: str
    has_webhook: bool = False


class WebhookTokenResponse(BaseModel):
    """The one and only time the plaintext token is ever shown."""

    connection_id: UUID
    token: str = Field(
        description="Se muestra una sola vez. Guárdalo ahora; no se puede volver a ver."
    )
    url: str = Field(description="URL completa para pegar en n8n, Make o Zapier.")
    default_kind: BatchKindName | None = None
    created_at: datetime
    message: str


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
    """What Data Effi understood about a file, before anything is stored."""

    filename: str
    format: str = Field(description="xlsx | html | csv | xls_binary")
    profile_code: str | None
    profile_label: str | None
    # Migration 042: the platform the recognised report belongs to, so the
    # upload screen can pre-select it and refuse a mismatch before anything
    # is stored. Null when the shape is not recognised.
    detected_platform_code: str | None = None
    detected_platform_name: str | None = None
    detected_country_code: str | None
    detected_country_raw: str | None
    row_count: int
    column_count: int
    mapped_columns: dict[str, str] = Field(
        default_factory=dict,
        description="Encabezado del archivo -> campo canonico de Data Effi.",
    )
    unmapped_columns: list[str] = Field(default_factory=list)


class BatchSummary(BaseModel):
    batch_id: UUID
    connection_id: UUID
    connection_name: str
    # NULL only on batches loaded before migration 013 through a global connection.
    country_code: str | None = None
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
#
# THE DATE RANGE, AND SAYING WHICH DATE IT WAS
#
# A COD guide has several dates and they answer different questions: the day it
# was created, the day it was delivered, the day the money settled. The default
# everywhere is the CREATION date, because it is the only one every guide has -
# a guide still in transit has no delivery date, so filtering on that one hides
# exactly the guides the operator still has to chase.
#
# `date_basis` is on the response rather than on each row so an EMPTY result can
# still say what it filtered on. "No hay datos" and "no hay guías CREADAS en ese
# rango" are different sentences, and only the second one is useful.
#
#   creacion    - fecha de creación de la guía (el caso por defecto)
#   despacho    - fecha de la relación de despacho (`date_field=despacho`)
#   entrega     - fecha de entrega (`date_field=entrega`)
#   interaccion - fecha de la gestión de servicio al cliente
#   pauta       - fecha del gasto en pauta
#   movimiento  - fecha en que se movió el dinero (reservado; hoy nadie lo usa,
#                 ver la cabecera de la migración 018 sobre v_cash_cycle)
#   null        - el endpoint no admite filtro por fecha, y lo dice en vez de
#                 aceptar el parámetro y no aplicarlo
#
# `date_basis` reporta lo que se APLICÓ, no lo que se pidió. Un endpoint que no
# puede honrar el `date_field` recibido devuelve la base que usó de verdad -
# ver la cabecera de la migración 020 para los cuatro casos y su porqué.
# =============================================================================

RowT = TypeVar("RowT", bound=BaseModel)


class KpiResponse(BaseModel, Generic[RowT]):
    """Every KPI endpoint answers with this shape.

    `date_from` and `date_to` are echoed back so the UI can label the widget with
    the range the server actually used, instead of the one it believes it asked
    for.
    """

    rows: list[RowT]
    date_basis: DateBasis | None = Field(
        default=None, description="Sobre qué fecha se filtró. Null = no admite filtro."
    )
    date_from: date | None = None
    date_to: date | None = None
    excluded_no_date: int | None = Field(
        default=None,
        description=(
            "Guías que no pueden caer en ningún rango sobre esa fecha porque "
            "todavía no la tienen. Null si no se aplicó rango o no aplica."
        ),
    )
    # Migration 040/041. Same contract as `date_basis`: what was APPLIED, never
    # what was asked for. Null = this answer mixes every platform, either
    # because none was requested or because this endpoint cannot separate them.
    platform: str | None = Field(
        default=None,
        description=(
            "Plataforma (effi, dropi...) sobre la que se filtró. Null = todas: "
            "no se pidió, o este endpoint no separa por plataforma."
        ),
    )


class DailyStatusRow(BaseModel):
    """mart.f_daily_status - one day, one platform, the guides by status group.

    Two return percentages on purpose (see migration 040): `pct_devolucion_total`
    is what the operator's hand-made report prints; `pct_devolucion_cerradas`
    is the one that will still be true once the day's guides close.
    """

    country_code: str
    platform_code: str
    platform_name: str
    day: date
    shipments: int
    entregada: int
    devolucion: int
    en_camino: int
    novedad: int
    muerta: int
    cerradas: int
    pct_entrega_cerradas: float | None
    pct_devolucion_cerradas: float | None
    pct_devolucion_total: float | None
    sample_quality: SampleQuality
    declared_value: float | None
    revenue: float | None
    contribution: float | None
    currency_code: str | None


class PlatformSummaryRow(BaseModel):
    """mart.f_platform_summary - one row per platform, with its share of the guides."""

    country_code: str
    platform_code: str
    platform_name: str
    shipments: int
    entregada: int
    devolucion: int
    en_camino: int
    novedad: int
    muerta: int
    cerradas: int
    pct_entrega_cerradas: float | None
    pct_devolucion_cerradas: float | None
    pct_devolucion_total: float | None
    share_pct: float | None
    sample_quality: SampleQuality
    declared_value: float | None
    revenue: float | None
    contribution: float | None
    currency_code: str | None
    first_day: date | None
    last_day: date | None


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
    # From migration 021. Optional because a response built before that column
    # existed is still a valid CarrierRow, and because a missing flag must read
    # as "no sabemos" rather than as "suficiente".
    sample_quality: SampleQuality | None = None


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
    # Always null: the layout says which widgets exist for a country, which is
    # not a question a date range can narrow. Stated rather than omitted, so the
    # UI can grey the picker out for this call instead of guessing.
    date_basis: DateBasis | None = None


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
    """Merge-patch semantics (RFC 7396), because PATCH has three cases:

        campo ausente        -> queda como estaba
        campo con valor      -> se guarda
        campo en null        -> se BORRA

    `name` e `is_active` son NOT NULL en la base: mandarlos en null es un 422,
    no un borrado.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=120)
    supplier_name: str | None = Field(default=None, max_length=200)
    unit_cost: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
        description=(
            "Con valor, el producto queda marcado como revisado por ti. "
            "En null, se borra el costo y también la marca de revisión."
        ),
    )
    list_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    target_margin_pct: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=6, decimal_places=2
    )
    weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = Field(
        default=None,
        description="Reactivar un producto desactivado. DELETE es lo que lo desactiva.",
    )


# =============================================================================
# Contribution, split
#
# One net number reads as a loss whenever a young cohort is large: those guides
# have paid freight, product and fee and collected nothing, because they have
# not arrived yet. `mart.v_contribution_split` reports the two halves so the UI
# can say "in the street" instead of "lost".
# =============================================================================


class ContributionSplitRow(BaseModel):
    """mart.v_contribution_split - what closed, next to what is still moving."""

    country_code: str
    currency_code: str | None
    shipments: int
    closed_shipments: int
    open_shipments: int
    realised_revenue: float | None
    realised_cost: float | None
    realised_contribution: float | None
    realised_margin_pct: float | None
    capital_in_street: float | None
    committed_revenue: float | None
    net_contribution: float | None
    maturity_pct: float | None = Field(
        default=None,
        description="Qué % de las guías ya cerró. Por debajo de ~60 la cifra realizada es "
        "una muestra pequeña y la UI debe decirlo.",
    )


# =============================================================================
# Orders and customers
#
# THE ONLY RESPONSES IN THIS FILE THAT CARRY A REAL PERSON'S DATA.
#
# `customer_name`, `customer_phone`, `customer_address` and `customer_document`
# are decrypted by the API, never by the database, and only for owner/analyst.
# For a viewer they are null - the server does not send them at all, so there is
# nothing for the client to un-hide. `pii_visible` tells the UI which of the two
# it is looking at, so an empty column can be labelled "sin permiso" instead of
# "sin teléfono".
#
# `customer_ref` ('#A3F91C') is derived from the deterministic hash and is safe
# for everyone: it is a stable label, not an identity. It is what a viewer sees
# where an analyst sees a name.
# =============================================================================

CustomerGrade = Literal["nuevo", "excelente", "bueno", "regular", "riesgo"]
OrderSort = Literal["recent", "contribution", "days_open"]
CustomerSort = Literal["orders", "contribution", "revenue", "recent"]


class OrderRow(BaseModel):
    """One guide, as the orders table renders it."""

    shipment_id: UUID
    tracking_number: str
    carrier_tracking_number: str | None
    created_date: date
    delivered_at: datetime | None
    status_code: str
    status_label: str
    status_bucket: str
    is_terminal: bool

    # The route key for /customers/{customer_hash}. Null on a guide that never
    # carried a phone or a document - there is nobody to group it with.
    customer_hash: str | None
    customer_ref: str | None = Field(
        default=None, description="Etiqueta estable del cliente. Visible para todos los roles."
    )
    customer_name: str | None = Field(
        default=None, description="Descifrado. Null para el rol viewer o sin llave configurada."
    )
    customer_phone: str | None = Field(
        default=None, description="Descifrado. Null para el rol viewer o sin llave configurada."
    )

    city_name: str | None
    province_name: str | None
    product_name: str | None
    quantity: int
    carrier_name: str | None

    revenue_amount: float | None
    freight_amount: float | None
    cogs_amount: float | None
    fee_amount: float | None
    contribution: float | None
    currency_code: str | None

    # Null once the guide is terminal: a closed guide is not open any number of days.
    days_open: int | None
    movement_count: int


class OrderDetailRow(OrderRow):
    """The same guide with the two fields only the detail card ever needs.

    Address and document are separate from name and phone on purpose: the table
    never shows them, so they are never decrypted for a list of 200 rows.
    """

    customer_address: str | None = None
    customer_document: str | None = None


class OrdersPage(BaseModel):
    rows: list[OrderRow]
    page: int
    page_size: int
    total: int
    pii_visible: bool = Field(
        description="False cuando el rol no puede ver datos de contacto, o cuando el "
        "despliegue no tiene llave de cifrado. Los campos vienen en null."
    )


class OrderTimelineEvent(BaseModel):
    """mart.v_order_timeline - a status change or a movement of money."""

    event_kind: Literal["estado", "dinero"]
    event_label: str
    amount: float | None
    currency_code: str | None
    event_date: date
    reference: str | None


class OrderDetail(BaseModel):
    order: OrderDetailRow
    timeline: list[OrderTimelineEvent]
    pii_visible: bool


class CustomerRow(BaseModel):
    """mart.v_customer_metrics - one person, however many guides they generated."""

    customer_hash: str
    customer_ref: str
    customer_name: str | None = None
    customer_phone: str | None = None

    orders: int
    delivered: int
    returned: int
    open_orders: int

    revenue: float | None
    contribution: float | None
    contribution_per_order: float | None

    # Over CLOSED orders only: an order still in transit is not a failed one.
    delivery_rate_pct: float | None
    customer_grade: CustomerGrade
    main_city: str | None
    first_order_date: date
    last_order_date: date
    days_since_last_order: int
    distinct_products: int
    currency_code: str | None


class CustomerDetailRow(CustomerRow):
    """The customer as the detail card shows them: contact data included.

    Separate from `CustomerRow` for the same reason `OrderDetailRow` is separate
    from `OrderRow` - the table must not be able to return these fields at all.
    Opening ONE customer to read their address is what an operator does before
    sending a parcel; decrypting two hundred addresses because someone scrolled
    a list is not the same act, and a shared model would make them one call
    away from each other.

    Each field resolves to the most recent guide that carried it, so a customer
    who corrected their address is seen corrected.
    """

    customer_address: str | None = Field(
        default=None, description="Descifrado. Null para viewer o sin llave configurada."
    )
    customer_document: str | None = Field(
        default=None, description="Descifrado. Null para viewer o sin llave configurada."
    )
    # NOT gated: a city is not an identity, and `main_city` has been visible to
    # every role since 015. They are here because a street line without a city
    # is not a usable address.
    customer_city: str | None = Field(
        default=None, description="Ciudad de la última guía. Visible para todos los roles."
    )
    customer_province: str | None = Field(
        default=None, description="Provincia/departamento de la última guía."
    )


class CustomersPage(BaseModel):
    rows: list[CustomerRow]
    page: int
    page_size: int
    total: int
    pii_visible: bool
    # Same three fields as `KpiResponse`, for the same reason: the clientes tab
    # shares the dashboard's date picker, and a page of customers filtered to a
    # window has to be able to say so.
    date_basis: DateBasis | None = None
    date_from: date | None = None
    date_to: date | None = None
    excluded_no_date: int | None = None


class CustomerDetail(BaseModel):
    customer: CustomerDetailRow
    orders: list[OrderRow]
    pii_visible: bool


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
    # Present when the alert was persisted (raw.notification); the panel uses
    # them to mark it read. Absent on the live, unstored detection.
    id: int | None = None
    country_code: str | None = None
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
    conversation_id: UUID | None = Field(
        default=None,
        description=(
            "Hilo al que pertenece la pregunta. Con él, un seguimiento como "
            "'¿y en Guayas?' se resuelve contra lo que ya se preguntó. Sin él se "
            "abre un hilo nuevo."
        ),
    )


class AskResponse(BaseModel):
    answer: str
    sql: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    rejected: bool = False
    rejection_reason: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    conversation_id: UUID | None = None
    message_id: UUID | None = Field(
        default=None, description="Identificador de la respuesta, para enviar feedback."
    )


class ConversationSummary(BaseModel):
    id: UUID
    country_code: str | None
    title: str | None
    message_count: int
    created_at: datetime
    last_message_at: datetime


class ConversationsResponse(BaseModel):
    conversations: list[ConversationSummary]


class MessageResponse(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    sql_executed: str | None = None
    row_count: int | None = None
    tokens: int = 0
    created_at: datetime
    helpful: bool | None = None
    feedback_comment: str | None = None


class ConversationDetail(BaseModel):
    id: UUID
    country_code: str | None
    title: str | None
    created_at: datetime
    last_message_at: datetime
    messages: list[MessageResponse]


class FeedbackRequest(BaseModel):
    message_id: UUID
    helpful: bool
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Por qué la respuesta estuvo mal. Con un comentario, un 'no me sirvió' "
            "se guarda como corrección y entra en las siguientes respuestas."
        ),
    )


class FeedbackResponse(BaseModel):
    stored: bool
    learned: bool = Field(
        description="True cuando el comentario quedó guardado como corrección duradera."
    )
    message: str


class RecommendationResponse(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    country_code: str | None
    title: str
    finding: str
    action: str
    impact_amount: float | None
    impact_currency: str | None
    deep_link: str
    detected_at: datetime


class RecommendationsResponse(BaseModel):
    country_code: str | None
    recommendations: list[RecommendationResponse]
    narrative: str | None = Field(
        default=None,
        description="Párrafo opcional del modelo sobre los hallazgos. Nunca los reemplaza.",
    )
    degraded: bool = False
    degraded_reason: str | None = None


# =============================================================================
# Notifications, events, decisions (migration 039)
# =============================================================================

NotificationKind = Literal["urgent", "digest", "system"]
Severity = Literal["info", "warning", "critical"]


class NotificationItem(BaseModel):
    id: int
    kind: NotificationKind
    code: str
    severity: Severity
    country_code: str | None
    title: str
    finding: str
    action: str
    impact_amount: float | None
    impact_currency: str | None
    deep_link: str | None
    created_at: datetime
    read_at: datetime | None
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationsResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    critical_unread_count: int
    next_before: int | None = Field(
        default=None,
        description="Pasa este id como `before` para traer la página anterior. Null al final.",
    )


class UnreadCountResponse(BaseModel):
    unread_count: int
    critical_unread_count: int


class ReadAllResponse(BaseModel):
    marked: int


THRESHOLD_KEYS: tuple[str, ...] = (
    "efectividad_tipica_pct",
    "flete_tipico",
    "dias_a_caja_tipicos",
    "alistamiento_tipico_dias",
)


class ThresholdRow(BaseModel):
    key: str
    value: str
    source: Literal["user", "inferred", "system"]
    confidence: float | None
    updated_at: datetime


class ThresholdsResponse(BaseModel):
    country_code: str
    thresholds: list[ThresholdRow]


class ThresholdsUpdateRequest(BaseModel):
    thresholds: dict[str, str] = Field(
        description=(
            "Clave -> valor. Solo se aceptan las cuatro claves conocidas. Un valor vacío "
            "borra el umbral fijado a mano y deja que se vuelva a inferir de los datos."
        )
    )


class EventItem(BaseModel):
    id: int
    type: str
    country_code: str | None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EventsResponse(BaseModel):
    cursor: int = Field(description="Pásalo como `since` en la siguiente llamada.")
    events: list[EventItem]


Verdict = Literal["keep", "cut", "watch", "switch", "call", "hold", "ok"]
DecisionScope = Literal["products", "carriers", "office", "cash"]


class DecisionItem(BaseModel):
    key: str
    label: str
    verdict: Verdict
    headline: str
    numbers: dict[str, Any] = Field(default_factory=dict)
    impact_amount: float | None
    impact_currency: str | None
    deep_link: str


class DecisionsResponse(BaseModel):
    scope: DecisionScope
    country_code: str
    items: list[DecisionItem]
    thresholds: dict[str, Any] = Field(default_factory=dict)
    narrative: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


class CarrierZoneRow(BaseModel):
    """mart.v_carrier_by_zone - delivery rate per carrier per city, last 90 days."""

    country_code: str
    level1_name: str
    city_name: str
    carrier_id: UUID | None
    carrier_name: str
    shipments: int
    terminal: int
    delivery_rate_pct: float | None
    avg_days_to_deliver: float | None
    avg_freight: float | None
    delivered_value: float | None
    currency_code: str | None
