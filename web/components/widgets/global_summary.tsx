"use client";

/**
 * The multi-country view: every operation on one comparable line.
 *
 * The trap this widget exists to avoid is showing four countries' money with one
 * country's currency. 12.000 in Colombia and 12.000 in Peru are not the same
 * amount and must never be printed the same way, so each row formats its own
 * local figure with its own currency, and the ONLY column you are allowed to
 * compare across rows is the USD one - which is blank, on purpose, whenever the
 * exchange rate for that day is missing.
 */

import { useMemo } from "react";

import { Card, Chip, EmptyState, ErrorState, MicroBar, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { useApi } from "@/lib/hooks";
import type { FormatCountry } from "@/lib/format";
import { countryFlag, formatMoney, formatNumber, formatPercent } from "@/lib/format";
import type { Country, GlobalRow } from "@/lib/types";

/**
 * Formatting rules for one row's local currency.
 *
 * Taken from `core.country`, never from a table written here. A map in the
 * frontend has to be edited every time a country is added, and the failure is
 * silent: an unlisted currency falls back to "$", so Guaraníes render as
 * dollars on a screen whose whole job is comparing countries.
 *
 * The currency comes from the row's own country; the separators stay the
 * viewer's, because the reader is one person with one set of reading habits.
 */
function localFormat(
  countryCode: string,
  currencyCode: string | null,
  countries: Country[],
  viewer: FormatCountry,
): FormatCountry {
  const source = countries.find((c) => c.code === countryCode);
  if (source) {
    return {
      ...viewer,
      currency_code: source.currency_code,
      currency_symbol: source.currency_symbol,
      decimal_places: source.decimal_places,
    };
  }
  // A country the workspace no longer lists can still own historical rows. The
  // code is shown instead of guessing a symbol: "PYG 1.500" is honest, "$ 1.500"
  // is wrong.
  const code = (currencyCode ?? viewer.currency_code).toUpperCase();
  return { ...viewer, currency_code: code, currency_symbol: code, decimal_places: 2 };
}

function usdFormat(viewer: FormatCountry): FormatCountry {
  return {
    ...viewer,
    currency_code: "USD",
    currency_symbol: "US$",
    decimal_places: 2,
  };
}

/**
 * Ordering key. USD is the honest comparator; a row without a rate falls back to
 * its local figure only so it lands somewhere sensible instead of at the bottom.
 */
function sortKey(row: GlobalRow): number {
  const value = row.contribution_usd ?? row.contribution;
  return value === null || !Number.isFinite(value) ? Number.NEGATIVE_INFINITY : value;
}

export default function GlobalSummary({ country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<GlobalRow[]>("/kpis/global");
  const countries = useApi<Country[]>("/config/countries");

  const rows = useMemo(
    () => [...(data ?? [])].sort((a, b) => sortKey(b) - sortKey(a)),
    [data],
  );

  const usd = useMemo(() => usdFormat(country), [country]);

  const activeCount = rows.filter((row) => row.shipments > 0).length;
  const withRate = rows.filter(
    (row) => !row.fx_missing && row.contribution_usd !== null,
  );
  const totalUsd = withRate.reduce((sum, row) => sum + (row.contribution_usd ?? 0), 0);
  const missingRate = rows.length - withRate.length;

  const subtitle = "Contribución de cada país en su moneda y convertida a dólares";

  if (loading) {
    return (
      <Card title="Consolidado multi-país" subtitle={subtitle}>
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Consolidado multi-país" subtitle={subtitle}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Consolidado multi-país" subtitle={subtitle}>
        <EmptyState
          title="Solo hay un país con operación"
          instruction="Active otro país en Configuración y conéctele su fuente de guías. Cuando dos o más países tengan despachos, esta tabla los pone lado a lado."
        />
      </Card>
    );
  }

  return (
    <Card title="Consolidado multi-país" subtitle={subtitle} bodyClassName="p-0">
      <p className="border-b border-line-subtle px-4 py-2.5 text-sm text-ink-2">
        <span className="font-semibold text-ink">
          {activeCount} {activeCount === 1 ? "país" : "países"} con operación
        </span>
        {withRate.length > 0 && (
          <>
            {" · "}
            <span className={totalUsd < 0 ? "text-negative-ink" : "text-positive-ink"}>
              {formatMoney(totalUsd, usd)}
            </span>{" "}
            <span className="text-ink-dim">
              de contribución total en {withRate.length}{" "}
              {withRate.length === 1 ? "país con tasa" : "países con tasa de cambio"}
            </span>
          </>
        )}
        {missingRate > 0 && (
          <>
            {" · "}
            <span className="text-warning-ink">
              {missingRate} sin tasa, fuera del total
            </span>
          </>
        )}
      </p>

      <div className="flex items-center gap-3 px-4 pb-2 pt-3 text-xs font-bold uppercase tracking-[0.06em] text-ink-faint">
        <span className="min-w-0 flex-1">País</span>
        <span className="w-[132px] shrink-0 text-right">Contribución local</span>
        <span className="w-[128px] shrink-0 text-right">En dólares</span>
        <span className="w-[126px] shrink-0 pl-[52px]">% entrega</span>
        <span className="w-[76px] shrink-0 text-right">Guías</span>
      </div>

      <ul className="divide-y divide-line-row">
        {rows.map((row) => {
          const local = localFormat(
            row.country_code,
            row.currency_code,
            countries.data ?? [],
            country,
          );
          const contributionTone =
            row.contribution !== null && row.contribution < 0
              ? "text-negative-ink"
              : "text-positive-ink";

          return (
            <li
              key={row.country_code}
              className="flex items-center gap-3 px-4 py-2.5"
            >
              <span className="flex min-w-0 flex-1 items-center gap-2">
                <span aria-hidden className="text-md leading-none">
                  {countryFlag(row.country_code)}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-base font-semibold text-ink">
                    {row.country_name}
                  </span>
                  <span className="block text-xs text-ink-dim">
                    {row.currency_code ?? "—"}
                  </span>
                </span>
              </span>

              <span
                className={`w-[132px] shrink-0 text-right text-sm ${contributionTone}`}
              >
                {formatMoney(row.contribution, local)}
              </span>

              <span className="flex w-[128px] shrink-0 justify-end text-sm">
                {row.fx_missing || row.contribution_usd === null ? (
                  <Chip tone="warning">sin tasa de cambio</Chip>
                ) : (
                  <span
                    className={
                      row.contribution_usd < 0 ? "text-negative-ink" : "text-positive-ink"
                    }
                  >
                    {formatMoney(row.contribution_usd, usd)}
                  </span>
                )}
              </span>

              <span className="w-[126px] shrink-0">
                <MicroBar
                  value={row.delivery_rate_pct}
                  max={100}
                  tone={
                    row.delivery_rate_pct === null
                      ? "neutral"
                      : row.delivery_rate_pct >= 70
                        ? "positive"
                        : row.delivery_rate_pct >= 55
                          ? "warning"
                          : "negative"
                  }
                  label={formatPercent(row.delivery_rate_pct)}
                />
              </span>

              <span className="w-[76px] shrink-0 text-right text-sm text-ink-2">
                {formatNumber(row.shipments, country, 0)}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
