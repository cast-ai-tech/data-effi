"use client";

/**
 * The four numbers an operator checks before anything else.
 *
 * Contribution leads because it is the only figure that answers "did this
 * country make money". The other three exist to explain it: how much volume
 * produced it, how much of that volume actually arrived, and how much cash is
 * still sitting in a van somewhere.
 *
 * Every figure is an aggregate over the WHOLE range - never the last day, never
 * an average of daily percentages. A weighted delivery rate and a mean of daily
 * rates diverge badly on low-volume days, and the mean is the one that lies.
 */

import { useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, XAxis, YAxis } from "recharts";

import { Card, Delta, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import type { DailyContribution } from "@/lib/types";

/** How many trailing points a sparkline shows. Older than this is not a trend. */
const SPARK_POINTS = 30;

interface SparkPoint {
  day: string;
  v: number;
}

interface Kpi {
  key: string;
  label: string;
  figure: string;
  /** Change of the most recent half of the range against the earlier half. */
  delta: number | null;
  deltaSuffix: string;
  /** True when a fall is the good outcome. */
  invertDelta: boolean;
  figureClass: string;
  stroke: string;
  points: SparkPoint[];
  note: string | null;
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

function sumBy(
  rows: DailyContribution[],
  pick: (row: DailyContribution) => number | null,
): number {
  return rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
}

/** Weighted delivery rate: delivered over everything that reached an end state. */
function deliveryRate(rows: DailyContribution[]): number | null {
  const terminal = rows.reduce(
    (total, row) => total + row.delivered + row.returned + row.dead,
    0,
  );
  if (terminal <= 0) return null;
  return (sumBy(rows, (row) => row.delivered) / terminal) * 100;
}

/**
 * Declared value still in transit.
 *
 * The API gives declared value per day, not per shipment state, so we take the
 * in-transit share of each day's shipments. It is an approximation, and an
 * honest one: the alternative is showing nothing.
 */
function capitalInTransit(rows: DailyContribution[]): number {
  return rows.reduce((total, row) => {
    if (row.shipments <= 0) return total;
    return total + ((row.declared_value ?? 0) * row.in_transit) / row.shipments;
  }, 0);
}

/** Relative change in percent. Null when the base is zero: there is no ratio. */
function percentChange(recent: number, earlier: number): number | null {
  if (!Number.isFinite(recent) || !Number.isFinite(earlier) || earlier === 0) return null;
  return ((recent - earlier) / Math.abs(earlier)) * 100;
}

/** Difference in percentage points, for metrics that are already percentages. */
/** Delivered over everything dispatched: the number that is still rising while
 *  guides are open, and the one the operator means by "¿cuánto se entregó?". */
function dispatchedRate(rows: DailyContribution[]): number | null {
  const shipments = sumBy(rows, (row) => row.shipments);
  if (shipments <= 0) return null;
  return (sumBy(rows, (row) => row.delivered) / shipments) * 100;
}

function pointChange(recent: number | null, earlier: number | null): number | null {
  if (recent === null || earlier === null) return null;
  return recent - earlier;
}

function trailing(rows: DailyContribution[], pick: (row: DailyContribution) => number): SparkPoint[] {
  return rows.slice(-SPARK_POINTS).map((row) => ({ day: row.day, v: pick(row) }));
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

export default function KpiContribution({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<DailyContribution[]>(
    `/kpis/daily-contribution?country=${countryCode}`,
  );

  const kpis = useMemo<Kpi[] | null>(() => {
    if (!data || data.length === 0) return null;

    const rows = [...data].sort((a, b) => a.day.localeCompare(b.day));
    const split = Math.floor(rows.length / 2);
    const earlier = split > 0 ? rows.slice(0, split) : [];
    const recent = split > 0 ? rows.slice(split) : [];
    const comparable = earlier.length > 0 && recent.length > 0;

    const contribution = sumBy(rows, (row) => row.contribution);
    const shipments = sumBy(rows, (row) => row.shipments);
    const rate = deliveryRate(rows);
    const dispatched = dispatchedRate(rows);
    const inTransit = capitalInTransit(rows);

    const adSpendMissing = rows.some((row) => row.ad_spend_missing);

    return [
      {
        key: "contribution",
        label: "Contribución total",
        figure: formatMoney(contribution, country),
        delta: comparable
          ? percentChange(
              sumBy(recent, (row) => row.contribution),
              sumBy(earlier, (row) => row.contribution),
            )
          : null,
        deltaSuffix: "%",
        invertDelta: false,
        figureClass: contribution >= 0 ? "text-positive-ink" : "text-negative-ink",
        stroke:
          contribution >= 0 ? "var(--color-positive)" : "var(--color-negative)",
        points: trailing(rows, (row) => row.contribution ?? 0),
        note: adSpendMissing
          ? "Sin datos de pauta: la contribución no descuenta medios."
          : null,
      },
      {
        key: "shipments",
        label: "Despachos",
        figure: formatNumber(shipments, country, 0),
        delta: comparable
          ? percentChange(
              sumBy(recent, (row) => row.shipments),
              sumBy(earlier, (row) => row.shipments),
            )
          : null,
        deltaSuffix: "%",
        invertDelta: false,
        figureClass: "text-ink",
        // Volume is neither good nor bad on its own, so it gets a neutral line.
        stroke: "var(--color-neutral-series)",
        points: trailing(rows, (row) => row.shipments),
        note: null,
      },
      {
        key: "delivery-rate",
        label: "% entregado de lo despachado",
        figure: formatPercent(dispatched),
        delta: comparable ? pointChange(dispatchedRate(recent), dispatchedRate(earlier)) : null,
        deltaSuffix: " pp",
        invertDelta: false,
        figureClass: "text-ink",
        stroke: "var(--color-positive)",
        points: trailing(rows, (row) =>
          row.shipments > 0 ? (row.delivered / row.shipments) * 100 : 0,
        ),
        // The closed-only rate stays visible: it is what the day will settle
        // at, but it reads as "casi todo entregado" while most guides are open.
        note: rate === null ? null : `Sobre las ya cerradas: ${formatPercent(rate)}`,
      },
      {
        key: "capital-in-transit",
        label: "Capital en tránsito",
        figure: formatMoney(inTransit, country),
        // Cash stuck in transit is exposure: less of it is the better outcome.
        delta: comparable
          ? percentChange(capitalInTransit(recent), capitalInTransit(earlier))
          : null,
        deltaSuffix: "%",
        invertDelta: true,
        figureClass: "text-ink",
        stroke: "var(--color-warning)",
        points: trailing(rows, (row) =>
          row.shipments > 0
            ? ((row.declared_value ?? 0) * row.in_transit) / row.shipments
            : 0,
        ),
        note: null,
      },
    ];
  }, [data, country]);

  if (loading) {
    return (
      <Card>
        <SkeletonRows rows={4} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!kpis) {
    return (
      <Card>
        <EmptyState
          title="Todavía no hay guías en este rango"
          instruction="Sube un archivo de guías desde Cargar datos, o amplía el rango de fechas: los KPIs se calculan sobre los despachos del periodo seleccionado."
        />
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {kpis.map((kpi) => (
        <Card key={kpi.key} bodyClassName="p-4">
          <p className="text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
            {kpi.label}
          </p>

          <p
            className={`mt-1.5 text-4xl font-bold leading-none ${kpi.figureClass}`}
          >
            {kpi.figure}
          </p>

          <div className="mt-2">
            <Delta value={kpi.delta} suffix={kpi.deltaSuffix} invert={kpi.invertDelta} />
          </div>

          <div className="mt-3 -mx-1">
            <ResponsiveContainer width="100%" height={48}>
              <AreaChart data={kpi.points} margin={{ top: 2, right: 2, bottom: 0, left: 2 }}>
                <XAxis dataKey="day" hide />
                <YAxis hide domain={["dataMin", "dataMax"]} />
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke={kpi.stroke}
                  strokeWidth={1.5}
                  fill={kpi.stroke}
                  fillOpacity={0.12}
                  dot={false}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {kpi.note && (
            <p className="mt-2 text-xs leading-snug text-warning-ink">{kpi.note}</p>
          )}
        </Card>
      ))}
    </div>
  );
}
