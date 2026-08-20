"use client";

/**
 * Who is actually making the customer wait.
 *
 * Everyone blames the carrier. But the clock starts when the guide is created,
 * and in a real export the median gap from creation to the parcel physically
 * leaving was SIX DAYS - longer than the carrier then took to deliver it.
 *
 * So the bar is split in two: the amber half is yours (preparation), the blue
 * half is theirs (transit). The headline says what share of the wait you cause,
 * because that is the half you can fix this week without renegotiating a single
 * contract.
 */

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import type { FormatCountry } from "@/lib/format";
import { formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { FulfillmentSlaRow } from "@/lib/types";

/** Yours. */
const PREP_COLOUR = "#f5a83c";
/** Theirs. */
const TRANSIT_COLOUR = "#29a9e0";

/** A service level nobody filled in. Not worth repeating in every label. */
const NO_SERVICE = "Sin servicio";

interface Lane {
  key: string;
  label: string;
  shipments: number;
  prep_days: number | null;
  transit_days: number | null;
  on_time_pct: number | null;
  measurable_count: number;
}

/**
 * One lane per row of the view: a carrier and one of its services.
 *
 * Rows are NOT collapsed into a carrier average, because `p50_prep_days` is a
 * median and medians do not add up. Two services of the same carrier appear as
 * two lanes rather than as one invented number.
 */
function toLane(row: FulfillmentSlaRow): Lane {
  const label =
    row.service_level && row.service_level !== NO_SERVICE
      ? `${row.carrier_name} · ${row.service_level}`
      : row.carrier_name;

  return {
    key: `${row.carrier_id ?? row.carrier_name}-${row.service_level}`,
    label,
    shipments: row.shipments,
    prep_days: row.p50_prep_days,
    transit_days: row.avg_transit_days,
    on_time_pct: row.on_time_pct,
    measurable_count: row.measurable_count,
  };
}

/**
 * The share of the wait that happens before the parcel leaves.
 *
 * Weighted by guides over the AVERAGES (which do weight), never over the
 * per-row `prep_share_pct` (a mean of ratios, which lies on low volume).
 */
function weightedPrepShare(rows: FulfillmentSlaRow[]): number | null {
  let prepWeighted = 0;
  let totalWeighted = 0;
  let weight = 0;

  for (const row of rows) {
    const prep = row.avg_prep_days;
    const total = row.avg_total_days;
    if (prep === null || total === null) continue;
    if (!Number.isFinite(prep) || !Number.isFinite(total)) continue;
    prepWeighted += prep * row.shipments;
    totalWeighted += total * row.shipments;
    weight += row.shipments;
  }

  if (weight === 0 || totalWeighted === 0) return null;
  return (prepWeighted / totalWeighted) * 100;
}

/** Weighted over the guides whose promise date could actually be checked. */
function weightedOnTime(rows: FulfillmentSlaRow[]): number | null {
  const measurable = rows.reduce((total, row) => total + row.measurable_count, 0);
  if (measurable === 0) return null;
  const onTime = rows.reduce((total, row) => total + row.on_time_count, 0);
  return (onTime / measurable) * 100;
}

/** Above 60% on time is liveable; below 40% the promise is fiction. */
function onTimeTone(pct: number | null): "positive" | "warning" | "negative" {
  if (pct === null || !Number.isFinite(pct)) return "negative";
  if (pct >= 60) return "positive";
  if (pct >= 40) return "warning";
  return "negative";
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

interface TooltipEntry {
  dataKey?: string | number;
  value?: number | string;
}

function ChartTooltip({
  active,
  payload,
  label,
  country,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  country: FormatCountry;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const read = (key: string): number | null => {
    const entry = payload.find((item) => item.dataKey === key);
    const value = typeof entry?.value === "number" ? entry.value : null;
    return value !== null && Number.isFinite(value) ? value : null;
  };

  const prep = read("prep_days");
  const transit = read("transit_days");
  const total = (prep ?? 0) + (transit ?? 0);

  return (
    <div className="rounded-[8px] border border-line-input bg-surface px-3 py-2 shadow-lg">
      <p className="text-[11px] font-semibold text-ink">{String(label ?? "")}</p>
      <p className="mt-1 text-[11px] text-ink-muted">
        Alistamiento (tuyo):{" "}
        <span className="text-ink-2">{formatNumber(prep, country, 1)} días</span>
      </p>
      <p className="text-[11px] text-ink-muted">
        Tránsito (de la transportadora):{" "}
        <span className="text-ink-2">{formatNumber(transit, country, 1)} días</span>
      </p>
      <p className="mt-1 text-[11px] text-ink-dim">
        Total: {formatNumber(total, country, 1)} días
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

export default function FulfillmentSla({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useApi<FulfillmentSlaRow[]>(
    `/kpis/fulfillment?country=${countryCode}`,
  );

  const model = useMemo(() => {
    const rows = data ?? [];
    if (rows.length === 0) return null;

    const lanes = rows
      .map(toLane)
      .sort((a, b) => b.shipments - a.shipments);

    return {
      lanes,
      prepShare: weightedPrepShare(rows),
      onTime: weightedOnTime(rows),
    };
  }, [data]);

  const SUBTITLE =
    "Cuánto tardas tú en despachar y cuánto tarda la transportadora en llegar.";

  if (loading) {
    return (
      <Card title="Alistamiento y cumplimiento" subtitle={SUBTITLE}>
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Alistamiento y cumplimiento" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!model) {
    return (
      <Card title="Alistamiento y cumplimiento" subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no se puede partir el reloj de entrega"
          instruction="Sube un reporte de guías que incluya la fecha de relación de despacho desde Cargar datos. Sin esa fecha no se sabe cuándo salió el paquete, y no se puede separar tu tiempo del de la transportadora."
        />
      </Card>
    );
  }

  const chartHeight = Math.max(160, model.lanes.length * 34 + 32);

  return (
    <Card title="Alistamiento y cumplimiento" subtitle={SUBTITLE}>
      <div className="flex flex-wrap items-end gap-x-10 gap-y-3">
        <div>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            La espera que causas tú
          </p>
          <p className="mt-1.5 text-[30px] font-bold leading-none text-warning">
            {formatPercent(model.prepShare)}
          </p>
        </div>

        <div className="self-end">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Llegó en la fecha prometida
          </p>
          <p className="mt-1 text-[16px] font-semibold leading-none text-ink-muted">
            {formatPercent(model.onTime)}
          </p>
        </div>
      </div>

      <p className="mt-3 max-w-2xl text-[11.5px] leading-relaxed text-ink-dim">
        {model.prepShare === null
          ? "Aún no hay suficientes fechas de despacho para saber qué parte de la espera ocurre antes de que el paquete salga."
          : `El ${formatPercent(model.prepShare)} de la espera está de tu lado: pasa entre que creas la guía y que el paquete sale de tu bodega, antes de que la transportadora lo toque. Es la mitad del reloj que puedes arreglar sin renegociar nada.`}
      </p>

      <div className="mt-4">
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart
            data={model.lanes}
            layout="vertical"
            margin={{ top: 4, right: 12, bottom: 4, left: 4 }}
            barCategoryGap="28%"
          >
            <CartesianGrid
              stroke="var(--color-line)"
              strokeDasharray="3 3"
              horizontal={false}
            />
            <XAxis
              type="number"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
              tickFormatter={(value: number) => `${formatNumber(value, country, 0)} d`}
            />
            <YAxis
              type="category"
              dataKey="label"
              axisLine={false}
              tickLine={false}
              width={132}
              tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
            />
            <Tooltip
              cursor={{ fill: "var(--color-line-subtle)" }}
              content={<ChartTooltip country={country} />}
            />
            <Bar
              dataKey="prep_days"
              stackId="clock"
              fill={PREP_COLOUR}
              radius={[3, 0, 0, 3]}
              isAnimationActive={false}
            />
            <Bar
              dataKey="transit_days"
              stackId="clock"
              fill={TRANSIT_COLOUR}
              radius={[0, 3, 3, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-[11px] text-ink-dim">
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block size-[8px] rounded-[2px]"
            style={{ background: PREP_COLOUR }}
          />
          Alistamiento: mediana de días hasta que el paquete sale (tuyo)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block size-[8px] rounded-[2px]"
            style={{ background: TRANSIT_COLOUR }}
          />
          Tránsito: promedio de días hasta entregar (de la transportadora)
        </span>
      </div>

      <div className="mt-4 overflow-x-auto border-t border-line-subtle pt-3">
        <table className="w-full min-w-[520px] border-collapse text-[12px]">
          <thead>
            <tr>
              <th
                scope="col"
                className="px-2 py-1.5 text-left text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                Transportadora
              </th>
              <th
                scope="col"
                className="px-2 py-1.5 text-right text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                Guías
              </th>
              <th
                scope="col"
                className="px-2 py-1.5 text-left text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim"
              >
                % en la fecha prometida
              </th>
            </tr>
          </thead>
          <tbody>
            {model.lanes.map((lane) => (
              <tr key={lane.key} className="border-t border-line-row">
                <td className="max-w-[220px] px-2 py-2 text-left text-ink-2">
                  <span className="block truncate">{lane.label}</span>
                </td>
                <td className="px-2 py-2 text-right text-ink-2">
                  {formatNumber(lane.shipments, country, 0)}
                </td>
                <td className="px-2 py-2">
                  {lane.measurable_count > 0 ? (
                    <MicroBar
                      value={lane.on_time_pct}
                      max={100}
                      tone={onTimeTone(lane.on_time_pct)}
                      label={formatPercent(lane.on_time_pct)}
                    />
                  ) : (
                    <span className="text-[11px] text-ink-dim">
                      sin fecha prometida
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
