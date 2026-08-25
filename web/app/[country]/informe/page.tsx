"use client";

/**
 * Informe diario consolidado - the operator's hand-made report, generated.
 *
 * Laid out the way the operator's own sheet is: a navy title band with the
 * period, then one block per platform (Effi, Dropi, carga manual) with its
 * three headline figures on the left and the day-by-day matrix on the right -
 * status groups DOWN, days ACROSS, "Total general" at the right edge, and the
 * three closing rows (total, devoluciones, porcentaje) - and a navy
 * consolidated band at the bottom.
 *
 * NO NEW NUMBERS. Everything here comes from `/kpis/daily-status` and
 * `/kpis/platforms`; the page only lays them out the way the sheet did. What
 * it adds over the sheet is honesty: days with no guides appear as zero
 * columns instead of vanishing, and a return rate over fewer than ten closed
 * guides carries a "~" so a two-guide day cannot print 50% as a fact.
 *
 * "VENTAS" IS NOT PRINTED. The sheet calls every guide a sale; a guide is a
 * sale once it is delivered. The figures say "guías" (header of migration 040).
 *
 * TO SHARE IT: "Guardar en PDF" opens the browser's print dialog with a print
 * stylesheet that hides the chrome. No server-side renderer, nothing to keep
 * alive - the browser already knows how to make a PDF.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { AppShell } from "@/components/AppShell";
import { DateBasisFrame } from "@/components/DateBasisNote";
import { Card, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import {
  DailyStatusMatrix,
  daysBetween,
  groupByPlatform,
  type PlatformBlock,
} from "@/components/widgets/daily_status_table";
import { combine } from "@/components/widgets/platform_split";
import { formatRangeLabel, useDateRange, useRangedApi } from "@/lib/date-range";
import { countryFlag, formatDate, formatNumber, formatPercent, type FormatCountry } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Country, DailyStatusRow, PlatformSummaryRow } from "@/lib/types";

const PRINT_STYLES = `
@media print {
  aside, header.app-header, .no-print { display: none !important; }
  main { padding: 0 !important; overflow: visible !important; }
  body { background: #fff !important; color: #111 !important; }
  section.report-block { break-inside: avoid; page-break-inside: avoid; }
  table { font-size: 10.5px !important; }
  .report-band, .report-platform-band, thead tr { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
`;

/**
 * Each platform keeps its own colour across the side card and the table
 * band, so Effi and Dropi read apart at a glance - the sheet does the same
 * (blue Effi, teal Dropi). Anything else is slate: a new platform must never
 * dress as one of these two.
 */
interface PlatformPalette {
  band: string;
  text: string;
  soft: string;
}

const PALETTES: Record<string, PlatformPalette> = {
  effi: { band: "bg-[#1d5fbf]", text: "text-[#1d5fbf]", soft: "bg-[#1d5fbf]/10" },
  dropi: { band: "bg-[#17a398]", text: "text-[#17a398]", soft: "bg-[#17a398]/10" },
};

const DEFAULT_PALETTE: PlatformPalette = {
  band: "bg-[#475569]",
  text: "text-[#475569]",
  soft: "bg-[#475569]/10",
};

