"use client";

/**
 * What the media actually cost, measured against guías that arrived.
 *
 * The ads platform reports a cost per purchase the moment the order is placed.
 * In COD that order is a promise, not money: a third of it comes back. This
 * widget puts the platform's number and the real one side by side, because the
 * gap between them is the single most expensive misunderstanding in the model.
 */

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";
import { formatDayShort, formatMoney, formatNumber, formatPercent } from "@/lib/format";
import type { CpaRow } from "@/lib/types";

interface DayPoint {
  day: string;
  label: string;
  ad_spend: number;
  cpa_delivered: number | null;
}

/** A ratio, not money: "2,45x". */
function formatRatio(value: number | null, country: FormatCountry): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${formatNumber(value, country, 2)}x`;
}

function safeDivide(numerator: number, denominator: number): number | null {
  if (denominator === 0 || !Number.isFinite(denominator)) return null;
  const result = numerator / denominator;
  return Number.isFinite(result) ? result : null;
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

  const spend = read("ad_spend");
  const cpa = read("cpa_delivered");

  return (
    <div className="rounded-control border border-line-input bg-surface px-3 py-2 shadow-pop">
      <p className="text-xs font-semibold text-ink">{String(label ?? "")}</p>
      <p className="mt-1 text-xs text-ink-muted">
        Inversión: <span className="text-ink-2">{formatMoney(spend, country)}</span>
      </p>
      <p className="text-xs text-ink-muted">
        CPA entregado: <span className="text-ink-2">{formatMoney(cpa, country)}</span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

export default function CpaRoas({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<CpaRow[]>(
    `/kpis/cpa?country=${countryCode}`,
  );

  const model = useMemo(() => {
    if (!data || data.length === 0) return null;

    const rows = [...data].sort((a, b) => a.day.localeCompare(b.day));

    const adSpend = rows.reduce((total, row) => total + row.ad_spend, 0);
    const delivered = rows.reduce((total, row) => total + row.delivered, 0);
    const shipments = rows.reduce((total, row) => total + row.shipments, 0);
    const revenue = rows.reduce((total, row) => total + row.revenue, 0);

    const cpaDelivered = safeDivide(adSpend, delivered);
    const cpaDispatched = safeDivide(adSpend, shipments);
    const roas = safeDivide(revenue, adSpend);
    const deliveryShare = safeDivide(delivered, shipments);

    // How much the real cost exceeds the one the platform reports.
    const gapPct =
      cpaDelivered !== null && cpaDispatched !== null && cpaDispatched !== 0
        ? (cpaDelivered / cpaDispatched - 1) * 100
        : null;

    const points: DayPoint[] = rows.map((row) => ({
      day: row.day,
      label: formatDayShort(row.day),
      ad_spend: row.ad_spend,
      cpa_delivered: row.cpa_delivered ?? safeDivide(row.ad_spend, row.delivered),
    }));

    return {
      points,
      cpaDelivered,
      cpaDispatched,
      roas,
      gapPct,
      deliveryShare: deliveryShare === null ? null : deliveryShare * 100,
    };
  }, [data]);

  if (loading) {
    return (
      <Card title="Costo por entrega y retorno de pauta">
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Costo por entrega y retorno de pauta">
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!model) {
    return (
      <Card title="Costo por entrega y retorno de pauta">
        <EmptyState
          title="Falta conectar una plataforma de pauta"
          instruction="Conecta Meta Ads, Google Ads o TikTok Ads en Configuración → Conexiones. Sin la inversión diaria no se puede calcular cuánto cuesta cada guía entregada."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Costo por entrega y retorno de pauta"
      subtitle="Medido sobre guías entregadas, no sobre pedidos tomados"
    >
      <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
            CPA entregado
          </p>
          <p className="mt-1.5 text-4xl font-bold leading-none text-ink">
            {formatMoney(model.cpaDelivered, country)}
          </p>
        </div>

        <div>
          <p className="text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
            ROAS neto
          </p>
          <p
            className={`mt-1.5 text-4xl font-bold leading-none ${
              model.roas !== null && model.roas < 1 ? "text-negative-ink" : "text-positive-ink"
            }`}
          >
            {formatRatio(model.roas, country)}
          </p>
        </div>

        <div className="self-end">
          <p className="text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
            CPA despachado
          </p>
          <p className="mt-1 text-lg font-semibold leading-none text-ink-muted">
            {formatMoney(model.cpaDispatched, country)}
          </p>
        </div>
      </div>

      {model.gapPct !== null && model.deliveryShare !== null && (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-dim">
          Tu CPA real es {formatPercent(model.gapPct)} más alto que el que reporta la
          plataforma de pauta, porque solo {formatPercent(model.deliveryShare)} de las
          guías se entrega.
        </p>
      )}

      <div className="mt-4">
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart
            data={model.points}
            margin={{ top: 8, right: 8, bottom: 4, left: 8 }}
          >
            <CartesianGrid
              stroke="var(--color-line)"
              strokeDasharray="3 3"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              tick={{ fill: "var(--color-ink-muted)", fontSize: 11 }}
              minTickGap={16}
            />
            <YAxis
              yAxisId="spend"
              axisLine={false}
              tickLine={false}
              width={54}
              tick={{ fill: "var(--color-ink-dim)", fontSize: 10 }}
              tickFormatter={(value: number) => formatMoney(value, country, { compact: true })}
            />
            <YAxis
              yAxisId="cpa"
              orientation="right"
              axisLine={false}
              tickLine={false}
              width={54}
              tick={{ fill: "var(--color-ink-dim)", fontSize: 10 }}
              tickFormatter={(value: number) => formatMoney(value, country, { compact: true })}
            />
            <Tooltip
              cursor={{ fill: "var(--color-line-subtle)" }}
              content={<ChartTooltip country={country} />}
            />
            {/* Spend is volume, not a verdict - it gets the neutral bar. */}
            <Bar
              yAxisId="spend"
              dataKey="ad_spend"
              fill="#3a4152"
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
            {/* The cost to watch. */}
            <Line
              yAxisId="cpa"
              type="monotone"
              dataKey="cpa_delivered"
              stroke="#f5a83c"
              strokeWidth={2}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
