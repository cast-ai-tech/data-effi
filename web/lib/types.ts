import type { CompanyType } from "@/lib/company";

import type { StatusGroup } from "@/lib/status";

/**
 * Types mirroring the API responses.
 *
 * Hand-written rather than generated so the shape stays readable, but they
 * follow api/schemas.py field for field. `npm run typecheck` will not catch a
 * drift here - the API integration tests will.
 */

/**
 * `uploader` is not a lesser `viewer`: it may upload files and may not see a
 * single number. Anything deciding what to render asks `capabilities`, never
 * compares roles.
 */
export type Role = "owner" | "analyst" | "viewer" | "uploader";
export type Capability = "read" | "ingest" | "config" | "manage";
export type WidgetState = "available" | "degraded" | "blocked";
export type TrafficLight = "verde" | "amarillo" | "rojo" | "sin_datos";

/** Fewer than ten closed guides behind a percentage: an estimate, not a measure. */
export type SampleQuality = "suficiente" | "muestra_corta";

/** `/ingest/detect`: what Master Data understood about a file before storing it. */
export interface DetectResult {
  filename: string;
  format: string;
  profile_code: string | null;
  profile_label: string | null;
  /** Migration 042: which platform the recognised report belongs to. */
  detected_platform_code: string | null;
  detected_platform_name: string | null;
  detected_country_code: string | null;
  detected_country_raw: string | null;
  row_count: number;
  column_count: number;
  mapped_columns: Record<string, string>;
  unmapped_columns: string[];
}

/**
 * `mart.f_daily_status` (migration 040): one day, one platform, the guides
 * by screen group. Two return percentages on purpose - `pct_devolucion_total`
 * is what the operator's hand-made report prints; `pct_devolucion_cerradas`
 * is the one that stays true once the day's guides close.
 */
export interface DailyStatusRow {
  country_code: string;
  platform_code: string;
  platform_name: string;
  day: string;
  shipments: number;
  entregada: number;
  devolucion: number;
  en_transito: number;
  novedad: number;
  indemnizacion: number;
  cerradas: number;
  pct_entrega_cerradas: number | null;
  pct_devolucion_cerradas: number | null;
  pct_devolucion_total: number | null;
  sample_quality: SampleQuality;
  declared_value: number | null;
  revenue: number | null;
  contribution: number | null;
  currency_code: string | null;
}

/** `mart.f_platform_summary`: one row per platform, with its share of the guides. */
export interface PlatformSummaryRow {
  country_code: string;
  platform_code: string;
  platform_name: string;
  shipments: number;
  entregada: number;
  devolucion: number;
  en_transito: number;
  novedad: number;
  indemnizacion: number;
  cerradas: number;
  pct_entrega_cerradas: number | null;
  pct_devolucion_cerradas: number | null;
  pct_devolucion_total: number | null;
  share_pct: number | null;
  sample_quality: SampleQuality;
  declared_value: number | null;
  revenue: number | null;
  contribution: number | null;
  currency_code: string | null;
  first_day: string | null;
  last_day: string | null;
}

/** One company the person may open, and the grant that applies inside it. */
export interface Workspace {
  tenant_id: string;
  name: string;
  slug: string;
  role: Role;
  /** Countries the company operates in. */
  countries: string[];
  /** If set, the only countries THIS person may read there. */
  country_scope: string[] | null;
  share_pct: number | null;
  /** Tienda (dropshipping / propia / mixta) o proveedor; null = sin definir. */
  company_type?: CompanyType | null;
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  /** The company this token stands in. Null only for an admin with no membership. */
  tenant_id: string | null;
  tenant_name: string | null;
  role: Role | null;
  is_org_admin: boolean;
  countries: string[] | null;
  workspaces: Workspace[];
}

export type OrgRole = "admin" | "analyst" | "viewer";

/** Un refresh token vivo: un navegador o dispositivo que todavía puede volver. */
export interface Session {
  id: string;
  tenant_id: string | null;
  tenant_name: string | null;
  created_at: string;
  expires_at: string;
}

/** Una moneda contra el dólar y contra el peso colombiano. */
export interface FxRate {
  currency_code: string;
  currency_symbol: string;
  decimal_places: number;
  country_codes: string[];
  country_names: string[];
  /** Cuánto vale UNA unidad de esta moneda. */
  to_usd: number | null;
  to_cop: number | null;
  /** Cuántas unidades cuesta un dólar: así se cotiza hablando. */
  per_usd: number | null;
  rate_date: string | null;
  source: string | null;
  has_rate: boolean;
  is_stale: boolean | null;
}

export interface OrgMember {
  user_id: string;
  email: string;
  full_name: string | null;
  role: OrgRole;
  is_active: boolean;
  last_login_at: string | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: Role;
  tenant_id: string | null;
  tenant_name: string | null;
  created_at: string;
  org_id: string | null;
  org_name: string | null;
  is_org_admin: boolean;
  /** admin | analyst | viewer sobre el holding. Distinto de `role`, que aplica
   *  dentro de la sociedad donde estás parado. */
  org_role: OrgRole | null;
  countries: string[] | null;
  capabilities: Capability[];
  workspaces: Workspace[];
  /** The free month / plan of the organisation (migration 048). Absent only
   *  in fixtures older than the field. */
  subscription?: SubscriptionState | null;
}