function platformPalette(code: string): PlatformPalette {
  return PALETTES[code] ?? DEFAULT_PALETTE;
}

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
  const periodLabel = formatRangeLabel(range, country ?? undefined);

  return (
    <AppShell>
      <style>{PRINT_STYLES}</style>

      <div className="no-print mb-3 flex flex-wrap items-center justify-end gap-2">
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

      <ReportBand
        countryCode={countryCode}
        countryName={country?.name ?? countryCode}
        period={periodLabel}
        detail={[
          field !== "creacion" ? `por fecha de ${field}` : null,
          platform ? `solo ${platform}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      />

      {loading && (
        <div className="mt-4">
          <SkeletonRows rows={8} />
        </div>
      )}

      {!loading && error && (
        <Card className="mt-4">
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
        <Card className="mt-4">
          <EmptyState
            title="Ninguna guía en este período"
            instruction="Amplía el rango de fechas arriba, o elige «Todas» las plataformas."
          />
        </Card>
      )}

      {!loading && !error && country && blocks.length > 0 && (
        <div className="mt-4 space-y-4">
          {blocks.map((block) => (
            <section key={block.code} className="report-block">
              <DateBasisFrame>
                <PlatformReport block={block} country={country} fillDays={fillDays} />
              </DateBasisFrame>
            </section>
          ))}

          {platformRows.length > 0 && (
            <section className="report-block">
              <ConsolidatedBand rows={platformRows} country={country} />
            </section>
          )}

          <footer className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] bg-sunken px-4 py-2 text-[11px] text-ink-dim">
            <span>
              <span aria-hidden>ⓘ</span> Informe consolidado de guías y estados por plataforma
            </span>
            <span>
              Período: {periodLabel} · generado el {formatDate(toIso(printedOn), country)}
            </span>
          </footer>

          <p className="text-[11px] text-ink-faint">
            «Porcentaje devoluciones» divide las devoluciones por todas las guías del día,
            como el informe manual. Un «~» marca días con menos de 10 guías cerradas:
            estimado, no medición. Los días sin guías aparecen en cero en vez de
            desaparecer. Una guía cuenta como devolución cuando va de regreso, ya volvió o
            fue cancelada; como indemnización cuando la transportadora la perdió.
          </p>
        </div>
      )}
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// The navy title band
// ---------------------------------------------------------------------------

function ReportBand({
  countryCode,
  countryName,
  period,
  detail,
}: {
  countryCode: string;
  countryName: string;
  period: string;
  detail: string;
}) {
  return (
    <header className="report-band flex flex-wrap items-center justify-between gap-4 rounded-[14px] bg-[#0f2a5c] px-5 py-4 text-white">
      <div className="flex items-center gap-4">
        <span
          aria-hidden
          className="flex size-12 shrink-0 items-center justify-center rounded-[12px] bg-white/15 text-[24px]"
        >
          {countryFlag(countryCode)}
        </span>
        <div>
          <h1 className="text-[20px] font-extrabold uppercase leading-tight tracking-[0.04em] sm:text-[24px]">
            Informe diario consolidado
          </h1>
          <p className="text-[12px] font-medium text-white/75">{countryName}</p>
        </div>
      </div>
      <div className="flex flex-col items-start gap-1 sm:items-end">
        <span className="inline-flex items-center gap-2 rounded-full bg-white/15 px-4 py-1.5 text-[13px] font-bold uppercase tracking-[0.06em]">
          <span aria-hidden>📅</span>
          {period}
        </span>
        {detail && <span className="text-[11px] text-white/70">{detail}</span>}
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// One platform: side card + matrix
// ---------------------------------------------------------------------------

function PlatformReport({
  block,
  country,
  fillDays,
}: {
  block: PlatformBlock;
  country: FormatCountry;
  fillDays?: readonly string[];
}) {
  const palette = platformPalette(block.code);
  const { totals } = block;

  return (
    <div className="grid gap-3 lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="flex flex-col overflow-hidden rounded-[12px] border border-line-subtle bg-surface">
        <div
          className={cx(
            "report-platform-band flex items-center gap-3 px-4 py-3 text-white",
            palette.band,
          )}
        >
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-[10px] bg-white/20 text-[16px]"
          >
            📦
          </span>
          <h2 className="text-[20px] font-extrabold uppercase tracking-[0.06em]">{block.name}</h2>
        </div>
        <div className="flex flex-col gap-2.5 p-3">
          <span
            className={cx(
              "self-start rounded-full px-3 py-0.5 text-[11px] font-bold uppercase tracking-[0.06em]",
              palette.soft,
              palette.text,
            )}
          >
            {block.name}
          </span>
          <Figure
            icon="📦"
            iconClass={palette.band}
            label="Guías totales"
            value={formatNumber(totals.shipments, country, 0)}
          />
          <Figure
            icon="↺"
            iconClass="bg-warning"
            label="Devoluciones totales"
            value={formatNumber(totals.devolucion, country, 0)}
            valueClass="text-negative"
          />
          <Figure
            icon="%"
            iconClass="bg-positive"
            label="% devolución"
            value={formatPercent(totals.pctDevolucionTotal, 0)}
            valueClass="text-negative"
            hint={`Sobre cerradas: ${formatPercent(totals.pctDevolucionCerradas, 0)}`}
          />
        </div>
      </aside>

      <div className="min-w-0 overflow-hidden rounded-[12px] border border-line-subtle bg-surface">
        <h3
          className={cx(
            "report-platform-band px-4 py-2.5 text-center text-[13px] font-extrabold uppercase tracking-[0.08em] text-white",
            palette.band,
          )}
        >
          Resumen diario por estados – {block.name}
        </h3>
        <DailyStatusMatrix block={block} country={country} fillDays={fillDays} accentClass={palette.band} />
      </div>
    </div>
  );
}

function Figure({
  icon,
  iconClass,
  label,
  value,
  valueClass = "text-ink",
  hint,
}: {
  icon: string;
  iconClass: string;
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-3 rounded-[10px] border border-line-subtle bg-sunken px-3 py-2.5">
      <span
        aria-hidden
        className={cx(
          "flex size-9 shrink-0 items-center justify-center rounded-full text-[15px] font-bold text-white",
          iconClass,
        )}
      >
        {icon}
      </span>
      <div className="min-w-0">
        <dt className="text-[10px] font-bold uppercase tracking-[0.06em] text-ink-muted">{label}</dt>
        <dd className={cx("text-[22px] font-extrabold leading-tight", valueClass)}>{value}</dd>
        {hint && <dd className="text-[10.5px] text-ink-dim">{hint}</dd>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The consolidated band at the bottom
// ---------------------------------------------------------------------------

function ConsolidatedBand({
  rows,
  country,
}: {
  rows: readonly PlatformSummaryRow[];
  country: FormatCountry;
}) {
  const total = useMemo(() => combine(rows), [rows]);
  const leader = total.leader;

  return (
    <div className="report-band rounded-[14px] bg-[#0f2a5c] px-5 py-4 text-white">
      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)] lg:items-center">
        <div className="flex items-center gap-3 lg:border-r lg:border-white/15 lg:pr-4">
          <span
            aria-hidden
            className="flex size-11 shrink-0 items-center justify-center rounded-full bg-white/15 text-[20px]"
          >
            📋
          </span>
          <h2 className="text-[17px] font-extrabold uppercase leading-tight tracking-[0.04em]">
            Resumen consolidado
          </h2>
        </div>

        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <BandFigure icon="📦" iconClass="bg-[#1d5fbf]" label="Guías totales combinadas" value={formatNumber(total.shipments, country, 0)} />
          <BandFigure icon="↺" iconClass="bg-warning" label="Devoluciones totales combinadas" value={formatNumber(total.devolucion, country, 0)} valueClass="text-[#ff8a80]" />
          <BandFigure
            icon="%"
            iconClass="bg-positive"
            label="% devolución combinado"
            value={formatPercent(total.pctDevolucionTotal, 0)}
            valueClass="text-[#ff8a80]"
            hint={`Sobre cerradas: ${formatPercent(total.pctDevolucionCerradas, 0)}`}
          />
          <BandFigure
            icon="📊"
            iconClass="bg-[#7c3aed]"
            label="Plataforma con más guías"
            value={leader ? leader.platform_name.toUpperCase() : "—"}
            valueClass="text-[#5fe0a8]"
            hint={
              leader
                ? `${formatNumber(leader.shipments, country, 0)} guías (${formatPercent(leader.share_pct, 0)})`
                : undefined
            }
          />
        </dl>
      </div>
    </div>
  );
}

function BandFigure({
  icon,
  iconClass,
  label,
  value,
  valueClass = "text-white",
  hint,
}: {
  icon: string;
  iconClass: string;
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center text-center">
      <span
        aria-hidden
        className={cx(
          "flex size-10 items-center justify-center rounded-full text-[16px] font-bold text-white",
          iconClass,
        )}
      >
        {icon}
      </span>
      <dt className="mt-1.5 text-[10px] font-bold uppercase tracking-[0.06em] text-white/75">{label}</dt>
      <dd className={cx("text-[26px] font-extrabold leading-tight", valueClass)}>{value}</dd>
      {hint && <dd className="text-[10.5px] text-white/65">{hint}</dd>}
    </div>
  );
}

function toIso(date: Date): string {
  return (
    `${date.getFullYear()}-` +
    `${String(date.getMonth() + 1).padStart(2, "0")}-` +
    `${String(date.getDate()).padStart(2, "0")}`
  );
}
