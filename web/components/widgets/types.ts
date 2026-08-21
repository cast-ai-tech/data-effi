import type { Country, WidgetState } from "@/lib/types";

/**
 * The contract every widget implements.
 *
 * A widget receives its country (which carries the formatting rules) and the
 * state the API computed for it. It NEVER decides whether it should exist -
 * `mart.v_country_dashboard_layout` already answered that, and WidgetRenderer
 * enforces it.
 *
 * THE DATE RANGE IS NOT A PROP. It used to be, and a prop is exactly how a
 * dashboard ends up with three widgets that quietly ignore the filter because
 * nobody threaded `dateFrom` down to them. Widgets fetch through
 * `useRangedApi` (see `lib/date-range.tsx`), which reads the one range in the
 * URL and reports back whether the server actually honoured it.
 */
export interface WidgetProps {
  countryCode: string;
  country: Country;
  /** 'available' or 'degraded'. A blocked widget is never mounted. */
  state: Exclude<WidgetState, "blocked">;
  /** Why it is degraded, when it is. Rendered as a band above the content. */
  message: string | null;
}

export type WidgetComponent = React.ComponentType<WidgetProps>;