// ---------------------------------------------------------------------------
// Plans: a free month, then Master / Master Pro / Master Elite, or a custom
// deal with an advisor. Billing is manual: `pending` until an advisor activates.
// ---------------------------------------------------------------------------

export interface Plan {
  code: string;
  name: string;
  /** Null = negotiated (custom plan). */
  price_usd: number | null;
  /** Null = no limit (custom plan). */
  max_tenants: number | null;
  is_custom: boolean;
}

export type SubscriptionStatus = "trial" | "pending" | "active" | "expired";

export interface SubscriptionState {
  status: SubscriptionStatus;
  plan_code: string | null;
  plan_name: string | null;
  requested_plan_code: string | null;
  requested_plan_name: string | null;
  trial_ends_at: string;
  current_period_end: string | null;
  days_left: number | null;
  max_tenants: number | null;
  tenants_used: number;
  /** True = the API answers 402 to every data endpoint. */
  blocked: boolean;
  message: string;
}

export interface BillingResponse {
  plans: Plan[];
  subscription: SubscriptionState;
  advisor_whatsapp_url: string | null;
  can_choose: boolean;
}

// ---------------------------------------------------------------------------
// Organization: the holding above the companies. Money is in `base_currency`.
// ---------------------------------------------------------------------------

export interface OrgTenantRow {
  tenant_id: string;
  name: string;
  slug: string;
  countries: string[];
  shipments: number;
  delivered: number;
  delivery_rate_pct: number | null;
  revenue_usd: number | null;
  ad_spend_usd: number | null;
  contribution_usd: number | null;
  share_pct: number | null;
  my_share_usd: number | null;
  fx_missing: boolean;
  last_shipment_date: string | null;
}

export interface OrgCountryRow {
  country_code: string;
  country_name: string;
  shipments: number;
  delivered: number;
  delivery_rate_pct: number | null;
  revenue_usd: number | null;
  contribution_usd: number | null;
  tenants: string[];
}

export interface OrgTotals {
  shipments: number;
  delivered: number;
  delivery_rate_pct: number | null;
  revenue_usd: number | null;
  ad_spend_usd: number | null;
  contribution_usd: number | null;
  my_share_usd: number | null;
}

export interface OrgSummary {
  org_id: string;
  org_name: string;
  base_currency: string;
  date_from: string | null;
  date_to: string | null;
  date_basis: string | null;
  totals: OrgTotals;
  by_tenant: OrgTenantRow[];
  by_country: OrgCountryRow[];
  /** Companies that could not be read; their numbers are NOT in the totals. */
  unavailable: string[];
}

/** Un país que la plataforma sabe operar, para armar el organigrama. */
export interface SupportedCountry {
  code: string;
  name: string;
  currency_code: string;
}

export interface TenantRow {
  tenant_id: string;
  name: string;
  slug: string;
  countries: string[];
  member_count: number;
  notes: string | null;
  company_type?: CompanyType | null;
  created_at: string;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string | null;
  role: Role;
  country_scope: string[] | null;
  share_pct: number | null;
  /** ecommerce | proveeduria, or null while the admin has not said (migration 046). */
  business_model: "ecommerce" | "proveeduria" | null;
  /** The company this access belongs to. */
  tenant_id: string | null;
  tenant_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
}

export interface InviteResult {
  id: string;
  email: string;
  role: Role;
  /** Null when the person already had an account: access is immediate. */
  invitation_token: string | null;
  expires_at: string;
  already_registered: boolean;
  country_scope: string[] | null;
  share_pct: number | null;
}

/** Everything the UI needs to format a number or a date for one country. */
export interface Country {
  code: string;
  name: string;
  currency_code: string;
  currency_symbol: string;
  decimal_places: number;
  thousands_sep: string;
  decimal_sep: string;
  date_format: string;
  locale: string;
  timezone: string;
  geo_level1_label: string;
  is_active: boolean;
  maturation_days: number | null;
  maturation_days_suggested: number | null;
}

/** Where a platform lives: one connection per country, or one per workspace. */
export type PlatformScope = "country" | "global";

/** Catalogue shelf. Drives the headings on the connections screen. */
export type PlatformCategory =
  | "pauta"
  | "tienda"
  | "crm"
  | "fulfillment"
  | "automatizacion"
  | "archivos"
  | "analitica"
  | "otros";

/** What the platform needs to prove who you are. */
export type PlatformAuthType =
  | "none"
  | "file"
  | "api_key"
  | "oauth2"
  | "session"
  | "webhook";

/**
 * Whether it works TODAY.
 *
 * `planned` rows are listed and refused on creation: hiding them would make the
 * roadmap invisible, which is worse than a greyed-out card.
 */
