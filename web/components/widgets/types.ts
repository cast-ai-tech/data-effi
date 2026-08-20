import type { Country, WidgetState } from "@/lib/types";

/**
 * The contract every widget implements.
 *
 * A widget receives its country (which carries the formatting rules), the date
 * range, and the state the API computed for it. It NEVER decides whether it
 * should exist - `mart.v_country_dashboard_layout` already answered that, and
 * WidgetRenderer enforces it.
 */
export interface WidgetProps {
  countryCode: string;
  country: Country;
  dateFrom?: string;
  dateTo?: string;
  /** 'available' or 'degraded'. A blocked widget is never mounted. */
  state: Exclude<WidgetState, "blocked">;
  /** Why it is degraded, when it is. Rendered as a band above the content. */
  message: string | null;
}

export type WidgetComponent = React.ComponentType<WidgetProps>;
