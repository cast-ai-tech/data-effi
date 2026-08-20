/**
 * Types mirroring the API responses.
 *
 * Hand-written rather than generated so the shape stays readable, but they
 * follow api/schemas.py field for field. `npm run typecheck` will not catch a
 * drift here - the API integration tests will.
 */

export type Role = "owner" | "analyst" | "viewer";
export type WidgetState = "available" | "degraded" | "blocked";
export type TrafficLight = "verde" | "amarillo" | "rojo" | "sin_datos";

export interface Tokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: Role;
  tenant_id: string;
  tenant_name: string;
  created_at: string;
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

export interface Platform {
  platform_code: string;
  platform_name: string;
  tier: number;
  data_domains: string[];
  requires_consent: boolean;
  docs_url: string | null;
  connection_count: number;
  active_connection_count: number;
  is_connected: boolean;
}

export interface Connection {
  connection_id: string;
  connection_name: string;
  country_code: string;
  platform_code: string;
  platform_name: string;
  tier: number;
  status: string;
  health: "ok" | "stale" | "error" | "never_synced" | "disabled";
  consent_granted_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  hours_since_sync: number | null;
  batches_7d: number | null;
  failed_batches_7d: number | null;
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
}

export interface LayoutWidget {
  widget_code: string;
  tab: string;
  title: string;
  description: string;
  sort_order: number;
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