export type PlatformAvailability = "available" | "beta" | "planned";

/** Data coming in, going out, or both. */
export type PlatformDirection = "in" | "out" | "both";

/**
 * One row of `mart.v_platform_catalogue` - the whole catalogue, country or not.
 *
 * `GET /config/platforms` with no `country` returns these.
 */
export interface Platform {
  platform_code: string;
  platform_name: string;
  tier: number;
  scope: PlatformScope;
  category: PlatformCategory;
  auth_type: PlatformAuthType;
  availability: PlatformAvailability;
  direction: PlatformDirection;
  data_domains: string[];
  requires_consent: boolean;
  setup_hint: string | null;
  docs_url: string | null;
  /** Countries where it can be connected. Empty for global platforms. */
  available_countries: string[];
}

/**
 * The same platform seen from one country: `GET /config/platforms?country=XX`.
 *
 * Only this variant knows whether anything is already connected, so the counts
 * live here rather than being optional on `Platform`.
 */
export interface CountryPlatform extends Platform {
  connection_count: number;
  active_connection_count: number;
  is_connected: boolean;
}

export interface Connection {
  connection_id: string;
  connection_name: string;
  /** NULL for a global connection: the file declares its own country. */
  country_code: string | null;
  platform_code: string;
  platform_name: string;
  tier: number;
  scope: PlatformScope;
  category: PlatformCategory;
  status: string;
  health: "ok" | "stale" | "error" | "never_synced" | "disabled";
  consent_granted_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  hours_since_sync: number | null;
  batches_7d: number | null;
  failed_batches_7d: number | null;
  /** True once a webhook token exists. The token itself is never returned. */
  has_webhook: boolean;
}

// ---------------------------------------------------------------------------
// Connecting a platform account (migration 051)
//
// A password goes UP through `ConnectionCredentialBody` and there is no type
// here that brings one back down. That asymmetry is the design, not an
// oversight: see api/credentials.py. If you ever need to add a `password` field
// to a response type, the answer is no.
// ---------------------------------------------------------------------------

export type CredentialStatus =
  | "none"
  | "ok"
  | "invalid"
  | "expired"
  | "insufficient_permissions"
  | "locked";

/** `PUT /config/connections/{id}/credential`. Owner only, write-only. */
export interface ConnectionCredentialBody {
  username: string;
  password: string;
  consent_granted: boolean;
}

/** What the screen may know about a stored credential. Never the password. */
export interface ConnectionCredential {
  connection_id: string;
  username: string;
  credential_status: CredentialStatus;
  last_login_at: string | null;
  last_login_error: string | null;
  session_expires_at: string | null;
  rotated_at: string | null;
  message: string;
}

/** One permission we ask for on the source platform, and whether we have it. */
export interface ConnectionPermission {
  permission_code: string;
  permission_name: string;
  /** Always read-only: "consultar" and/or "ver_reportes". */
  actions: string[];
  /** What the merchant loses by not granting it. This is the useful half. */
  why: string;
  requirement: "required" | "optional";
  admin_only: boolean;
  status: "unknown" | "granted" | "denied" | "unreachable";
  detail: string | null;
  checked_at: string | null;
}

// ---------------------------------------------------------------------------
// El buzón de capturas (migración 052)
//
// Lo que manda la extensión de tools/effi-capture: rutas y nombres de campos.
// Nunca una contraseña ni una cookie - la extensión no las lee y el motor las
// rechaza si llegaran.
// ---------------------------------------------------------------------------

/** El contrato de login tal como llegó. Todo son nombres, ningún valor. */
export interface CaptureContract {
  base?: string;
  ruta?: string;
  campoUsuario?: string;
  campoClave?: string;
  campoCsrf?: string;
  carrier?: "cookie" | "json" | "";
  carrierNombre?: string;
  estado?: number | null;
  otrosCampos?: string[];
  exportaciones?: Array<{
    metodo?: string;
    ruta?: string;
    params?: string;
    estado?: number | null;
    tipo?: string;
  }>;
}

/** Una captura recibida. `GET /captures`. */
export interface CaptureRow {
  id: number;
  platform_code: string;
  platform_name: string;
  /** Cómo la etiquetaste al invitar: "Juan, de Distrilatam". */
  invited_label: string | null;
  source: "extension" | "analizador" | "manual";
  /** False = la persona grabó tarde y hay que pedirle que repita. */
  found_login: boolean;
  export_count: number;
  contract: CaptureContract;
  base_url: string | null;
  login_path: string | null;
  created_at: string;
  reviewed_at: string | null;
  is_new: boolean;
}

/**
 * Una organización, como la ve quien opera la plataforma. `GET /captures/orgs`.
 *
 * Fíjate en lo que NO tiene: ni ventas, ni guías, ni dinero. Ese vacío es
 * deliberado (migración 053) — el tablero de una empresa es de su dueño.
 */
