"use client";

/**
 * Parcels sitting at a carrier office, waiting for the customer to come.
 *
 * These guides are in nobody's report: not delivered, not returned, just
 * decaying. Past three weeks the carrier normally sends them back on its own,
 * so the whole widget exists to answer one question - how much money is still
 * close enough to be rescued by a phone call, and which city to call first.
 */

import { useMemo } from "react";

import {
  Card,
  EmptyState,
  ErrorState,
  ShowMore,
  SkeletonRows,
  cx,
  useShowMore,
} from "@/components/ui";
import { decisionsPath } from "@/components/DecisionStrip";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Decision, DecisionsResponse, OfficeRescueRow } from "@/lib/types";

/** How many cities "Llamar primero" names. Five is one morning's calls. */
const CALL_FIRST = 5;

/**
 * The four bands, in the order they decay.
 *
 * Same ramp as the aging widget - green while it is normal, amber while a call
 * still works, red on the last week. The last band is grey rather than red on
 * purpose: past 21 days the parcel is already on its way back and a call does
 * not change the outcome, so it is not an action, it is a loss to record.
 */
const BANDS = [
  {
    key: "fresh_0_7" as const,
    label: "0-7 días",
    colour: "#21c08a",
    hint: "Normal: el cliente todavía está dentro del plazo para recogerlo.",
  },
  {
    key: "aging_8_14" as const,
    label: "8-14 días",
    colour: "#f5a83c",
    hint: "Se está enfriando. Una llamada acá casi siempre lo rescata.",
  },
  {
    key: "urgent_15_21" as const,
    label: "15-21 días",
    colour: "#ff6259",
    hint: "Última semana antes de que la transportadora lo devuelva.",
  },
  {
    key: "probably_lost" as const,
    label: "Más de 21 días",
    colour: "#5b6272",
    hint: "Ya va de vuelta. Llamar no cambia el resultado.",
  },
];

/** Stable empty list: an inline [] would reset the expansion every render. */
const EMPTY_CITIES: CityRow[] = [];

interface CityRow {
  key: string;
  city_name: string;
  level1_name: string;
  shipments: number;
  value_waiting: number;
}

/** One line per city: the operator calls a city, not a carrier-city pair. */
function groupByCity(rows: OfficeRescueRow[]): CityRow[] {
  const cities = new Map<string, CityRow>();

  for (const row of rows) {
    const key = `${row.level1_name}||${row.city_name}`;
    const existing = cities.get(key);
    if (existing) {
      existing.shipments += row.shipments;
      existing.value_waiting += row.value_waiting ?? 0;
    } else {
      cities.set(key, {
        key,
        city_name: row.city_name,
        level1_name: row.level1_name,
        shipments: row.shipments,
        value_waiting: row.value_waiting ?? 0,
      });
    }
  }

  return [...cities.values()].sort((a, b) => b.value_waiting - a.value_waiting);
}

