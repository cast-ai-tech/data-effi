"use client";

/**
 * Confirmation funnel from the customer-service source.
 *
 * In COD the order is not real until someone answers the phone. This is the
 * daily shape of that conversation: how many were confirmed, how many said no,
 * how many never answered, and how many are still open - plus how many attempts
 * it took on average, which is the number that decides how many agents you need.
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

import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";
import { formatDate, formatDayShort, formatNumber, formatPercent } from "@/lib/format";
import type { CsRow } from "@/lib/types";

const SERIES = [
  { key: "confirmed", label: "Confirmados", colour: "#21c08a" },
  { key: "rejected", label: "Rechazados", colour: "#ff6259" },
  { key: "no_answer", label: "Sin respuesta", colour: "#5b6272" },
  { key: "pending", label: "Pendientes", colour: "#f5a83c" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];

interface Totals {
  interactions: number;
  confirmed: number;
  rejected: number;
  noAnswer: number;
  pending: number;
  confirmationRate: number | null;
  avgAttempts: number | null;
}

function summarize(rows: CsRow[]): Totals {
  let interactions = 0;
  let confirmed = 0;
  let rejected = 0;
  let noAnswer = 0;
  let pending = 0;
  // Attempts are a per-day average; re-averaging them flat would let a quiet
  // Sunday weigh as much as a Monday with ten times the volume.
  let attemptsWeight = 0;
  let attemptsSum = 0;

  for (const row of rows) {
    interactions += row.interactions;
    confirmed += row.confirmed;
    rejected += row.rejected;
    noAnswer += row.no_answer;
    pending += row.pending;

    if (row.avg_attempts !== null && Number.isFinite(row.avg_attempts)) {
      attemptsWeight += row.interactions;
      attemptsSum += row.avg_attempts * row.interactions;
    }
  }

  return {
    interactions,
    confirmed,
    rejected,
    noAnswer,
    pending,
    confirmationRate: interactions > 0 ? (confirmed / interactions) * 100 : null,
    avgAttempts: attemptsWeight > 0 ? attemptsSum / attemptsWeight : null,
  };
}

export default function CsConfirmation({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<CsRow[]>(
    `/kpis/cs?country=${countryCode}`,
  );

  const rows = useMemo(() => data ?? [], [data]);
  const totals = useMemo(() => summarize(rows), [rows]);

  const subtitle = "Resultado de cada contacto con el cliente, día por día";

  if (loading) {
    return (
      <Card title="Confirmación de pedidos" subtitle={subtitle}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Confirmación de pedidos" subtitle={subtitle}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Confirmación de pedidos" subtitle={subtitle}>
        <EmptyState
          title="Falta conectar una fuente de servicio al cliente"
          instruction="Conecte el CRM o la plataforma de WhatsApp donde su equipo confirma los pedidos (o cargue el archivo de gestiones). Sin esa fuente Master Data no sabe cuántos contactos terminaron en confirmación."
        />
      </Card>
    );
  }

  return (
    <Card title="Confirmación de pedidos" subtitle={subtitle}>
      <div className="mb-4 flex flex-wrap items-end gap-x-8 gap-y-3">
        <Headline
          label="Tasa de confirmación"
          value={formatPercent(totals.confirmationRate)}
          hint={`${formatNumber(totals.confirmed, country, 0)} de ${formatNumber(
            totals.interactions,
            country,
            0,
          )} gestiones`}
        />
        <Headline
          label="Intentos por gestión"
          value={
            totals.avgAttempts === null
              ? "—"
              : formatNumber(totals.avgAttempts, country, 1)
          }
          hint="promedio ponderado por volumen"
        />
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "var(--color-line)" }}
            tickFormatter={(value: string) => formatDayShort(value)}
            minTickGap={16}
          />
          <YAxis
            tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(value: number) => formatNumber(value, country, 0)}
          />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            content={<CsTooltip country={country} />}
          />
          {SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              stackId="gestiones"
              name={series.label}
              fill={series.colour}
              isAnimationActive={false}
              maxBarSize={26}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>

      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {SERIES.map((series) => (
          <li
            key={series.key}
            className="flex items-center gap-1.5 text-xs text-ink-muted"
          >
            <span
              className="inline-block size-[7px] rounded-full"
              style={{ background: series.colour }}
            />
            {series.label}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Headline({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div>
      <p className="text-xs font-bold uppercase tracking-[0.06em] text-ink-faint">
        {label}
      </p>
      <p className="mt-0.5 text-2xl font-semibold leading-none text-ink">{value}</p>
      <p className="mt-1 text-xs text-ink-dim">{hint}</p>
    </div>
  );
}

function CsTooltip({
  active,
  payload,
  country,
}: {
  active?: boolean;
  payload?: Array<{ payload?: CsRow }>;
  country: FormatCountry;
}) {
  const row = active ? payload?.[0]?.payload : undefined;
  if (!row) return null;

  const values: Record<SeriesKey, number> = {
    confirmed: row.confirmed,
    rejected: row.rejected,
    no_answer: row.no_answer,
    pending: row.pending,
  };

  return (
    <div className="rounded-control border border-line-strong bg-surface px-3 py-2 shadow-pop">
      <p className="mb-1.5 text-sm font-semibold text-ink">
        {formatDate(row.day, country)}
      </p>
      <dl className="space-y-0.5 text-sm">
        {SERIES.map((series) => (
          <div key={series.key} className="flex items-baseline justify-between gap-5">
            <dt className="flex items-center gap-1.5 text-ink-dim">
              <span
                className="inline-block size-[6px] rounded-full"
                style={{ background: series.colour }}
              />
              {series.label}
            </dt>
            <dd className="text-ink-2">
              {formatNumber(values[series.key], country, 0)}
            </dd>
          </div>
        ))}
        <div className="mt-1 flex items-baseline justify-between gap-5 border-t border-line-subtle pt-1">
          <dt className="text-ink-dim">% confirmación</dt>
          <dd className="text-ink">{formatPercent(row.confirmation_rate_pct)}</dd>
        </div>
      </dl>
    </div>
  );
}