export interface PlatformOrgRow {
  org_id: string;
  org_name: string;
  slug: string;
  created_at: string;
  subscription_status: string;
  plan_code: string | null;
  plan_name: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  tenant_count: number;
  user_count: number;
  /** Salud operativa, no negocio. */
  connection_count: number;
  connection_errors: number;
}

/** `POST /captures/tokens`. El código se muestra una sola vez. */
export interface CaptureToken {
  token_id: string;
  token: string;
  label: string;
  platform_code: string;
  platform_name: string;
  /** La URL que va en el paquete: lleva el código dentro. */
  submit_url: string;
  max_uses: number;
  expires_at: string;
  created_at: string;
  message: string;
}

/** `GET .../permissions` and `POST .../test` answer with the same shape. */
export interface ConnectionPreflight {
  connection_id: string;
  credential_status: CredentialStatus;
  /** False only when a REQUIRED permission is missing or the login failed. */
  is_usable: boolean;
  /** One line that says what to do next, not what went wrong. */
  summary: string;
  permissions: ConnectionPermission[];
}

/** What a webhook connection accepts, when the caller does not say. */
export type WebhookKind = "shipments" | "movements" | "ads" | "cs";

/**
 * `POST /config/connections/{id}/webhook`, returned exactly once. Owner only.
 *
 * The database keeps only the SHA-256, so this is the only moment the token
 * exists in readable form anywhere: there is no endpoint to read it back,
 * deliberately, exactly like a refresh token. `url` already carries the token
 * in its path (the receiver is POST /ingest/webhook/{token}), which makes the
 * URL itself a credential - it must never reach a log, an analytics call, the
 * page title or the browser history.
 */
export interface WebhookSecret {
  connection_id: string;
  token: string;
  url: string;
  default_kind: WebhookKind | null;
  created_at: string;
  /** Spanish copy written by the API. Show it as it comes. */
  message: string;
}

export interface UploadJob {
  id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  status: "queued" | "processing" | "done" | "failed" | "duplicate";
  batch_id: string | null;
  error: string | null;
  queued_at: string;
  finished_at: string | null;
}

