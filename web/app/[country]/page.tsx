"use client";

/**
 * The country dashboard - the screen this product lives or dies on.
 *
 * It does not decide what to show. It asks `/kpis/layout`, gets back every
 * widget with its state, and renders them through WidgetRenderer. That is why a
 * missing ads connector produces a locked CPA card here instead of a hole where
 * a card should be.
 */

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { DecisionStrip } from "@/components/DecisionStrip";
import { WidgetRenderer } from "@/components/WidgetRenderer";
import { TABS, type TabKey } from "@/components/widgets/registry";
import { HelpTip } from "@/components/HelpTip";
import { Card, EmptyState, SkeletonRows, Tabs, cx } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { countryFlag } from "@/lib/format";
import { TAB_HELP } from "@/lib/glossary";
import { useApi } from "@/lib/hooks";
import type { Country, DecisionScope, LayoutResponse } from "@/lib/types";

/**
 * Which decisions sit above which tab. Each tab gets the verdicts about the
 * thing its widgets measure, so the strip reads as the conclusion of the
 * cards below it rather than as a second, unrelated dashboard.
 */
const DECISION_SCOPE: Record<TabKey, DecisionScope> = {
  finanzas: "cash",
  logistica: "carriers",
  efectividad: "products",
  servicio: "office",
};

/** Widgets that deserve the full width of the grid. */
const FULL_WIDTH = new Set([
  "kpi_contribution",
  "carrier_table",
  "product_table",
  "geo_traffic_light",
  "cs_confirmation",
  // One block per platform, nine columns each: needs the whole row.
  "daily_status_table",
]);

export default function CountryDashboard() {
  const params = useParams<{ country: string }>();
  const search = useSearchParams();
  const router = useRouter();

  const countryCode = (params.country ?? "").toUpperCase();

  // The date range is not held here any more: it lives in the URL and every
  // widget reads it through `useRangedApi`, so it survives a tab switch, a
  // change of country, and a copy-pasted link.
  const [tab, setTab] = useState<TabKey>(() => {
    const requested = search.get("tab");
    return (TABS.find((t) => t.key === requested)?.key ?? "finanzas") as TabKey;
  });

  // Keep the tab in the URL so the copilot's deep links land on the right one.
  useEffect(() => {
    const requested = search.get("tab");
    if (requested && requested !== tab && TABS.some((t) => t.key === requested)) {
      setTab(requested as TabKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const { data: countries, loading: loadingCountries } = useApi<Country[]>(
    "/config/countries",
  );
  const country = useMemo(
    () => (countries ?? []).find((item) => item.code === countryCode) ?? null,
    [countries, countryCode],
  );

  const { data: layout, loading: loadingLayout, error } = useApi<LayoutResponse>(
    countryCode ? `/kpis/layout?country=${countryCode}` : null,
    [countryCode],
  );

  const widgets = useMemo(
    () => (layout?.widgets ?? []).filter((widget) => widget.tab === tab),
    [layout, tab],
  );

  if (!loadingCountries && countries && !country) {
    return (
      <AppShell>
        <EmptyState
          title={`${countryCode} no está activo en tu workspace`}
          instruction="Actívalo en Configuración para ver su tablero."
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        flag={countryFlag(countryCode)}
        title={country?.name ?? countryCode}
        subtitle={
          country ? (
            <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <span>
                Moneda {country.currency_code} · esperamos {country.maturation_days ?? 21} días
                para dar una guía por cerrada
              </span>
              <HelpTip term="maduracion" />
            </span>
          ) : (
            "Cargando…"
          )
        }
        actions={
          country && (
            /* The printable version of the logistics tab: one block per platform,
               the daily table, the consolidated strip. Keeps the range and the
               platform from the URL so it opens on the same question. */
            <Link
              href={`/${countryCode.toLowerCase()}/informe${
                search.toString() ? `?${search.toString()}` : ""
              }`}
              className="inline-flex min-h-11 items-center rounded-control border border-line-strong bg-surface px-4 text-base font-medium text-ink-2 no-underline hover:border-accent-deep hover:text-accent-ink"
            >
              Informe diario
            </Link>
          )
        }
      />

      <Tabs
        className="mb-4"
        label="Secciones del tablero"
        value={tab}
        items={TABS}
        onChange={(key) => {
          setTab(key);
          // Rebuild from the current query, never from scratch: writing
          // `?tab=x` alone would drop the date range the reader just set.
          const next = new URLSearchParams(search.toString());
          next.set("tab", key);
          router.replace(`/${countryCode.toLowerCase()}?${next.toString()}`, {
            scroll: false,
          });
        }}
      />

      {/* One line that says what this tab is about, for whoever has never
          opened it. Cheaper than a tooltip and it works on a phone. */}
      <p className="mb-4 text-base text-ink-muted">{TAB_HELP[tab]}</p>

      {/* The verdicts for this tab, before the numbers that justify them. Only
          once the country is known: a strip for a country you may not open
          would be a 403 dressed as a recommendation. */}
      {country && <DecisionStrip countryCode={countryCode} scope={DECISION_SCOPE[tab]} />}

      {loadingLayout && <SkeletonRows rows={4} />}

      {error && (
        <Card>
          <EmptyState
            title="No se pudo cargar el tablero"
            instruction="Revisa que la API esté corriendo y vuelve a intentarlo."
          />
        </Card>
      )}

      {country && widgets.length === 0 && !loadingLayout && (
        <Card>
          <EmptyState
            title="Esta pestaña todavía no tiene datos"
            instruction="Sube un reporte en Cargar datos o conecta una plataforma en Configuración → Conexiones."
          />
        </Card>
      )}

      {country && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-5">
          {widgets.map((widget) => (
            <div
              key={widget.widget_code}
              className={cx("min-w-0", FULL_WIDTH.has(widget.widget_code) && "lg:col-span-2")}
            >
              <WidgetRenderer widget={widget} country={country} />
            </div>
          ))}
        </div>
      )}
    </AppShell>
  );
}