export default function OfficeRescue({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<OfficeRescueRow[]>(
    `/kpis/office-rescue?country=${countryCode}`,
  );

  // Who to call first comes from the decisions endpoint, ordered by what is
  // still recoverable and filtered to the 8-21 day window where a call works.
  const { data: decisions } = useApi<DecisionsResponse>(
    decisionsPath(countryCode, "office"),
    [countryCode],
  );
  const callFirst = useMemo<Decision[]>(
    () => (decisions?.items ?? []).filter((item) => item.verdict === "call").slice(0, CALL_FIRST),
    [decisions],
  );

  const model = useMemo(() => {
    const rows = data ?? [];
    if (rows.length === 0) return null;

    const bands = BANDS.map((band) => ({
      ...band,
      count: rows.reduce((total, row) => total + row[band.key], 0),
    }));

    return {
      bands,
      maxBand: bands.reduce((max, band) => Math.max(max, band.count), 0),
      cities: groupByCity(rows),
      shipments: rows.reduce((total, row) => total + row.shipments, 0),
      valueWaiting: rows.reduce((total, row) => total + (row.value_waiting ?? 0), 0),
      recoverable: rows.reduce(
        (total, row) => total + (row.value_still_recoverable ?? 0),
        0,
      ),
    };
  }, [data]);

  // Called before the early returns below: a hook cannot live behind a
  // conditional. Until the model resolves it just paginates an empty list.
  const cities = useShowMore(model?.cities ?? EMPTY_CITIES);

  const SUBTITLE = "Paquetes esperando que el cliente los recoja, por antigüedad y ciudad.";

  if (loading) {
    return (
      <Card title="Guías en oficina" subtitle={SUBTITLE}>
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Guías en oficina" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!model || model.shipments === 0) {
    return (
      <Card title="Guías en oficina" subtitle={SUBTITLE}>
        <EmptyState
          title="Ninguna guía está esperando en una oficina"
          instruction="No hay paquetes en estado «en oficina» en este momento. Si esperabas ver alguno, revisa la última sincronización de la transportadora en Configuración → Conexiones."
        />
      </Card>
    );
  }

  return (
    <Card title="Guías en oficina" subtitle={SUBTITLE}>
      <p
        className={cx(
          "text-base font-semibold leading-snug",
          model.recoverable > 0 ? "text-warning-ink" : "text-ink-muted",
        )}
      >
        {model.recoverable > 0
          ? `${formatMoney(model.recoverable, country)} todavía se salvan con una llamada.`
          : "No queda plata en la ventana de rescate: nada entre 8 y 21 días esperando."}
      </p>
      <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-dim">
        Es el recaudo de las guías que llevan entre 8 y 21 días en la oficina: ya se
        enfriaron, pero la transportadora aún no las devuelve. Después de tres semanas se
        regresan solas y ese dinero se pierde junto con el flete de ida y el de vuelta.
      </p>

      {callFirst.length > 0 && (
        <section
          aria-label="Llamar primero"
          className="mt-4 rounded-control border border-accent/30 bg-accent/[0.06] p-3"
        >
          <h4 className="text-xs font-bold uppercase tracking-[0.08em] text-accent-ink">
            Llamar primero
          </h4>
          <ol className="mt-2 space-y-1.5">
            {callFirst.map((item, index) => {
              const shipments = Number(item.numbers.shipments);
              return (
                <li key={item.key} className="flex items-baseline gap-2 text-sm">
                  <span className="w-4 shrink-0 text-right text-ink-dim">{index + 1}.</span>
                  <span className="min-w-0 flex-1 truncate text-ink-2">
                    <span className="font-semibold text-ink">{item.label}</span>
                    {typeof item.numbers.carrier_name === "string" && (
                      <span className="text-ink-dim"> · {item.numbers.carrier_name}</span>
                    )}
                    {Number.isFinite(shipments) && shipments > 0 && (
                      <span className="text-ink-dim">
                        {" "}
                        · {formatNumber(shipments, country, 0)} guías
                      </span>
                    )}
                  </span>
                  {item.impact_amount !== null && (
                    <span className="shrink-0 font-semibold text-ink-2">
                      {formatMoney(item.impact_amount, country)}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      )}

      <ul className="mt-4 space-y-2.5">
        {model.bands.map((band) => {
          const width = model.maxBand > 0 ? (band.count / model.maxBand) * 100 : 0;
          return (
            <li key={band.key} className="flex items-center gap-3" title={band.hint}>
              <span className="w-[92px] shrink-0 text-sm font-medium text-ink-muted">
                {band.label}
              </span>
              <div className="h-[10px] flex-1 overflow-hidden rounded-full bg-track">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${width}%`, background: band.colour }}
                />
              </div>
              <span className="w-[64px] shrink-0 text-right text-sm font-semibold text-ink-2">
                {formatNumber(band.count, country, 0)}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="mt-4 overflow-x-auto border-t border-line-subtle pt-3">
        <table className="w-full min-w-[420px] border-collapse text-sm">
          <caption className="mb-2 text-left text-xs text-ink-dim">
            Ciudades ordenadas por el recaudo que tienen detenido. Empieza por arriba.
          </caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                Ciudad
              </th>
              <th
                scope="col"
                className="px-2 py-1.5 text-right text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                Guías
              </th>
              <th
                scope="col"
                className="px-2 py-1.5 text-right text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                Recaudo detenido
              </th>
            </tr>
          </thead>
          <tbody>
            {cities.shown.map((city) => (
              <tr key={city.key} className="border-t border-line-row">
                <td className="max-w-[220px] px-2 py-2 text-left">
                  <span className="block truncate text-ink-2">{city.city_name}</span>
                  <span className="block truncate text-xs text-ink-dim">
                    {city.level1_name}
                  </span>
                </td>
                <td className="px-2 py-2 text-right text-ink-2">
                  {formatNumber(city.shipments, country, 0)}
                </td>
                <td className="px-2 py-2 text-right font-semibold text-ink-2">
                  {formatMoney(city.value_waiting, country)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-line-strong bg-sunken text-sm font-semibold text-ink-2">
              <td className="px-2 py-2 text-left">Total en oficina</td>
              <td className="px-2 py-2 text-right">
                {formatNumber(model.shipments, country, 0)}
              </td>
              <td className="px-2 py-2 text-right">
                {formatMoney(model.valueWaiting, country)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <ShowMore
        remaining={cities.remaining}
        total={cities.total}
        shownCount={cities.shown.length}
        onMore={cities.showMore}
        onCollapse={cities.collapse}
        noun="ciudades"
      />
    </Card>
  );
}