export interface BatchSummary {
  batch_id: string;
  connection_id: string;
  connection_name: string;
  country_code: string;
  platform_code: string;
  source_name: string;
  kind: string;
  status: string;
  rows_total: number;
  rows_inserted: number;
  rows_updated: number;
  rows_skipped: number;
  rows_failed: number;
  discrepancy_count: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface BatchDetail {
  batch: BatchSummary;
  report: {
    already_loaded?: boolean;
    rows?: Record<string, number>;
    discrepancies?: Array<{
      entity: string;
      entity_key: string;
      field: string;
      old: string | null;
      new: string | null;
    }>;
    sanity_issues?: Array<{
      row: number;
      entity_key: string;
      code: string;
      message: string;
      severity: string;
    }>;
    errors?: Array<{ row: number; message: string }>;
    unmapped_columns?: string[];
    /** Batch-wide degradations that cost no rows, e.g. a missing encryption key. */
    warnings?: string[];
    /** Which known report shape the file matched, if any. */
    profile?: { code: string | null; label: string | null };
  };
  discrepancies: Array<{
    entity: string;
    entity_key: string;
    field_name: string;
    old_value: string | null;
    new_value: string | null;
    detected_at: string;
  }>;
}

export interface DailyContribution {
  country_code: string;
  store_id: string | null;
  store_name: string | null;
  day: string;
  shipments: number;
  delivered: number;
  returned: number;
  in_transit: number;
  dead: number;
  declared_value: number | null;
  revenue: number | null;
  freight: number | null;
  cogs: number | null;
  fees: number | null;
  adjustments: number | null;
  ad_spend: number | null;
  contribution: number | null;
  contribution_margin_pct: number | null;
  delivery_rate_terminal_pct: number | null;
  delivery_rate_dispatched_pct: number | null;
  currency_code: string | null;
  ad_spend_missing: boolean;
}

export interface CarrierRow {
  country_code: string;
  carrier_id: string | null;
  carrier_name: string;
  shipments: number;
  delivered: number;
  returned: number;
  in_transit: number;
  delivery_rate_pct: number | null;
  return_rate_pct: number | null;
  avg_days_to_deliver: number | null;
  p90_days_to_deliver: number | null;
  freight_total: number | null;
  avg_freight_per_shipment: number | null;
  revenue: number | null;
  contribution: number | null;
  currency_code: string | null;
  /**
   * `muestra_corta` = fewer than 10 terminal guides behind those percentages
   * (migration 021). The rates are still sent - a blank row explains nothing -
   * but they must not be presented as measured.
   *
   * Optional because `api/schemas.py::CarrierRow` does not declare the field
   * yet, so it never reaches the wire. The marker lights up on its own the day
   * it does.
   */
  sample_quality?: "suficiente" | "muestra_corta" | null;
  /** Migration 049: the dispatched basis and the realised contribution. */
  closed_shipments?: number | null;
  delivery_rate_dispatched_pct?: number | null;
  return_rate_dispatched_pct?: number | null;
  realised_contribution?: number | null;
  capital_in_street?: number | null;
}

export interface GeoRow {
  country_code: string;
  geo_id: string | null;
  level1_name: string;
  city_name: string;
  shipments: number;
  delivered: number;
  returned: number;
  in_transit: number;
  delivery_rate_pct: number | null;
  revenue: number | null;
  contribution: number | null;
  avg_days_to_deliver: number | null;
  traffic_light: TrafficLight;
  currency_code: string | null;
}

export interface ProductRow {
  country_code: string;
  product_id: string | null;
  product_name: string;
  sku: string | null;
  supplier_name: string | null;
  shipments: number;
  units: number | null;
  delivered: number;
  returned: number;
  delivery_rate_pct: number | null;
  revenue: number | null;
  cogs: number | null;
  freight: number | null;
  contribution: number | null;
  contribution_per_shipment: number | null;
  margin_pct: number | null;
  currency_code: string | null;
}

export interface CohortRow {
  country_code: string;
  cohort_date: string;
  cohort_size: number;
  days_since: number;
  delivered_by_day: number;
  delivery_rate_pct: number | null;
  is_observable: boolean;
  is_mature: boolean;
  maturation_days: number;
}

export interface AgingRow {
  country_code: string;
  aging_bucket: string;
  bucket_order: number;
  shipments: number;
  value_at_risk: number | null;
  avg_days_open: number | null;
  currency_code: string | null;
}

export interface CsRow {
  country_code: string;
  day: string;
  interactions: number;
  confirmed: number;
  rejected: number;
  no_answer: number;
  pending: number;
  confirmation_rate_pct: number | null;
  avg_attempts: number | null;
}

export interface CpaRow {
  country_code: string;
  day: string;
  ad_spend: number;
  impressions: number;
  clicks: number;
  shipments: number;
  delivered: number;
  revenue: number;
  cpa_dispatched: number | null;
  cpa_delivered: number | null;
  roas: number | null;
  currency_code: string | null;
}

export interface GlobalRow {
  country_code: string;
  country_name: string;
  currency_code: string | null;
  shipments: number;
  delivered: number;
  returned: number;
  in_transit: number;
  delivery_rate_pct: number | null;
  revenue: number | null;
  ad_spend: number | null;
  contribution: number | null;
  fx_rate_to_usd: number | null;
  fx_rate_date: string | null;
  contribution_usd: number | null;
  fx_missing: boolean;
  last_shipment_date: string | null;
  /** The five status groups (migration 045). `in_transit` is every open guide;
   * `en_transito` only the ones actually moving. */
  entregada: number;
  devolucion: number;
  en_transito: number;
  novedad: number;
  indemnizacion: number;
}

export interface LayoutWidget {
  widget_code: string;
  tab: string;
  title: string;
  description: string;
  sort_order: number;
  /** Cuántas columnas ocupa: 1 o 2. Lo guarda cada persona (migración 057). */
  width?: number;
  hidden?: boolean;
  state: WidgetState;
  state_message: string | null;
  required_domains: string[];
  optional_domains: string[];
  missing_required: string[];
  missing_optional: string[];
  awaiting_data: string[];
}

export interface LayoutResponse {
  country_code: string;
  widgets: LayoutWidget[];
  /** Si esta persona ya acomodó su tablero (migración 057). */
  customised?: boolean;
}

export interface Brief {
  country_code: string;
  generated_at: string;
  summary: string;
  cached: boolean;
  degraded: boolean;
  degraded_reason: string | null;
}

export interface Alert {
  code: string;
  severity: "info" | "warning" | "critical";
  title: string;
  finding: string;
  impact_amount: number | null;
  impact_currency: string | null;
  action: string;
  deep_link: string;
  detected_at: string;
}

export interface AskResult {
  answer: string;
  sql: string | null;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  row_count: number;
  rejected: boolean;
  rejection_reason: string | null;
  suggestions: string[];
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail: Record<string, unknown>;
  };
}

// ---------------------------------------------------------------------------
// Dropshipping metrics (migration 009) + cash cycle (migration 008).
//
// One interface per view, column for column. `tenant_id` is stripped by the
// API - it is the auth context, never payload.
// ---------------------------------------------------------------------------

/** `mart.v_dropshipping_margin`: charged, paid to the supplier, what is left. */
export interface DropshippingMarginRow {
  country_code: string;
  product_id: string | null;
  product_name: string;
  sku: string | null;
  supplier_name: string;
  shipments: number;
  delivered: number;
  units: number | null;
  revenue: number | null;
  supplier_cost: number | null;
  freight: number | null;
  gross_margin: number | null;
  gross_margin_pct: number | null;
  net_contribution: number | null;
  contribution_per_shipment: number | null;
  cost_of_undelivered: number | null;
  /** Below this delivery rate the product loses money on every dispatch. */
  breakeven_delivery_pct: number | null;
  delivery_rate_pct: number | null;
  catalogue_cost: number | null;
  catalogue_price: number | null;
  /** False when no human ever confirmed the cost: it is inferred from reports. */
  catalogue_reviewed: boolean;
  observed_unit_cost: number | null;
  currency_code: string | null;
}

