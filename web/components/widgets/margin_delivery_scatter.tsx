"use client";

/**
 * Margin vs. delivery, one bubble per product.
 *
 * In COD the two numbers that decide a product's fate are independent: a product
 * can have a beautiful margin and never arrive, or arrive perfectly and leave
 * nothing behind. Plotting them against each other splits the catalogue into the
 * four decisions you can actually take - and the bubble size (guías) says how
 * much each decision is worth.
 */

import { useMemo } from "react";
import {
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import type { ProductRow } from "@/lib/types";

/** The line between "arrives" and "does not arrive". */
const DELIVERY_THRESHOLD = 70;
/** The line between "leaves money" and "does not". */
const MARGIN_THRESHOLD = 30;

type Quadrant = "escalar" | "arreglar" | "subir_precio" | "matar";

const QUADRANT_COLOUR: Record<Quadrant, string> = {
  escalar: "#21c08a",
  arreglar: "#f5a83c",
  subir_precio: "#29a9e0",
  matar: "#ff6259",
};

const QUADRANT_LABEL: Record<Quadrant, string> = {
  escalar: "Escalar",
  arreglar: "Arreglar logística",
  subir_precio: "Subir precio",
  matar: "Matar",
};

interface ScatterPoint {
  name: string;
  deliveryRate: number;
  marginPct: number;
  shipments: number;
  contribution: number | null;
  quadrant: Quadrant;
}

function classify(deliveryRate: number, marginPct: number): Quadrant {
  const delivers = deliveryRate >= DELIVERY_THRESHOLD;
  const earns = marginPct >= MARGIN_THRESHOLD;
  if (delivers && earns) return "escalar";
  if (!delivers && earns) return "arreglar";
  if (delivers && !earns) return "subir_precio";
  return "matar";
}

function toPoints(rows: ProductRow[]): ScatterPoint[] {
  const points: ScatterPoint[] = [];

  for (const row of rows) {
    const deliveryRate = row.delivery_rate_pct;
    const marginPct = row.margin_pct;
    // A product missing either axis has no position on this plane. Counting it
    // at zero would invent a "matar" verdict the data never supported.
    if (deliveryRate === null || marginPct === null) continue;
    if (!Number.isFinite(deliveryRate) || !Number.isFinite(marginPct)) continue;

    points.push({
      name: row.product_name,
      deliveryRate,
      marginPct,
      shipments: row.shipments,
      contribution: row.contribution,
      quadrant: classify(deliveryRate, marginPct),
    });
  }

  return points;
}

export default function MarginDeliveryScatter({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<ProductRow[]>(
    `/kpis/products?country=${countryCode}`,
    [countryCode],
  );

  const points = useMemo(() => (data ? toPoints(data) : []), [data]);
  const skipped = (data?.length ?? 0) - points.length;

  const subtitle = "Cada burbuja es un producto; el tamaño son las guías despachadas";

  if (loading) {
    return (
      <Card title="Margen vs. entrega" subtitle={subtitle}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Margen vs. entrega" subtitle={subtitle}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (points.length === 0) {
    return (
      <Card title="Margen vs. entrega" subtitle={subtitle}>
        <EmptyState
          title="Ningún producto se puede ubicar en el plano"
          instruction="Para calcular margen se necesita el costo de cada producto. Cargue el catálogo con costo unitario y vuelva a sincronizar las guías para que Master Data cruce recaudo y costo."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Margen vs. entrega"
      subtitle={subtitle}
      actions={
        skipped > 0 ? (
          <span className="text-xs text-ink-dim">
            {skipped} sin margen o sin % de entrega
          </span>
        ) : undefined
      }
    >
      <div className="relative">
        <QuadrantLabels />

        <ResponsiveContainer width="100%" height={320}>
          <ScatterChart margin={{ top: 8, right: 12, bottom: 24, left: 4 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" />
            <XAxis
              type="number"
              dataKey="deliveryRate"
              name="% entrega"
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "var(--color-line)" }}
              tickFormatter={(value: number) => `${value}%`}
              label={{
                value: "% de entrega",
                position: "insideBottom",
                offset: -14,
                fill: "var(--color-ink-dim)",
                fontSize: 11,
              }}
            />
            <YAxis
              type="number"
              dataKey="marginPct"
              name="margen"
              tick={{ fill: "var(--color-ink-dim)", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={44}
              tickFormatter={(value: number) => `${value}%`}
              label={{
                value: "Margen",
                angle: -90,
                position: "insideLeft",
                fill: "var(--color-ink-dim)",
                fontSize: 11,
              }}
            />
            <ZAxis type="number" dataKey="shipments" range={[40, 520]} name="guías" />

            <ReferenceLine
              x={DELIVERY_THRESHOLD}
              stroke="var(--color-ink-faint)"
              strokeDasharray="4 4"
            />
            <ReferenceLine
              y={MARGIN_THRESHOLD}
              stroke="var(--color-ink-faint)"
              strokeDasharray="4 4"
            />

            <Tooltip
              cursor={{ stroke: "var(--color-line-strong)", strokeDasharray: "3 3" }}
              content={<ScatterTooltip country={country} />}
            />

            <Scatter data={points} isAnimationActive={false} fillOpacity={0.75}>
              {points.map((point) => (
                <Cell
                  key={`${point.name}-${point.deliveryRate}-${point.marginPct}`}
                  fill={QUADRANT_COLOUR[point.quadrant]}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {(Object.keys(QUADRANT_LABEL) as Quadrant[]).map((quadrant) => (
          <li key={quadrant} className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span
              className="inline-block size-[7px] rounded-full"
              style={{ background: QUADRANT_COLOUR[quadrant] }}
            />
            {QUADRANT_LABEL[quadrant]}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/**
 * The four verdicts, written on the plane itself. Absolutely positioned rather
 * than drawn as Recharts labels so they sit behind the bubbles and never steal
 * a hover.
 */
function QuadrantLabels() {
  const base =
    "pointer-events-none absolute text-xs font-semibold uppercase tracking-[0.06em]";
  return (
    <div className="pointer-events-none absolute inset-0 z-[1]" aria-hidden>
      <span className={`${base} right-4 top-1`} style={{ color: QUADRANT_COLOUR.escalar }}>
        {QUADRANT_LABEL.escalar}
      </span>
      <span className={`${base} left-12 top-1`} style={{ color: QUADRANT_COLOUR.arreglar }}>
        {QUADRANT_LABEL.arreglar}
      </span>
      <span
        className={`${base} bottom-9 right-4`}
        style={{ color: QUADRANT_COLOUR.subir_precio }}
      >
        {QUADRANT_LABEL.subir_precio}
      </span>
      <span className={`${base} bottom-9 left-12`} style={{ color: QUADRANT_COLOUR.matar }}>
        {QUADRANT_LABEL.matar}
      </span>
    </div>
  );
}

function ScatterTooltip({
  active,
  payload,
  country,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ScatterPoint }>;
  country: FormatCountry;
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;

  return (
    <div className="rounded-control border border-line-strong bg-surface px-3 py-2 shadow-pop">
      <p className="mb-1.5 max-w-[220px] text-sm font-semibold leading-snug text-ink">
        {point.name}
      </p>
      <dl className="space-y-0.5 text-sm">
        <Row label="% entrega" value={formatPercent(point.deliveryRate)} />
        <Row label="Margen" value={formatPercent(point.marginPct)} />
        <Row label="Guías" value={formatNumber(point.shipments, country, 0)} />
        <Row
          label="Contribución"
          value={formatMoney(point.contribution, country, { compact: true })}
          tone={
            point.contribution !== null && point.contribution < 0
              ? "text-negative-ink"
              : "text-positive-ink"
          }
        />
      </dl>
      <p
        className="mt-1.5 text-xs font-semibold uppercase tracking-[0.06em]"
        style={{ color: QUADRANT_COLOUR[point.quadrant] }}
      >
        {QUADRANT_LABEL[point.quadrant]}
      </p>
    </div>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-ink-dim">{label}</dt>
      <dd className={tone ?? "text-ink-2"}>{value}</dd>
    </div>
  );
}
