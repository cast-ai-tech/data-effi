"use client";

/**
 * How much of your own money is out there right now.
 *
 * Every open guide has already cost freight, product and platform fee, and has
 * collected nothing. That sum is not a loss and it is not profit: it is working
 * capital you cannot spend until the parcels arrive. An operator deciding
 * whether to buy the next batch of stock is asking exactly this number, and
 * before this widget the only place it existed was buried inside a net figure
 * that read as a loss.
 */

import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber, formatPercent, pluralize } from "@/lib/format";
import { maturityNotice, pickSplit } from "@/lib/orders";
import type { ContributionSplit } from "@/lib/types";

const TITLE = "Capital en la calle";
const SUBTITLE =
  "Flete, producto y comisión ya pagados de guías que todavía no cierran.";

export default function CapitalInStreet({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<ContributionSplit>(
    `/kpis/contribution-split?country=${countryCode}`,
  );

  const row = pickSplit(data, countryCode);

  if (loading) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <SkeletonRows rows={3} />
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

  if (!row || row.shipments === 0) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no hay guías en la calle"
          instruction="Sube un reporte de guías desde Cargar datos. Con la primera guía abierta ya se puede calcular cuánto capital tuyo está viajando."
        />
      </Card>
    );
  }

  const notice = maturityNotice(row.maturity_pct);

  return (
    <Card title={TITLE} subtitle={SUBTITLE}>
      <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        Tu plata viajando ahora mismo
      </p>
      <p className="mt-1.5 text-[30px] font-bold leading-none text-warning">
        {formatMoney(row.capital_in_street, country)}
      </p>
      <p className="mt-1.5 text-[11.5px] text-ink-muted">
        en {pluralize(row.open_shipments, "guía abierta", "guías abiertas")} · si todas
        llegan, deberían recaudar {formatMoney(row.committed_revenue, country)}
      </p>

      <div className="mt-4">
        <div className="mb-1 flex items-baseline justify-between gap-3">
          <span className="text-[11px] text-ink-dim">Guías que ya terminaron</span>
          <span className="text-[11.5px] font-semibold text-ink-2">
            {formatPercent(row.maturity_pct)}
          </span>
        </div>
        <MicroBar value={row.maturity_pct ?? 0} max={100} tone="accent" />
        <p className="mt-1 text-[10.5px] text-ink-dim">
          {formatNumber(row.closed_shipments, country, 0)} cerradas ·{" "}
          {formatNumber(row.open_shipments, country, 0)} en la calle
        </p>
      </div>

      {notice && (
        <p className="mt-4 rounded-[8px] border border-line-input bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-ink-muted">
          {notice}
        </p>
      )}

      <p className="mt-3 border-t border-line-subtle pt-3 text-[11px] leading-relaxed text-ink-dim">
        Este dinero no está perdido ni ganado: está comprometido. Vuelve a tu cuenta a
        medida que las guías se entregan y la transportadora te liquida, y no vuelve
        cuando se devuelven. Por eso es el número que manda a la hora de decidir si
        alcanza para comprar el siguiente lote.
      </p>
    </Card>
  );
}
