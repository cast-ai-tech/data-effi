"use client";

/**
 * Aging of open guides.
 *
 * A ranked list, not a chart: five buckets in a fixed order, each one a bar you
 * can compare at a glance. The colour ramp goes green → amber → red because the
 * older a COD guide gets, the less likely the money is ever collected.
 *
 * The line underneath is the whole point of the widget. Everything above it is
 * context for one sentence: this much money is about to be lost.
 */

import { useMemo } from "react";

import type { WidgetProps } from "@/components/widgets/types";
import { Card, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber } from "@/lib/format";
import type { AgingRow } from "@/lib/types";

/** 0-3, 4-7, 8-12, 13-20, 21+ — read positionally from `bucket_order`. */
const BUCKET_COLOURS = ["#21c08a", "#5fcb9e", "#f5a83c", "#ff6259", "#ff6259"];

/** The 4th and 5th buckets (13-20 and 21+) are the ones at real risk. */
const AT_RISK_FROM_INDEX = 3;

export default function AgingBars({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<AgingRow[]>(
    `/kpis/aging?country=${countryCode}`,
  );

  const buckets = useMemo<AgingRow[]>(
    () => [...(data ?? [])].sort((a, b) => a.bucket_order - b.bucket_order),
    [data],
  );

  const maxShipments = useMemo(
    () => buckets.reduce((max, bucket) => Math.max(max, bucket.shipments), 0),
    [buckets],
  );

  const atRisk = useMemo(() => {
    const slice = buckets.slice(AT_RISK_FROM_INDEX);
    return {
      shipments: slice.reduce((sum, bucket) => sum + bucket.shipments, 0),
      value: slice.reduce((sum, bucket) => sum + (bucket.value_at_risk ?? 0), 0),
    };
  }, [buckets]);

  if (loading) {
    return (
      <Card title="Antigüedad de guías abiertas" subtitle="Días desde el despacho">
        <SkeletonRows rows={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Antigüedad de guías abiertas" subtitle="Días desde el despacho">
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (buckets.length === 0) {
    return (
      <Card title="Antigüedad de guías abiertas" subtitle="Días desde el despacho">
        <EmptyState
          title="No hay guías abiertas"
          instruction="Todas las guías del período están cerradas, o la transportadora aún no ha sincronizado estados. Revisa la última sincronización en Configuración → Conexiones."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Antigüedad de guías abiertas"
      subtitle="Días desde el despacho, sin entregar ni devolver"
    >
      <ul className="space-y-2.5">
        {buckets.map((bucket, index) => {
          const width = maxShipments > 0 ? (bucket.shipments / maxShipments) * 100 : 0;
          const colour = BUCKET_COLOURS[Math.min(index, BUCKET_COLOURS.length - 1)];

          return (
            <li key={bucket.aging_bucket} className="flex items-center gap-3">
              <span className="w-[54px] shrink-0 text-[11.5px] font-medium text-ink-muted">
                {bucket.aging_bucket}
              </span>

              <div className="h-[10px] flex-1 overflow-hidden rounded-full bg-track">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${width}%`, background: colour }}
                />
              </div>

              <span className="w-[64px] shrink-0 text-right text-[12px] font-semibold text-ink-2">
                {formatNumber(bucket.shipments, country, 0)}
              </span>
              <span className="w-[104px] shrink-0 text-right text-[12px] text-ink-muted">
                {formatMoney(bucket.value_at_risk, country)}
              </span>
            </li>
          );
        })}
      </ul>

      <p
        className={cx(
          "mt-4 border-t border-line-subtle pt-3 text-[13px] font-semibold leading-snug",
          atRisk.value > 0 ? "text-negative" : "text-ink-muted",
        )}
      >
        {atRisk.shipments > 0
          ? `${formatNumber(atRisk.shipments, country, 0)} ${
              atRisk.shipments === 1 ? "guía lleva" : "guías llevan"
            } más de 13 días abierta${atRisk.shipments === 1 ? "" : "s"}: ${formatMoney(
              atRisk.value,
              country,
            )} en riesgo de no recaudarse.`
          : "Ninguna guía lleva más de 13 días abierta. Nada en riesgo por antigüedad."}
      </p>
    </Card>
  );
}