/** `mart.v_fulfillment_sla`: the delivery clock split into yours and theirs. */
export interface FulfillmentSlaRow {
  country_code: string;
  carrier_id: string | null;
  carrier_name: string;
  service_level: string;
  shipments: number;
  delivered: number;
  avg_prep_days: number | null;
  p50_prep_days: number | null;
  p90_prep_days: number | null;
  avg_transit_days: number | null;
  p90_transit_days: number | null;
  avg_total_days: number | null;
  /** How much of the total wait happened before the parcel even left. */
  prep_share_pct: number | null;
  on_time_count: number;
  measurable_count: number;
  on_time_pct: number | null;
}

/** `mart.v_office_rescue`: guides waiting at a carrier office, by city. */
export interface OfficeRescueRow {
  country_code: string;
  carrier_name: string;
  level1_name: string;
  city_name: string;
  shipments: number;
  value_waiting: number | null;
  avg_days_waiting: number | null;
  fresh_0_7: number;
  aging_8_14: number;
  urgent_15_21: number;
  probably_lost: number;
  /** The 8-21 day window: old enough to be at risk, young enough to rescue. */
  value_still_recoverable: number | null;
  currency_code: string | null;
}

/** `mart.v_freight_analysis`: freight per kilo, by component and discount. */
export interface FreightAnalysisRow {
  country_code: string;
  carrier_id: string | null;
  carrier_name: string;
  service_level: string;
  shipments: number;
  avg_weight_kg: number | null;
  total_weight_kg: number | null;
  freight_total: number | null;
  avg_freight: number | null;
  /** The only comparable figure: a heavier parcel is not a dearer carrier. */
  freight_per_kg: number | null;
  avg_freight_base: number | null;
  avg_handling: number | null;
  avg_collection_fee: number | null;
  avg_discount_pct: number | null;
  discount_value: number | null;
  freight_share_of_value_pct: number | null;
  return_freight_total: number | null;
  currency_code: string | null;
}

/** `mart.v_cash_cycle`: days from dispatch until the money is spendable. */
export interface CashCycleRow {
  country_code: string;
  settled: number;
  /** Delivered, the carrier holds the cash, it is not in your account yet. */
  delivered_unsettled: number;
  avg_days_to_cash: number | null;
  p50_days_to_cash: number | null;
  p90_days_to_cash: number | null;
  cash_in_transit: number | null;
  currency_code: string | null;
}

/**
 * What the catalogue is missing, per product.
 *
 * `sin_costo` -> no cost at all; `sin_revisar` -> a cost nobody confirmed;
 * `costo_desactualizado` -> the catalogue and the reports disagree by >10%.
 */
export type CatalogueStatus =
  | "sin_costo"
  | "sin_revisar"
  | "costo_desactualizado"
  | "ok";

/** `mart.v_product_catalogue`: the catalogue next to what the reports observed. */
export interface ProductCatalogueRow {
  product_id: string;
  product_name: string;
  sku: string | null;
  category: string | null;
  supplier_name: string | null;
  unit_cost: number | null;
  list_price: number | null;
  target_margin_pct: number | null;
  weight_kg: number | null;
  currency_code: string | null;
  is_active: boolean;
  reviewed_at: string | null;
  notes: string | null;
  shipments: number;
  delivered: number;
  last_shipment_date: string | null;
  observed_unit_cost: number | null;
  catalogue_status: CatalogueStatus;
  catalogue_margin_pct: number | null;
}

/** Who wrote a cost: a person, a file, or the guides themselves. */
export type ProductCostSource = "manual" | "import" | "observed";

/** One row of `core.product_cost_history`, newest first from the API. */
export interface ProductCostEntry {
  id: number;
  unit_cost: number;
  currency_code: string | null;
  source: ProductCostSource;
  changed_by: string | null;
  changed_at: string;
}

/**
 * GET /products/{id}: the row plus how its cost moved over time.
 *
 * The history is the only way to see a supplier quietly raising a price - the
 * catalogue alone shows today's number and forgets it ever changed.
 */
export interface ProductDetail {
  product: ProductCatalogueRow;
  cost_history: ProductCostEntry[];
}

/**
 * Body for POST /products and PATCH /products/{id}.
 *
 * `supplier_name` is text, not an id: the operator types the supplier the way
 * it appears on the invoice, and the API resolves it (get-or-create).
 *
 * PATCH is keyed on which fields are PRESENT, not on their values:
 *
 * - a field left out is not touched
 * - a field sent as `null` is cleared
 *
 * So a PATCH must carry only what the operator actually changed. Serialising
 * the whole form would send nulls for boxes nobody opened and wipe them.
 *
 * `reviewed_at` is set server-side, and SENDING `unit_cost` IS what marks the
 * product reviewed - which makes it the one field worth re-sending unchanged,
 * when the operator is deliberately confirming the number.
 *
 * `name` and `is_active` are NOT NULL: send them changed or not at all.
 * `is_active` is PATCH-only - a product is born active, and `{is_active: false}`
 * archives it.
 */
