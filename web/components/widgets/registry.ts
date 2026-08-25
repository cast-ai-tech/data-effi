/**
 * Widget registry: `widget_code` from the database -> the React component.
 *
 * The dashboard is COMPOSED from `/kpis/layout`. Adding a widget means adding a
 * row to `core.widget_catalog` and an entry here. The page files never list
 * widgets themselves, which is what keeps the blocked/degraded logic in one
 * place instead of scattered across four tabs.
 */

import AgingBars from "@/components/widgets/aging_bars";
import CapitalInStreet from "@/components/widgets/capital_in_street";
import CarrierByZone from "@/components/widgets/carrier_by_zone";
import CarrierTable from "@/components/widgets/carrier_table";
import CashCycle from "@/components/widgets/cash_cycle";
import CohortCurve from "@/components/widgets/cohort_curve";
import ContributionSplitWidget from "@/components/widgets/contribution_split";
import CpaRoas from "@/components/widgets/cpa_roas";
import CsConfirmation from "@/components/widgets/cs_confirmation";
import DailyStatusTableWidget from "@/components/widgets/daily_status_table";
import DropshippingMargin from "@/components/widgets/dropshipping_margin";
import FreightAnalysis from "@/components/widgets/freight_analysis";
import FulfillmentSla from "@/components/widgets/fulfillment_sla";
import GeoTrafficLight from "@/components/widgets/geo_traffic_light";
import GlobalSummary from "@/components/widgets/global_summary";
import KpiContribution from "@/components/widgets/kpi_contribution";
import MarginDeliveryScatter from "@/components/widgets/margin_delivery_scatter";
import OfficeRescue from "@/components/widgets/office_rescue";
import PlatformSplit from "@/components/widgets/platform_split";
import ProductTable from "@/components/widgets/product_table";
import WaterfallPnl from "@/components/widgets/waterfall_pnl";
import type { WidgetComponent } from "@/components/widgets/types";

export const WIDGET_REGISTRY: Record<string, WidgetComponent> = {
  kpi_contribution: KpiContribution,
  waterfall_pnl: WaterfallPnl,
  cpa_roas: CpaRoas,
  carrier_table: CarrierTable,
  aging_bars: AgingBars,
  cohort_curve: CohortCurve,
  geo_traffic_light: GeoTrafficLight,
  margin_delivery_scatter: MarginDeliveryScatter,
  product_table: ProductTable,
  cs_confirmation: CsConfirmation,
  global_summary: GlobalSummary,

  // Dropshipping metrics (migration 009) + cash cycle (migration 008).
  dropshipping_margin: DropshippingMargin,
  fulfillment_sla: FulfillmentSla,
  office_rescue: OfficeRescue,
  freight_analysis: FreightAnalysis,
  cash_cycle: CashCycle,

  // Orders, customers and the honest contribution split (migration 015).
  contribution_split: ContributionSplitWidget,
  capital_in_street: CapitalInStreet,

  // Carrier per zone (migration 039). Tab `logistica` in `core.widget_catalog`.
  carrier_by_zone: CarrierByZone,

  // Effi next to Dropi (migration 040): the daily table by status group and
  // the one-line-per-platform strip. Both on `logistica`, above the carriers.
  platform_split: PlatformSplit,
  daily_status_table: DailyStatusTableWidget,
};

/** Tabs of the country dashboard, in display order. */
export const TABS = [
  { key: "finanzas", label: "Dinero" },
  { key: "logistica", label: "Entregas" },
  { key: "efectividad", label: "Productos" },
  { key: "servicio", label: "Servicio" },
] as const;

export type TabKey = (typeof TABS)[number]["key"];
