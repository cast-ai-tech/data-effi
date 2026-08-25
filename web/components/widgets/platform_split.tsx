"use client";

/**
 * Effi, Dropi and the manual upload on one line each - the strip at the bottom
 * of the operator's report: total guides, total returns, combined return rate,
 * and "plataforma con más ventas".
 *
 * "Ventas" is not printed here. A guide is a sale once it is delivered; before
 * that it is a parcel. The strip says guías, and next to the share it says how
 * many of them arrived.
 *
 * This card IGNORES the platform picker on purpose and says so on its frame:
 * it is the comparison between platforms, and a filtered comparison is one bar
 * at 100%.
 */

import { useMemo } from "react";

import { Card, Chip, EmptyState, ErrorState, MicroBar, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatNumber, formatPercent, type FormatCountry } from "@/lib/format";
import { platformSwatch } from "@/lib/status";
import type { PlatformSummaryRow } from "@/lib/types";

const TITLE = "Plataformas";
const SUBTITLE = "Qué parte del volumen lleva cada una y cómo le va";

const SHORT_SAMPLE_HINT =
  "Menos de 10 guías cerradas: los porcentajes son un estimado, no una medición.";

export interface Combined {
  shipments: number;
  entregada: number;
  devolucion: number;
  cerradas: number;
  pctDevolucionTotal: number | null;
  pctDevolucionCerradas: number | null;
  leader: PlatformSummaryRow | null;
}

export function combine(rows: readonly PlatformSummaryRow[]): Combined {
  let shipments = 0;
  let entregada = 0;
  let devolucion = 0;
  let cerradas = 0;
  let leader: PlatformSummaryRow | null = null;
  for (const row of rows) {
    shipments += row.shipments;
    entregada += row.entregada;
    devolucion += row.devolucion;
    cerradas += row.cerradas;
    if (!leader || row.shipments > leader.shipments) leader = row;
  }
  return {
    shipments,
    entregada,
    devolucion,
    cerradas,
    pctDevolucionTotal: shipments > 0 ? (devolucion / shipments) * 100 : null,
    pctDevolucionCerradas: cerradas > 0 ? (devolucion / cerradas) * 100 : null,
    leader,
  };
}

function deliveryTone(pct: number | null): "positive" | "warning" | "negative" | "neutral" {
  if (pct === null || !Number.isFinite(pct)) return "neutral";
  if (pct >= 75) return "positive";
  if (pct >= 60) return "warning";
  return "negative";
}

/** The stacked bar: one segment per platform, width = its share. */
export function ShareBar({ rows }: { rows: readonly PlatformSummaryRow[] }) {
  return (
    <div
      className="flex h-[10px] w-full overflow-hidden rounded-full bg-track"
      role="img"
      aria-label={rows
        .map((row) => `${row.platform_name}: ${formatPercent(row.share_pct)}`)
        .join(", ")}
    >
      {rows.map((row) => (
        <div
          key={row.platform_code}
          className={cx("h-full", platformSwatch(row.platform_code))}
          style={{ width: `${Math.max(0, Math.min(100, row.share_pct ?? 0))}%` }}
          title={`${row.platform_name} · ${formatPercent(row.share_pct)}`}
        />
      ))}
    </div>
  );
}

/** The four figures of the consolidated strip, reusable by the report page. */
export function ConsolidatedStrip({
  rows,
  country,
}: {
  rows: readonly PlatformSummaryRow[];
  country: FormatCountry;
}) {
  const total = useMemo(() => combine(rows), [rows]);
  const leader = total.leader;

  return (
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Figure label="Guías totales combinadas" value={formatNumber(total.shipments, country, 0)} />
      <Figure
        label="Devoluciones combinadas"
        value={formatNumber(total.devolucion, country, 0)}
        tone="text-negative-ink"
      />
      <Figure
        label="% devolución combinado"
        value={formatPercent(total.pctDevolucionTotal)}
        hint={`Sobre cerradas: ${formatPercent(total.pctDevolucionCerradas)}`}
      />
      <Figure
        label="Plataforma con más guías"
        value={leader ? leader.platform_name : "—"}
        hint={
          leader
            ? `${formatNumber(leader.shipments, country, 0)} guías (${formatPercent(leader.share_pct)})`
            : undefined
        }
      />
    </dl>
  );
}

function Figure({
  label,
  value,
  hint,
  tone = "text-ink",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="rounded-control border border-line-subtle bg-sunken px-3.5 py-2.5">
      <dt className="text-xs font-bold uppercase tracking-[0.06em] text-ink-faint">
        {label}
      </dt>
      <dd className={cx("mt-0.5 text-xl font-bold leading-tight", tone)}>{value}</dd>
      {hint && <dd className="mt-0.5 text-xs text-ink-dim">{hint}</dd>}
    </div>
  );
}

export default function PlatformSplit({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<PlatformSummaryRow[]>(
    `/kpis/platforms?country=${countryCode}`,
  );

  const rows = useMemo(
    () => [...(data ?? [])].sort((a, b) => b.shipments - a.shipments),
    [data],
  );

  if (loading) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <SkeletonRows rows={4} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <EmptyState
          title="Ninguna plataforma con guías"
          instruction="No hay guías en el rango seleccionado. Amplía las fechas, o sube el reporte de Effi o Dropi a su conexión en Configuración → Conexiones."
        />
      </Card>
    );
  }

  return (
    <Card title={TITLE} subtitle={SUBTITLE}>
      <ShareBar rows={rows} />

      <ul className="mt-3 divide-y divide-line-row">
        {rows.map((row, index) => (
          <li key={row.platform_code} className="flex items-center gap-3 py-2.5">
            <span
              aria-hidden
              className={cx("size-2.5 shrink-0 rounded-full", platformSwatch(row.platform_code))}
            />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="truncate text-base font-semibold text-ink">
                  {row.platform_name}
                </span>
                {index === 0 && rows.length > 1 && <Chip tone="accent">más guías</Chip>}
                {row.sample_quality === "muestra_corta" && (
                  <abbr
                    title={SHORT_SAMPLE_HINT}
                    className="cursor-help text-xs font-semibold text-ink-dim no-underline"
                  >
                    ~
                  </abbr>
                )}
              </span>
              <span className="block text-xs text-ink-dim">
                {formatNumber(row.shipments, country, 0)} guías ·{" "}
                {formatPercent(row.share_pct)} del total ·{" "}
                {formatNumber(row.devolucion, country, 0)} devueltas
              </span>
            </span>

            <span className="w-[104px] shrink-0 sm:w-[130px]">
              <MicroBar
                value={row.pct_entrega_cerradas}
                max={100}
                tone={deliveryTone(row.pct_entrega_cerradas)}
                label={formatPercent(row.pct_entrega_cerradas)}
              />
              <span className="mt-0.5 block text-right text-xs text-ink-faint">
                entrega (cerradas)
              </span>
            </span>

            <span className="w-[104px] shrink-0 sm:w-[130px]">
              <MicroBar
                value={row.pct_devolucion_total}
                max={100}
                tone="negative"
                label={formatPercent(row.pct_devolucion_total)}
              />
              <span className="mt-0.5 block text-right text-xs text-ink-faint">
                devolución
              </span>
            </span>
          </li>
        ))}
      </ul>

      {rows.length > 1 && (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <ConsolidatedStrip rows={rows} country={country} />
        </div>
      )}
    </Card>
  );
}
