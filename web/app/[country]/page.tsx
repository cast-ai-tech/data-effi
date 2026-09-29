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
import { DashboardGrid } from "@/components/DashboardGrid";
import { TABS, type TabKey } from "@/components/widgets/registry";
import { HelpTip } from "@/components/HelpTip";
import { Card, EmptyState, ErrorState, SkeletonRows, Tabs } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { countryFlag } from "@/lib/format";
import { TAB_HELP } from "@/lib/glossary";
import { useApi } from "@/lib/hooks";
import type { Country, DecisionScope, LayoutResponse, User } from "@/lib/types";

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

  const {
    data: countries,
    loading: loadingCountries,
    error: countriesError,
    reload: reloadCountries,
  } = useApi<Country[]>(
    "/config/countries",
  );
  const country = useMemo(
    // /config/countries is the whole catalogue; only an ACTIVE one is this
    // company's. /pe used to open an empty Peru board for a company in Ecuador.
    () => (countries ?? []).find((item) => item.code === countryCode && item.is_active) ?? null,
    [countries, countryCode],
  );

  // A partner limited to other countries of this company: say so instead of
  // asking the API for a board it will refuse (that used to read "Revisa que
  // la API esté corriendo", which sent people chasing a server that was fine).
  const { data: user } = useApi<User>("/auth/me");
  const outOfScope = Boolean(user?.countries && !user.countries.includes(countryCode));

  const {
    data: layout,
    loading: loadingLayout,
    error,
    reload: refreshLayout,
  } = useApi<LayoutResponse>(
    countryCode && user && !outOfScope ? `/kpis/layout?country=${countryCode}` : null,
    [countryCode],
  );

  const widgets = useMemo(
    () => (layout?.widgets ?? []).filter((widget) => widget.tab === tab),
    [layout, tab],
  );

  // Without the country row there are no formatting rules and no dashboard;
  // the header would say "Cargando…" forever.
  if (!loadingCountries && countriesError) {
    return (
      <AppShell>
        <Card>
          <ErrorState message={countriesError.message} onRetry={reloadCountries} />
        </Card>
      </AppShell>
    );
  }

  if (outOfScope) {
    return (
      <AppShell>
        <EmptyState
          title={`No tienes acceso a ${country?.name ?? countryCode}`}
          instruction="Tu acceso en esta empresa no incluye este país. Si lo necesitas, pídeselo a quien administra la empresa."
        />
      </AppShell>
    );
  }

  if (!loadingCountries && countries && !country) {
    return (
      <AppShell>
        <EmptyState
          title={`${countryCode} no es un país de esta empresa`}
          instruction="Cada empresa opera en un país, el que eligió al crearla. Para ver otro, cambia de empresa o crea una para ese país."
          action={
            <Link
              href="/empresas"
              className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
            >
              Mis empresas
            </Link>
          }
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
      {country && user && <DecisionStrip countryCode={countryCode} scope={DECISION_SCOPE[tab]} />}

      {loadingLayout && <SkeletonRows rows={4} />}

      {error && (
        <Card>
          <ErrorState message={error.message} onRetry={refreshLayout} />
        </Card>
      )}

      {/* Not after a failed read: "todavía no tiene datos" under "no se pudo
          cargar" contradicts it and suggests uploading data that is there. */}
      {country && layout && widgets.length === 0 && !loadingLayout && !error && (
        <Card>
          <EmptyState
            title="Esta pestaña todavía no tiene datos"
            instruction="Sube el reporte de guías de tu plataforma, o conéctala para que los datos lleguen solos."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link
                  href={`/${countryCode.toLowerCase()}/cargar`}
                  className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
                >
                  Cargar datos
                </Link>
                <Link
                  href="/connections"
                  className="rounded-control border border-line-strong px-3.5 py-2 text-sm font-semibold text-ink-2 no-underline hover:border-accent"
                >
                  Ir a Conexiones
                </Link>
              </div>
            }
          />
        </Card>
      )}

      {country && (
        <DashboardGrid
          widgets={widgets}
          country={country}
          defaultFullWidth={FULL_WIDTH}
          customised={Boolean(layout?.customised)}
          onSaved={refreshLayout}
        />
      )}
    </AppShell>
  );
}
