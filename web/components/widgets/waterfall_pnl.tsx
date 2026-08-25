"use client";

/**
 * Where the money went, in the order it left.
 *
 * A waterfall is the only chart shape that survives the question "why is
 * contribution so low" - it names the step that ate the margin instead of
 * leaving the operator to subtract six numbers in their head.
 *
 * The bars are drawn with the floating-bar technique: a transparent base bar
 * stacked under a visible value bar, so each deduction starts where the previous
 * one ended.
 */

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney } from "@/lib/format";
import type { DailyContribution } from "@/lib/types";

/** Rounding noise below this is not worth a footnote. */
const RECONCILIATION_EPSILON = 1;

interface Step {
  name: string;
  /** Invisible pedestal that lifts the visible bar to where the chain left off. */
  base: number;
  value: number;
  colour: string;
  label: string;
}

function sumBy(
  rows: DailyContribution[],
  pick: (row: DailyContribution) => number | null,
): number {
  return rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
}

export default function WaterfallPnl({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<DailyContribution[]>(
    `/kpis/daily-contribution?country=${countryCode}`,
  );

  const model = useMemo(() => {
    if (!data || data.length === 0) return null;

    const rows = data;
    // Pauta only earns a step when at least one day actually reported spend.
    const adSpendMissing = rows.every((row) => row.ad_spend_missing);

    const revenue = sumBy(rows, (row) => row.revenue);
    const cogs = sumBy(rows, (row) => row.cogs);
    const freight = sumBy(rows, (row) => row.freight);
    const fees = sumBy(rows, (row) => row.fees);
    const adSpend = adSpendMissing ? 0 : sumBy(rows, (row) => row.ad_spend);
    const contribution = sumBy(rows, (row) => row.contribution);

    const deductions: Array<{ name: string; amount: number; colour: string }> = [
      { name: "Producto", amount: cogs, colour: "#ff6259" },
      { name: "Flete", amount: freight, colour: "#ff6259" },
      { name: "Comisiones", amount: fees, colour: "#ff6259" },
    ];
    if (!adSpendMissing) {
      deductions.push({ name: "Pauta", amount: adSpend, colour: "#f5a83c" });
    }

    const steps: Step[] = [
      {
        name: "Recaudo",
        base: 0,
        value: revenue,
        colour: "#3a4152",
        label: formatMoney(revenue, country, { compact: true }),
      },
    ];

    let running = revenue;
    for (const deduction of deductions) {
      running -= deduction.amount;
      steps.push({
        name: deduction.name,
        base: running,
        value: deduction.amount,
        colour: deduction.colour,
        label: `-${formatMoney(Math.abs(deduction.amount), country, { compact: true })}`,
      });
    }

    steps.push({
      name: "Contribución",
      base: 0,
      value: contribution,
      colour: contribution >= 0 ? "#21c08a" : "#ff6259",
      label: formatMoney(contribution, country, { compact: true }),
    });

    // The chain and the reported contribution differ by whatever `adjustments`
    // carries. Saying so beats letting the last bar look like an arithmetic bug.
    const residual = contribution - running;

    return { steps, adSpendMissing, residual, contribution };
  }, [data, country]);

  if (loading) {
    return (
      <Card title="Del recaudo a la contribución">
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Del recaudo a la contribución">
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (!model) {
    return (
      <Card title="Del recaudo a la contribución">
        <EmptyState
          title="No hay movimientos de dinero en este rango"
          instruction="Conecta la plataforma de recaudo o sube el archivo de movimientos desde Master Data para descomponer el recaudo en producto, flete, comisiones y pauta."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Del recaudo a la contribución"
      subtitle="Cada barra arranca donde terminó la anterior"
    >
      <ResponsiveContainer width="100%" height={280}>
        <BarChart
          data={model.steps}
          margin={{ top: 24, right: 8, bottom: 4, left: 8 }}
        >
          <CartesianGrid
            stroke="var(--color-line)"
            strokeDasharray="3 3"
            vertical={false}
          />
          <XAxis
            dataKey="name"
            axisLine={false}
            tickLine={false}
            tick={{ fill: "var(--color-ink-muted)", fontSize: 11 }}
          />
          <YAxis hide />
          {/* The pedestal. Transparent, but it is what makes this a waterfall. */}
          <Bar dataKey="base" stackId="pnl" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="value" stackId="pnl" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {model.steps.map((step) => (
              <Cell key={step.name} fill={step.colour} />
            ))}
            <LabelList
              dataKey="label"
              position="top"
              fill="var(--color-ink-muted)"
              fontSize={11}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {model.adSpendMissing && (
        <p className="mt-3 text-[11.5px] leading-snug text-warning">
          Sin datos de pauta en el rango: se omitió ese paso y la contribución no
          descuenta medios.
        </p>
      )}

      {Math.abs(model.residual) > RECONCILIATION_EPSILON && (
        <p className="mt-2 text-[11.5px] leading-snug text-ink-dim">
          La diferencia de {formatMoney(model.residual, country)} entre la cadena y la
          contribución corresponde a ajustes registrados aparte.
        </p>
      )}
    </Card>
  );
}
