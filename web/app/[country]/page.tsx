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
import { Card, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { countryFlag } from "@/lib/format";
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
      <header className="mb-5 flex items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-[22px] font-bold tracking-tight">
            <span className="text-[24px] leading-none">{countryFlag(countryCode)}</span>
            {country?.name ?? countryCode}
          </h1>
          <p className="mt-1 text-[12px] text-ink-dim">
            {country
              ? `Moneda ${country.currency_code} · ventana de maduración ${
                  country.maturation_days ?? 21
                } días`
              : "Cargando…"}
          </p>
        </div>

        {/* The printable version of the logistics tab: one block per platform,
            the daily table, the consolidated strip. Keeps the range and the
            platform from the URL so it opens on the same question. */}
        {country && (
          <Link
            href={`/${countryCode.toLowerCase()}/informe${
              search.toString() ? `?${search.toString()}` : ""
            }`}
            className="shrink-0 rounded-[8px] border border-line-strong bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-2 no-underline hover:text-ink"
          >
            Informe diario
          </Link>
        )}
      </header>

      <div className="mb-5 flex gap-1 border-b border-line-strong">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => {
              setTab(item.key);
              // Rebuild from the current query, never from scratch: writing
              // `?tab=x` alone would drop the date range the reader just set.
              const next = new URLSearchParams(search.toString());
              next.set("tab", item.key);
              router.replace(`/${countryCode.toLowerCase()}?${next.toString()}`, {
                scroll: false,
              });
            }}
            className={cx(
              "-mb-px border-b-2 px-3.5 py-2.5 text-[13px] transition-colors",
              tab === item.key
                ? "border-accent font-semibold text-ink"
                : "border-transparent text-ink-muted hover:text-ink-2",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

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
            title="Nada configurado en esta pestaña"
            instruction="Conecta una fuente de datos para este país y los widgets aparecerán aquí."
          />
        </Card>
      )}

      {country && (
        <div className="grid gap-4 xl:grid-cols-2">
          {widgets.map((widget) => (
            <div
              key={widget.widget_code}
              className={cx(FULL_WIDTH.has(widget.widget_code) && "xl:col-span-2")}
            >
              <WidgetRenderer widget={widget} country={country} />
            </div>
          ))}
        </div>
      )}
    </AppShell>
  );
}
