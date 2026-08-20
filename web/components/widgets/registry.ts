/**
 * Widget registry: `widget_code` from the database -> the React component.
 *
 * The dashboard is COMPOSED from `/kpis/layout`. Adding a widget means adding a
 * row to `core.widget_catalog` and an entry here. The page files never list
 * widgets themselves, which is what keeps the blocked/degraded logic in one
 * place instead of scattered across four tabs.
 */

import AgingBars from "@/components/widgets/aging_bars";
import CarrierTable from "@/components/widgets/carrier_table";
import CohortCurve from "@/components/widgets/cohort_curve";
import CpaRoas from "@/components/widgets/cpa_roas";
import CsConfirmation from "@/components/widgets/cs_confirmation";
import GeoTrafficLight from "@/components/widgets/geo_traffic_light";
import GlobalSummary from "@/components/widgets/global_summary";
import KpiContribution from "@/components/widgets/kpi_contribution";
import MarginDeliveryScatter from "@/components/widgets/margin_delivery_scatter";
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
};

/** Tabs of the country dashboard, in display order. */
export const TABS = [
  { key: "finanzas", label: "Finanzas" },
  { key: "logistica", label: "Logística" },
  { key: "efectividad", label: "Efectividad" },
  { key: "servicio", label: "Servicio" },
] as const;

export type TabKey = (typeof TABS)[number]["key"];
