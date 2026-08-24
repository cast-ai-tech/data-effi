"use client";

/**
 * Informe diario consolidado - the operator's hand-made report, generated.
 *
 * One page, printable: a block per platform (Effi, Dropi, carga manual) with
 * its three headline figures and its day-by-day table, and the consolidated
 * strip at the bottom. It reads the same KPI functions the dashboard does, so
 * it can never say something the dashboard does not.
 *
 * NO NEW NUMBERS. Everything here comes from `/kpis/daily-status` and
 * `/kpis/platforms`; the page only lays them out the way the sheet did. What
 * it adds over the sheet is honesty: days with no guides appear as zero rows
 * instead of vanishing, and every return rate carries its "sobre cerradas"
 * twin so a two-guide day cannot print 50%.
 *
 * TO SHARE IT: the "Guardar en PDF" button opens the browser's print dialog
 * with a print stylesheet that hides the chrome. No server-side renderer, no
 * queue, nothing to keep alive - the browser already knows how to make a PDF.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { AppShell } from "@/components/AppShell";
import { DateBasisFrame } from "@/components/DateBasisNote";
import { Card, EmptyState, ErrorState, SkeletonRows } from "@/components/ui";
import {
  BlockSummary,
  DailyStatusTable,
  daysBetween,
  groupByPlatform,
} from "@/components/widgets/daily_status_table";
import { ConsolidatedStrip, ShareBar } from "@/components/widgets/platform_split";
import { formatRangeLabel, useDateRange, useRangedApi } from "@/lib/date-range";
import { countryFlag, formatDate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Country, DailyStatusRow, PlatformSummaryRow } from "@/lib/types";

const PRINT_STYLES = `
@media print {
  aside, header.app-header, .no-print { display: none !important; }
  main { padding: 0 !important; overflow: visible !important; }
  body { background: #fff !important; color: #111 !important; }
  section.report-block { break-inside: avoid; page-break-inside: avoid; }
  table { font-size: 10.5px !important; }
}
`;

export default function DailyReportPage() {
  const params = useParams<{ country: string }>();
  const countryCode = (params.country ?? "").toUpperCase();
  const { range, field, platform } = useDateRange();

  const { data: countries } = useApi<Country[]>("/config/countries");
  const country = useMemo(
    () => (countries ?? []).find((item) => item.code === countryCode) ?? null,
    [countries, countryCode],
  );

  const daily = useRangedApi<DailyStatusRow[]>(
    countryCode ? `/kpis/daily-status?country=${countryCode}` : null,
  );
  const platforms = useRangedApi<PlatformSummaryRow[]>(
    countryCode ? `/kpis/platforms?country=${countryCode}` : null,
  );

  const blocks = useMemo(() => groupByPlatform(daily.data ?? []), [daily.data]);
  const platformRows = useMemo(
    () => [...(platforms.data ?? [])].sort((a, b) => b.shipments - a.shipments),
    [platforms.data],
  );

  // Fill the calendar only when both ends are known: "todo el histórico" has
  // no first day to start counting from.
  const fillDays = useMemo(
    () => (range.from && range.to ? daysBetween(range.from, range.to) : undefined),
    [range.from, range.to],
  );

  const loading = daily.loading || platforms.loading || !country;
  const error = daily.error ?? platforms.error;

  const printedOn = useMemo(() => new Date(), []);

  return (
    <AppShell>
      <style>{PRINT_STYLES}</style>

      <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">
            Informe diario consolidado
          </p>
          <h1 className="mt-1 flex items-center gap-2.5 text-[22px] font-bold tracking-tight">
            <span className="text-[24px] leading-none">{countryFlag(countryCode)}</span>
            {country?.name ?? countryCode}
          </h1>
          <p className="mt-1 text-[12px] text-ink-dim">
            Período: {formatRangeLabel(range, country ?? undefined)}
            {field !== "creacion" && ` · por fecha de ${field}`}
            {platform && ` · solo ${platform}`}
            {" · "}generado el {formatDate(toIso(printedOn), country ?? undefined)}
          </p>
        </div>

        <div className="no-print flex items-center gap-2">
          <Link
            href={`/${countryCode.toLowerCase()}?tab=logistica`}
            className="rounded-[8px] border border-line-strong bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-2 no-underline hover:text-ink"
          >
            Volver al tablero
          </Link>
          <button
            type="button"
            onClick={() => window.print()}
            className="rounded-[8px] bg-accent px-3.5 py-1.5 text-[12px] font-semibold text-on-accent hover:bg-accent-hover"
          >
            Guardar en PDF
          </button>
        </div>
      </header>

      {loading && <SkeletonRows rows={8} />}

      {!loading && error && (
        <Card>
          <ErrorState
            message={error.message}
            onRetry={() => {
              daily.reload();
              platforms.reload();
            }}
          />
        </Card>
      )}

      {!loading && !error && blocks.length === 0 && (
        <Card>
          <EmptyState
            title="Ninguna guía en este período"
            instruction="Amplía el rango de fechas arriba, o elige «Todas» las plataformas."
          />
        </Card>
      )}

      {!loading && !error && country && blocks.length > 0 && (
        <div className="space-y-4">
          {blocks.map((block) => (
            <section key={block.code} className="report-block">
              <DateBasisFrame>
                <Card
                  title={block.name}
                  subtitle={`Resumen diario por estados · ${block.rows.length} ${
                    block.rows.length === 1 ? "día con guías" : "días con guías"
                  }`}
                  bodyClassName="p-0"
                >
                  <div className="border-b border-line-subtle px-4 py-2.5">
                    <BlockSummary block={block} country={country} />
                  </div>
                  <DailyStatusTable block={block} country={country} fillDays={fillDays} compact />
                </Card>
              </DateBasisFrame>
            </section>
          ))}

          {platformRows.length > 0 && (
            <section className="report-block">
              <Card title="Resumen consolidado" subtitle="Todas las plataformas sumadas">
                <ShareBar rows={platformRows} />
                <div className="mt-3">
                  <ConsolidatedStrip rows={platformRows} country={country} />
                </div>
              </Card>
            </section>
          )}

          <p className="text-[11px] text-ink-faint">
            «% devol.» divide las devoluciones por todas las guías del día, como el informe
            manual. «% devol. cerradas» divide solo por las guías ya resueltas y es la cifra
            que se cumple cuando el día termina de madurar. Un «~» marca días con menos de 10
            guías cerradas: estimado, no medición. Los días sin guías aparecen en cero en vez
            de desaparecer.
          </p>
        </div>
      )}
    </AppShell>
  );
}

function toIso(date: Date): string {
  return (
    `${date.getFullYear()}-` +
    `${String(date.getMonth() + 1).padStart(2, "0")}-` +
    `${String(date.getDate()).padStart(2, "0")}`
  );
}
