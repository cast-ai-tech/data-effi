"use client";

/**
 * Delivery curve by dispatch cohort.
 *
 * Each line is one week of despachos, followed day by day as its guides get
 * delivered. Comparing weeks at the same `days_since` is the only fair way to
 * tell whether logistics is getting better or worse.
 *
 * IMMATURE POINTS ARE NOT PLOTTED. A cohort dispatched three days ago has not
 * had the chance to deliver yet; drawing it next to a mature week makes this
 * week look like a catastrophe and triggers panic decisions on non-data.
 */

import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { WidgetProps } from "@/components/widgets/types";
import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import { useRangedApi } from "@/lib/date-range";
import { formatDate, formatPercent, parseIsoDate } from "@/lib/format";
import type { CohortRow } from "@/lib/types";

/** Most recent week first: accent for "now", neutral grey for the oldest. */
const WEEK_COLOURS = ["#29a9e0", "#21c08a", "#f5a83c", "#5b6272"];

const MAX_WEEKS = 4;
const MAX_DAYS = 30;

interface WeekSeries {
  key: string;
  label: string;
  colour: string;
  /** The oldest week on screen is dashed, so "now" reads as the solid line. */
  dashed: boolean;
}

interface ChartRow {
  days_since: number;
  [weekKey: string]: number | null;
}

/** Monday of the ISO week containing `date`, at local midnight. */
function isoWeekStart(date: Date): Date {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const mondayOffset = (start.getDay() + 6) % 7;
  start.setDate(start.getDate() - mondayOffset);
  return start;
}

/** Sortable "2026-W33" key. Lexicographic order equals chronological order. */
function isoWeekKey(date: Date): string {
  const thursday = isoWeekStart(date);
  thursday.setDate(thursday.getDate() + 3);

  const isoYear = thursday.getFullYear();
  const firstThursday = isoWeekStart(new Date(isoYear, 0, 4));
  firstThursday.setDate(firstThursday.getDate() + 3);

  const week =
    1 + Math.round((thursday.getTime() - firstThursday.getTime()) / (7 * 86_400_000));
  return `${isoYear}-W${String(week).padStart(2, "0")}`;
}

export default function CohortCurve({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<CohortRow[]>(
    `/kpis/cohorts?country=${countryCode}&only_observable=true`,
  );

  const maturationDays = data?.[0]?.maturation_days ?? null;

  const { series, chartData } = useMemo(() => {
    const rows = data ?? [];

    // weekKey -> days_since -> running mean of the cohorts inside that week.
    const byWeek = new Map<string, Map<number, { sum: number; count: number }>>();
    const weekStart = new Map<string, Date>();

    for (const row of rows) {
      // `is_observable === false` means the cohort has not had time to deliver.
      if (!row.is_observable) continue;
      if (row.delivery_rate_pct === null || !Number.isFinite(row.delivery_rate_pct)) continue;
      if (row.days_since < 0 || row.days_since > MAX_DAYS) continue;

      const cohortDate = parseIsoDate(row.cohort_date);
      if (!cohortDate) continue;

      const key = isoWeekKey(cohortDate);
      if (!weekStart.has(key)) weekStart.set(key, isoWeekStart(cohortDate));

      let days = byWeek.get(key);
      if (!days) {
        days = new Map();
        byWeek.set(key, days);
      }

      const bucket = days.get(row.days_since) ?? { sum: 0, count: 0 };
      bucket.sum += row.delivery_rate_pct;
      bucket.count += 1;
      days.set(row.days_since, bucket);
    }

    const keys = [...byWeek.keys()].sort().reverse().slice(0, MAX_WEEKS);

    const built: WeekSeries[] = keys.map((key, index) => {
      const start = weekStart.get(key);
      return {
        key,
        label: start ? `Sem. ${formatDate(start, country)}` : key,
        colour: WEEK_COLOURS[Math.min(index, WEEK_COLOURS.length - 1)],
        dashed: index === keys.length - 1 && keys.length > 1,
      };
    });

    const points: ChartRow[] = [];
    for (let day = 0; day <= MAX_DAYS; day += 1) {
      const point: ChartRow = { days_since: day };
      for (const key of keys) {
        const bucket = byWeek.get(key)?.get(day);
        point[key] = bucket && bucket.count > 0 ? bucket.sum / bucket.count : null;
      }
      points.push(point);
    }

    return { series: built, chartData: points };
  }, [data, country]);

  if (loading) {
    return (
      <Card title="Curva de entrega por cohorte" subtitle="Días desde el despacho">
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Curva de entrega por cohorte" subtitle="Días desde el despacho">
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (series.length === 0) {
    return (
      <Card title="Curva de entrega por cohorte" subtitle="Días desde el despacho">
        <EmptyState
          title="Todavía no hay cohortes maduras"
          instruction={
            maturationDays !== null
              ? `Ninguna semana de despachos cumple aún los ${maturationDays} días de maduración. Vuelve cuando el primer lote los complete, o ajusta los días de maduración del país en Configuración.`
              : "Ninguna semana de despachos cumple aún el período de maduración. Ajusta los días de maduración del país en Configuración, o espera a que el primer lote los complete."
          }
        />
      </Card>
    );
  }

  return (
    <Card
      title="Curva de entrega por cohorte"
      subtitle="Cada línea es una semana de despachos, seguida día a día"
    >
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 12, right: 12, bottom: 4, left: -8 }}>
          <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="days_since"
            type="number"
            domain={[0, MAX_DAYS]}
            ticks={[0, 5, 10, 15, 20, 25, 30]}
            tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--color-line)" }}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) => formatPercent(value, 0)}
          />
          <Tooltip
            isAnimationActive={false}
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-line-strong)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--color-ink-dim)", fontSize: 11 }}
            itemStyle={{ fontSize: 12 }}
            labelFormatter={(label) => `Día ${label} desde el despacho`}
            formatter={(value) =>
              formatPercent(typeof value === "number" ? value : null, 1)
            }
          />
          <Legend
            iconType="plainline"
            wrapperStyle={{ fontSize: 11, color: "var(--color-ink-dim)" }}
          />

          {maturationDays !== null && (
            <ReferenceLine
              x={maturationDays}
              stroke="var(--color-ink-dim)"
              strokeDasharray="3 3"
              label={{
                value: "Maduración",
                position: "top",
                fill: "var(--color-ink-dim)",
                fontSize: 11,
              }}
            />
          )}

          {series.map((week) => (
            <Line
              key={week.key}
              type="monotone"
              dataKey={week.key}
              name={week.label}
              stroke={week.colour}
              strokeWidth={2}
              strokeDasharray={week.dashed ? "4 3" : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>

      <p className="mt-3 text-[11.5px] leading-relaxed text-ink-dim">
        {maturationDays !== null
          ? `Solo se grafican los días que la cohorte ya alcanzó a vivir: una semana despachada hace menos de ${maturationDays} días todavía no tuvo la oportunidad de entregar, y dibujarla completa la haría ver peor de lo que es.`
          : "Solo se grafican los días que la cohorte ya alcanzó a vivir: una semana recién despachada todavía no tuvo la oportunidad de entregar, y dibujarla completa la haría ver peor de lo que es."}
      </p>
    </Card>
  );
}
