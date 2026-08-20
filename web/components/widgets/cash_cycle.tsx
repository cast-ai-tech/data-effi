"use client";

/**
 * How long until the money from a dispatch is actually yours.
 *
 * In cash on delivery there are two moments that everyone treats as one:
 * the customer pays the courier, and the courier pays you. Between them the
 * cash exists, belongs to you on paper, and cannot be spent - which is why an
 * operation can be profitable and still run out of money to buy stock.
 */

import { useMemo } from "react";

import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { formatMoney, formatNumber } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { CashCycleRow } from "@/lib/types";

export default function CashCycle({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useApi<CashCycleRow[]>(
    `/kpis/cash-cycle?country=${countryCode}`,
  );

  /**
   * The view is grouped by country, so a filtered request returns one row.
   * Picking the matching country rather than blindly taking the first keeps it
   * correct if the filter is ever dropped - percentiles cannot be averaged
   * across countries, so combining rows is not an option.
   */
  const row = useMemo<CashCycleRow | null>(() => {
    const rows = data ?? [];
    if (rows.length === 0) return null;
    return rows.find((item) => item.country_code === countryCode) ?? rows[0];
  }, [data, countryCode]);

  const SUBTITLE = "Días desde que despachas hasta que la plata se puede gastar.";

  if (loading) {
    return (
      <Card title="Ciclo de caja" subtitle={SUBTITLE}>
        <SkeletonRows rows={3} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Ciclo de caja" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!row) {
    return (
      <Card title="Ciclo de caja" subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no se puede medir el ciclo de caja"
          instruction="Sube un reporte de guías con la fecha de liquidación desde Cargar datos. Mientras no exista al menos una guía ya liquidada, no hay forma de saber cuántos días tarda la transportadora en pagarte."
        />
      </Card>
    );
  }

  const settledYet = row.settled > 0;

  return (
    <Card title="Ciclo de caja" subtitle={SUBTITLE}>
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            La mitad de tus guías se paga en
          </p>
          <p className="mt-1.5 text-[30px] font-bold leading-none text-ink">
            {formatNumber(row.p50_days_to_cash, country, 1)}
            <span className="ml-1.5 text-[15px] font-semibold text-ink-muted">días</span>
          </p>
        </div>

        <div className="self-end">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Las más lentas (9 de cada 10)
          </p>
          <p className="mt-1 text-[16px] font-semibold leading-none text-ink-muted">
            {formatNumber(row.p90_days_to_cash, country, 1)} días
          </p>
        </div>

        <div className="self-end">
          <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Entregadas y todavía sin pagar
          </p>
          <p className="mt-1 text-[16px] font-semibold leading-none text-warning">
            {formatNumber(row.delivered_unsettled, country, 0)} guías ·{" "}
            {formatMoney(row.cash_in_transit, country)}
          </p>
        </div>
      </div>

      <p className="mt-4 max-w-2xl border-t border-line-subtle pt-3 text-[11.5px] leading-relaxed text-ink-dim">
        Entregada no es lo mismo que recaudada: el cliente ya pagó, pero la plata sigue en
        manos de la transportadora hasta que te la liquida. Ese dinero cuenta en tu
        contribución y no sirve para comprar el siguiente lote.
        {settledYet
          ? ` Sobre ${formatNumber(row.settled, country, 0)} guías ya liquidadas, el promedio fue de ${formatNumber(row.avg_days_to_cash, country, 1)} días.`
          : " Todavía no hay ninguna guía liquidada, así que aún no se puede medir cuántos días tarda el pago en llegarte."}
      </p>
    </Card>
  );
}
