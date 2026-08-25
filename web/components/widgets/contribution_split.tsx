"use client";

/**
 * Contribution, split into what closed and what is still travelling.
 *
 * This widget exists because a single net number lied. On a real operation it
 * read -3.376 while the guides that had actually finished had produced +8.642:
 * the difference was a large young cohort that had already paid freight,
 * product and platform fee and had collected nothing yet, because it had not
 * arrived. That is a statement about timing, not about profitability, and
 * summing the two cohorts turns it into a phantom loss.
 *
 * So the headline is the REALISED figure, and the money still out there is
 * shown beside it as what it is - working capital, not a hole. `net_contribution`
 * is deliberately not a headline anywhere: it appears once, small, at the
 * bottom, labelled as the number that caused the confusion.
 */

import { Card, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber, formatPercent, pluralize } from "@/lib/format";
import { maturityNotice, pickSplit } from "@/lib/orders";
import type { ContributionSplit } from "@/lib/types";

const TITLE = "Contribución: cerrada vs. en calle";
const SUBTITLE =
  "Lo que ya se cobró, separado de lo que todavía está viajando.";

export default function ContributionSplitWidget({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<ContributionSplit>(
    `/kpis/contribution-split?country=${countryCode}`,
  );

  const row = pickSplit(data, countryCode);

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

  if (!row || row.shipments === 0) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no hay guías para separar"
          instruction="Sube un reporte de guías desde Master Data. Apenas exista la primera, este widget empieza a mostrar cuánto ya se cobró y cuánto sigue en la calle."
        />
      </Card>
    );
  }

  const realised = row.realised_contribution ?? 0;
  const notice = maturityNotice(row.maturity_pct);

  return (
    <Card title={TITLE} subtitle={SUBTITLE}>
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Contribución de lo que ya cerró
          </p>
          <p
            className={cx(
              "mt-1.5 text-[30px] font-bold leading-none",
              realised >= 0 ? "text-positive" : "text-negative",
            )}
          >
            {formatMoney(row.realised_contribution, country)}
          </p>
          <p className="mt-1.5 text-[11.5px] text-ink-muted">
            {formatPercent(row.realised_margin_pct)} de margen sobre{" "}
            {formatMoney(row.realised_revenue, country)} recaudados ·{" "}
            {pluralize(row.closed_shipments, "guía cerrada", "guías cerradas")}
          </p>
        </div>

        <div className="self-end">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Capital todavía en la calle
          </p>
          <p className="mt-1 text-[18px] font-semibold leading-none text-warning">
            {formatMoney(row.capital_in_street, country)}
          </p>
          <p className="mt-1.5 text-[11.5px] text-ink-muted">
            en {pluralize(row.open_shipments, "guía abierta", "guías abiertas")} · espera
            recaudar {formatMoney(row.committed_revenue, country)}
          </p>
        </div>

        <div className="self-end">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Guías que ya terminaron
          </p>
          <p className="mt-1 text-[18px] font-semibold leading-none text-ink">
            {formatPercent(row.maturity_pct)}
          </p>
          <p className="mt-1.5 text-[11.5px] text-ink-muted">
            {formatNumber(row.closed_shipments, country, 0)} de{" "}
            {formatNumber(row.shipments, country, 0)}
          </p>
        </div>
      </div>

      {notice && (
        <p className="mt-4 max-w-2xl rounded-[8px] border border-line-input bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-ink-muted">
          {notice}
        </p>
      )}

      <p className="mt-3 max-w-2xl border-t border-line-subtle pt-3 text-[11px] leading-relaxed text-ink-dim">
        Si sumas las dos mitades te da {formatMoney(row.net_contribution, country)}. Ese
        es el número que confundía: mezcla guías que ya cerraron con guías que apenas
        pagaron flete, producto y comisión y todavía no han cobrado nada. Mientras haya
        despachos jóvenes, ese total se ve peor de lo que la operación realmente está.
      </p>
    </Card>
  );
}