export interface ProductWrite {
  name?: string;
  sku?: string | null;
  category?: string | null;
  supplier_name?: string | null;
  unit_cost?: number | null;
  list_price?: number | null;
  target_margin_pct?: number | null;
  weight_kg?: number | null;
  currency_code?: string | null;
  notes?: string | null;
  /** PATCH only. `false` archives the product, `true` restores it. */
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Orders, customers and the honest contribution split (migration 015).
//
// Contact columns are ciphertext in the database. The API decrypts them and
// answers with `pii_visible`, so the UI is told WHY a name is missing instead
// of having to guess from a null.
// ---------------------------------------------------------------------------

/** The four ends a guide can reach. `pipeline` is the only non-terminal one. */
export type StatusBucket = "pipeline" | "delivered" | "returned" | "dead";

/**
 * One row of `mart.v_orders`, after the API decrypted what it was allowed to.
 *
 * `customer_name` and `customer_phone` are null EITHER because the server
 * would not decrypt them (see `pii_visible` on the envelope) OR because that
 * guide never carried them. `customer_ref` survives both cases: it is derived
 * from the deterministic hash, so it is the same label across every order of
 * the same person.
 */
export interface OrderRow {
  shipment_id: string;
  tracking_number: string;
  carrier_tracking_number: string | null;
  created_date: string;
  delivered_at: string | null;
  status_code: string;
  status_label: string;
  status_bucket: StatusBucket;
  /** One of the five words (migration 045). */
  status_group: StatusGroup;
  is_terminal: boolean;
  /**
   * The deterministic hash, and the only key that routes to a customer:
   * `customer_ref` is six characters for a human to read, not an identifier.
   */
  customer_hash: string | null;
  customer_ref: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  city_name: string | null;
  province_name: string | null;
  product_name: string | null;
  quantity: number | null;
  carrier_name: string | null;
  revenue_amount: number | null;
  freight_amount: number | null;
  cogs_amount: number | null;
  fee_amount: number | null;
  contribution: number | null;
  currency_code: string | null;
  days_open: number | null;
  movement_count: number | null;
}

/**
 * The two extra fields ONLY `GET /orders/{shipment_id}` returns.
 *
 * They are absent from the list rows entirely - not even as null - so that a
 * page of two hundred guides never decrypts two hundred addresses. Nothing
 * that renders an `OrderRow` may reach for them.
 */
export interface OrderDetailRow extends OrderRow {
  customer_address: string | null;
  customer_document: string | null;
}

/** A status change or a movement of money. Both live on the same thread. */
export type TimelineEventKind = "estado" | "dinero";

export interface TimelineEvent {
  event_kind: TimelineEventKind;
  event_label: string;
  amount: number | null;
  currency_code: string | null;
  event_date: string;
  reference: string | null;
}

export interface OrderDetail {
  order: OrderDetailRow;
  timeline: TimelineEvent[];
  /**
   * Repeated here, not inherited from the list.
   *
   * Without it the card cannot tell "you may not see this phone" from "this
   * guide never carried one", and those are different sentences for the
   * operator: one is a permission, the other is a gap in the upload.
   */
  pii_visible: boolean;
}

/**
 * A server-paginated list.
 *
 * `pii_visible` belongs to the envelope, not the row: it is a statement about
 * this reader and this server, identical for every row in the page.
 */
export interface Paged<T> {
  rows: T[];
  page: number;
  page_size: number;
  total: number;
  pii_visible: boolean;
}

export type OrdersPage = Paged<OrderRow>;
export type CustomersPage = Paged<CustomerRow>;

/**
 * A grade, never a score out of a hundred.
 *
 * `nuevo` is not a compliment or a complaint: with fewer than two closed
 * orders there is no basis for either, and saying so is more useful than
 * inventing a 50%.
 */
export type CustomerGrade =
  | "nuevo"
  | "excelente"
  | "bueno"
  | "regular"
  | "riesgo";

/** One row of `mart.v_customer_metrics`: one person, in one country. */
export interface CustomerRow {
  customer_hash: string;
  customer_ref: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  orders: number;
  delivered: number;
  returned: number;
  open_orders: number;
  revenue: number | null;
  contribution: number | null;
  contribution_per_order: number | null;
  delivery_rate_pct: number | null;
  customer_grade: CustomerGrade;
  main_city: string | null;
  first_order_date: string | null;
  last_order_date: string | null;
  days_since_last_order: number | null;
  distinct_products: number | null;
  currency_code: string | null;
}

/**
 * The customer as `GET /customers/{customer_hash}` returns them.
 *
 * Address and document exist ONLY here, for the same reason an order's do: a
 * page of fifty customers must not decrypt fifty addresses to render a table
 * that shows none of them.
 */
export interface CustomerDetailRow extends CustomerRow {
  customer_address: string | null;
  customer_document: string | null;
}

export interface CustomerDetail {
  customer: CustomerDetailRow;
  /** The most recent guides, capped server-side at `CUSTOMER_ORDERS_CAP`. */
  orders: OrderRow[];
  pii_visible: boolean;
}

/**
 * `mart.v_contribution_split`: what closed, next to what is still out.
 *
 * `net_contribution` is deliberately NOT the headline anywhere in the UI. It
 * sums a matured cohort with one that has charged its costs and collected
 * nothing yet, which reads as a loss whenever the young cohort is large - a
 * statement about timing, not about profitability.
 */
export interface ContributionSplit {
  country_code: string;
  currency_code: string | null;
  shipments: number;
  closed_shipments: number;
  open_shipments: number;
  realised_revenue: number | null;
  realised_cost: number | null;
  realised_contribution: number | null;
  realised_margin_pct: number | null;
  capital_in_street: number | null;
  committed_revenue: number | null;
  net_contribution: number | null;
  maturity_pct: number | null;
}

// ---------------------------------------------------------------------------
// Notifications, live events and decisions (migration 039).
//
// The bell reads `Notification`; the long-poll reads `EventsResponse`; the
// decision strips read `Decision`. None of them is date-ranged: a decision is
// about the operation as it stands, not about the window the reader picked.
// ---------------------------------------------------------------------------

/** `urgent` lands the moment a load finishes; `digest` is the 7 am summary. */
export type NotificationKind = "urgent" | "digest" | "system";
export type NotificationSeverity = "info" | "warning" | "critical";

/**
 * What a digest carries. The recommendations and alerts are the same shape the
 * copilot panel already renders, minus `detected_at`, so one card serves both.
 */
export interface NotificationPayload {
  brief?: string;
  recommendations?: AlertLike[];
  alerts?: AlertLike[];
  [key: string]: unknown;
}

/** An alert as it travels inside a notification payload: no detection time. */
export type AlertLike = Omit<Alert, "detected_at"> & {
  detected_at?: string;
  deep_link: string | null;
};

export interface Notification {
  id: number;
  kind: NotificationKind;
  code: string;
  severity: NotificationSeverity;
  country_code: string | null;
  title: string;
  finding: string;
  action: string;
  impact_amount: number | null;
  impact_currency: string | null;
  deep_link: string | null;
  created_at: string;
  /** Null until THIS reader opened it: the state is per person, not per row. */
  read_at: string | null;
  payload: NotificationPayload;
}

export interface NotificationsResponse {
  items: Notification[];
  unread_count: number;
  critical_unread_count: number;
  /** Pass as `before` to fetch the next, older page. Null on the last page. */
  next_before: number | null;
}

export interface UnreadCount {
  unread_count: number;
  critical_unread_count: number;
}

/** A learned threshold, or one the operator typed over it (`source = "user"`). */
export interface Threshold {
  key: string;
  value: string;
  source: string;
  confidence: number | null;
  updated_at: string | null;
}

export interface ThresholdsResponse {
  country_code: string;
  thresholds: Threshold[];
}

/** One row of `raw.event`, as the long-poll hands it over. */
export interface LiveEvent {
  id: number;
  type: string;
  country_code: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

/** `GET /events`. Without `since` only `cursor` is meaningful and `events` is empty. */
export interface EventsResponse {
  cursor: number;
  events: LiveEvent[];
}

export type DecisionScope = "products" | "carriers" | "office" | "cash";

/**
 * The verb. `cut/call/switch` ask for an action, `keep/ok` say leave it alone,
 * `watch/hold` say not yet.
 */
export type Verdict =
  | "keep"
  | "cut"
  | "watch"
  | "switch"
  | "call"
  | "hold"
  | "ok";

export interface Decision {
  key: string;
  label: string;
  verdict: Verdict;
  headline: string;
  /** Scope-specific figures: `product_id`, `best_carrier`, `p50`... */
  numbers: Record<string, unknown>;
  impact_amount: number | null;
  impact_currency: string | null;
  deep_link: string | null;
}

export interface DecisionsResponse {
  scope: DecisionScope;
  country_code: string;
  items: Decision[];
  thresholds: Record<string, unknown>;
  /** Only with `narrative=true`, and only if the model answered. */
  narrative: string | null;
  degraded: boolean;
  degraded_reason: string | null;
}

/** `mart.v_carrier_by_zone`: the same carrier measured city by city, last 90 days. */
export interface CarrierZoneRow {
  country_code: string;
  level1_name: string;
  city_name: string;
  carrier_id: string | null;
  carrier_name: string;
  shipments: number;
  /** Delivered or returned: the guides that already have an outcome. */
  terminal: number;
  delivery_rate_pct: number | null;
  avg_days_to_deliver: number | null;
  avg_freight: number | null;
  delivered_value: number | null;
  currency_code: string | null;
}
