"use client";

/**
 * The table from the operator's hand-made daily report, one block per platform.
 *
 * Day by day: how many guides, how many delivered, returned, still moving,
 * stopped with an issue - and the return percentage. Effi and Dropi get their
 * own block because they do not behave alike and mixing them hides which one
 * is bleeding. A TOTAL GENERAL row closes each block, as on the sheet.
 *
 * TWO RETURN PERCENTAGES, AND A TILDE. The sheet divides returns by every guide
 * of the day, which understates the rate on recent days: a guide still in
 * transit cannot have been returned yet. That number is shown because it is
 * the one the reader knows, and next to it the honest one - returns over
 * CLOSED guides - which is what the day will look like once it settles. A day
 * with fewer than ten closed guides carries a `~`: an estimate, not a measure.
 */

import { Fragment, useMemo } from "react";

import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import { formatDate, formatNumber, formatPercent, type FormatCountry } from "@/lib/format";
import {
  STATUS_GROUPS,
  STATUS_GROUP_HINTS,
  STATUS_GROUP_LABELS,
  STATUS_GROUP_TEXT,
  type StatusGroup,
} from "@/lib/status";
import type { DailyStatusRow } from "@/lib/types";

/** The columns, in the order the operator reads them (migration 045). */
const COLUMNS: StatusGroup[] = [...STATUS_GROUPS];

const SHORT_SAMPLE_HINT =
  "Menos de 10 guías cerradas ese día: el porcentaje es un estimado, no una medición.";

export interface PlatformBlock {
  code: string;
  name: string;
  rows: DailyStatusRow[];
  totals: BlockTotals;
}

export interface BlockTotals {
  shipments: number;
  entregada: number;
  devolucion: number;
  en_transito: number;
  novedad: number;
  indemnizacion: number;
  cerradas: number;
  /** Returns over every guide - what the sheet prints. */
  pctDevolucionTotal: number | null;
  /** Returns over closed guides - what will still be true tomorrow. */
  pctDevolucionCerradas: number | null;
  pctEntregaCerradas: number | null;
}

export function sumBlock(rows: readonly DailyStatusRow[]): BlockTotals {
  const totals = {
    shipments: 0,
    entregada: 0,
    devolucion: 0,
    en_transito: 0,
    novedad: 0,
    indemnizacion: 0,
    cerradas: 0,
  };
  for (const row of rows) {
    totals.shipments += row.shipments;
    totals.entregada += row.entregada;
    totals.devolucion += row.devolucion;
    totals.en_transito += row.en_transito;
    totals.novedad += row.novedad;
    totals.indemnizacion += row.indemnizacion;
    totals.cerradas += row.cerradas;
  }
  return {
    ...totals,
    pctDevolucionTotal:
      totals.shipments > 0 ? (totals.devolucion / totals.shipments) * 100 : null,
    pctDevolucionCerradas:
      totals.cerradas > 0 ? (totals.devolucion / totals.cerradas) * 100 : null,
    pctEntregaCerradas: totals.cerradas > 0 ? (totals.entregada / totals.cerradas) * 100 : null,
  };
}

/**
 * One block per platform, biggest first, rows in day order.
 *
 * Grouped here rather than in SQL so the same rows can also be read as one
 * flat series by the report page.
 */
export function groupByPlatform(rows: readonly DailyStatusRow[]): PlatformBlock[] {
  const blocks = new Map<string, PlatformBlock>();
  for (const row of rows) {
    let block = blocks.get(row.platform_code);
    if (!block) {
      block = {
        code: row.platform_code,
        name: row.platform_name,
        rows: [],
        totals: sumBlock([]),
      };
      blocks.set(row.platform_code, block);
    }
    block.rows.push(row);
  }
  for (const block of blocks.values()) {
    block.rows.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
    block.totals = sumBlock(block.rows);
  }
  return [...blocks.values()].sort((a, b) => b.totals.shipments - a.totals.shipments);
}

/** Every day between `from` and `to`, inclusive, as ISO strings. */
export function daysBetween(from: string, to: string): string[] {
  const match = (value: string) => /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const a = match(from);
  const b = match(to);
  if (!a || !b || from > to) return [];
  const days: string[] = [];
  const cursor = new Date(Number(a[1]), Number(a[2]) - 1, Number(a[3]));
  const end = new Date(Number(b[1]), Number(b[2]) - 1, Number(b[3]));
  // Guard: a range of years is not a daily table anyone reads.
  for (let i = 0; cursor <= end && i < 400; i += 1) {
    days.push(
      `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(
        cursor.getDate(),
      ).padStart(2, "0")}`,
    );
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function pctTone(value: number | null): "positive" | "warning" | "negative" | "neutral" {
  if (value === null || !Number.isFinite(value)) return "neutral";
  if (value <= 20) return "positive";
  if (value <= 35) return "warning";
  return "negative";
}

function ShortSampleMark() {
  return (
    <abbr
      title={SHORT_SAMPLE_HINT}
      className="ml-1 cursor-help align-middle text-xs font-semibold text-ink-dim no-underline"
    >
      ~
    </abbr>
  );
}

/**
 * The table itself, for one platform. Pure: rows in, markup out. The widget
 * below and the printable report both render it.
 *
 * `fillDays` lists the days that must appear even with no guides, so a gap in
 * the sheet reads as "0" and not as a day that silently vanished - the
 * operator's own report skipped the 2nd and the 9th without saying so.
 */
export function DailyStatusTable({
  block,
  country,
  fillDays,
  compact = false,
}: {
  block: PlatformBlock;
  country: FormatCountry;
  fillDays?: readonly string[];
  compact?: boolean;
}) {
  const rows = useMemo(() => {
    if (!fillDays || fillDays.length === 0) return block.rows.map((row) => ({ row, empty: false }));
    const byDay = new Map(block.rows.map((row) => [row.day, row]));
    return fillDays.map((day) => {
      const row = byDay.get(day);
      return row
        ? { row, empty: false }
        : { row: emptyRow(day, block), empty: true };
    });
  }, [block, fillDays]);

  const { totals } = block;
  const cell = cx("px-2.5 text-right tabular-nums", compact ? "py-1" : "py-1.5");

  return (
    <div className="data-table">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <thead>
          <tr className="text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim">
            <th scope="col" className={cx("px-2.5 text-left", compact ? "py-1" : "py-1.5")}>
              Fecha
            </th>
            {COLUMNS.map((group) => (
              <th
                key={group}
                scope="col"
                title={STATUS_GROUP_HINTS[group]}
                className={cx(cell, "cursor-help", STATUS_GROUP_TEXT[group])}
              >
                {STATUS_GROUP_LABELS[group]}
              </th>
            ))}
            <th scope="col" className={cx(cell, "bg-sunken text-ink-2")}>
              Total guías
            </th>
            <th
              scope="col"
              title="Devoluciones divididas por todas las guías del día, como en el informe manual."
              className={cx(cell, "cursor-help")}
            >
              % devol.
            </th>
            <th
              scope="col"
              title="Devoluciones divididas solo por las guías ya cerradas (entregadas o devueltas). Es la cifra que se cumple cuando el día termina de madurar."
              className={cx(cell, "cursor-help")}
            >
              % devol. cerradas
            </th>
          </tr>
        </thead>

        <tbody>
          {rows.map(({ row, empty }) => (
            <tr
              key={row.day}
              className={cx("border-t border-line-row", empty && "text-ink-faint")}
              data-empty-day={empty || undefined}
            >
              <td className={cx("px-2.5 text-left font-medium", compact ? "py-1" : "py-1.5", empty ? "text-ink-faint" : "text-ink-body")}>
                {formatDate(row.day, country)}
                {empty && (
                  <span className="ml-1.5 text-xs font-normal uppercase tracking-wide">
                    sin guías
                  </span>
                )}
              </td>
              {COLUMNS.map((group) => (
                <td
                  key={group}
                  className={cx(cell, !empty && row[group] > 0 && STATUS_GROUP_TEXT[group])}
                >
                  {formatNumber(row[group], country, 0)}
                </td>
              ))}
              <td className={cx(cell, "bg-sunken font-semibold text-ink")}>
                {formatNumber(row.shipments, country, 0)}
              </td>
              <td className={cell}>
                {empty ? "—" : formatPercent(row.pct_devolucion_total)}
              </td>
              <td className={cell}>
                {empty ? (
                  "—"
                ) : (
                  <Fragment>
                    {formatPercent(row.pct_devolucion_cerradas)}
                    {row.sample_quality === "muestra_corta" && <ShortSampleMark />}
                  </Fragment>
                )}
              </td>
            </tr>
          ))}
        </tbody>

        <tfoot>
          <tr className="border-t border-line-strong bg-sunken text-sm font-semibold text-ink">
            <td className={cx("px-2.5 text-left", compact ? "py-1" : "py-1.5")}>TOTAL GENERAL</td>
            {COLUMNS.map((group) => (
              <td key={group} className={cx(cell, STATUS_GROUP_TEXT[group])}>
                {formatNumber(totals[group], country, 0)}
              </td>
            ))}
            <td className={cell}>{formatNumber(totals.shipments, country, 0)}</td>
            <td className={cell}>{formatPercent(totals.pctDevolucionTotal)}</td>
            <td className={cell}>
              {formatPercent(totals.pctDevolucionCerradas)}
              {totals.cerradas < 10 && <ShortSampleMark />}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function emptyRow(day: string, block: PlatformBlock): DailyStatusRow {
  const sample = block.rows[0];
  return {
    country_code: sample?.country_code ?? "",
    platform_code: block.code,
    platform_name: block.name,
    day,
    shipments: 0,
    entregada: 0,
    devolucion: 0,
    en_transito: 0,
    novedad: 0,
    indemnizacion: 0,
    cerradas: 0,
    pct_entrega_cerradas: null,
    pct_devolucion_cerradas: null,
    pct_devolucion_total: null,
    sample_quality: "muestra_corta",
    declared_value: null,
    revenue: null,
    contribution: null,
    currency_code: sample?.currency_code ?? null,
  };
}

/** The three figures the sheet puts beside each platform's logo. */
export function BlockSummary({
  block,
  country,
}: {
  block: PlatformBlock;
  country: FormatCountry;
}) {
  const { totals } = block;
  return (
    <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
      <div className="flex items-baseline gap-1.5">
        <dt className="text-ink-dim">Guías</dt>
        <dd className="font-semibold text-ink">{formatNumber(totals.shipments, country, 0)}</dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-ink-dim">Devoluciones</dt>
        <dd className="font-semibold text-negative-ink">
          {formatNumber(totals.devolucion, country, 0)}
        </dd>
      </div>
      <div className="flex items-center gap-1.5">
        <dt className="text-ink-dim">% devolución</dt>
        <dd className="w-[96px] sm:w-[120px]">
          <MicroBar
            value={totals.pctDevolucionTotal}
            max={100}
            tone={pctTone(totals.pctDevolucionTotal)}
            label={formatPercent(totals.pctDevolucionTotal)}
          />
        </dd>
      </div>
      <div className="flex items-baseline gap-1.5">
        <dt className="text-ink-dim">sobre cerradas</dt>
        <dd className="font-semibold text-ink-2">
          {formatPercent(totals.pctDevolucionCerradas)}
          {totals.cerradas < 10 && <ShortSampleMark />}
        </dd>
      </div>
    </dl>
  );
}

// ---------------------------------------------------------------------------
// The matrix the operator's sheet prints: states down, days across
// ---------------------------------------------------------------------------

const MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** "2026-08-01" -> "1-ago", the way the sheet heads its columns. */
export function shortDayLabel(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  return `${Number(match[3])}-${MONTHS_SHORT[Number(match[2]) - 1] ?? match[2]}`;
}

export interface MatrixColumn {
  day: string;
  row: DailyStatusRow | null;
}

/**
 * The columns of the matrix: every day in `fillDays` when given (so a day
 * without guides prints as zeros, as a column that is simply missing reads
 * like a day that never happened), else the block's own days.
 */
export function matrixColumns(block: PlatformBlock, fillDays?: readonly string[]): MatrixColumn[] {
  const byDay = new Map(block.rows.map((row) => [row.day, row]));
  const days = fillDays && fillDays.length > 0 ? fillDays : block.rows.map((row) => row.day);
  return days.map((day) => ({ day, row: byDay.get(day) ?? null }));
}

/**
 * The daily table as the operator's hand-made sheet lays it out: one row per
 * status group, one column per day, a "Total general" column at the right,
 * and three closing rows - total guides, returns, return percentage. Pure:
 * rows in, markup out; the printable report renders it.
 */
export function DailyStatusMatrix({
  block,
  country,
  fillDays,
  accentClass = "bg-accent",
}: {
  block: PlatformBlock;
  country: FormatCountry;
  fillDays?: readonly string[];
  /** Header band colour: the platform's own, so Effi and Dropi read apart. */
  accentClass?: string;
}) {
  const columns = useMemo(() => matrixColumns(block, fillDays), [block, fillDays]);
  const { totals } = block;
  const cell = "px-2 py-1.5 text-right tabular-nums text-sm";
  const count = (value: number, group?: StatusGroup) =>
    value > 0 ? (
      <span className={group ? STATUS_GROUP_TEXT[group] : undefined}>{formatNumber(value, country, 0)}</span>
    ) : (
      <span className="text-ink-faint">{group ? "" : "0"}</span>
    );

  return (
    <div className="data-table">
      <table className="w-full min-w-[640px] border-collapse">
        <thead>
          <tr className={cx("text-xs font-bold uppercase tracking-[0.05em] text-on-solid", accentClass)}>
            <th scope="col" className="px-2.5 py-2 text-left">
              Estado
            </th>
            {columns.map((column) => (
              <th key={column.day} scope="col" className="px-2 py-2 text-right" title={formatDate(column.day, country)}>
                {shortDayLabel(column.day)}
              </th>
            ))}
            <th scope="col" className="px-2.5 py-2 text-right">
              Total general
            </th>
          </tr>
        </thead>
        <tbody>
          {STATUS_GROUPS.map((group) => (
            <tr key={group} className="border-t border-line-row">
              <th
                scope="row"
                title={STATUS_GROUP_HINTS[group]}
                className={cx("cursor-help px-2.5 py-1.5 text-left text-sm font-medium", STATUS_GROUP_TEXT[group])}
              >
                {STATUS_GROUP_LABELS[group]}
              </th>
              {columns.map((column) => (
                <td key={column.day} className={cell}>
                  {count(column.row?.[group] ?? 0, group)}
                </td>
              ))}
              <td className={cx(cell, "font-semibold")}>{count(totals[group], group)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-line-strong bg-sunken text-sm font-bold text-ink">
            <th scope="row" className="px-2.5 py-1.5 text-left">
              Total general
            </th>
            {columns.map((column) => (
              <td key={column.day} className={cell}>
                {formatNumber(column.row?.shipments ?? 0, country, 0)}
              </td>
            ))}
            <td className={cell}>{formatNumber(totals.shipments, country, 0)}</td>
          </tr>
          <tr className="border-t border-line-row text-sm text-negative-ink">
            <th scope="row" className="px-2.5 py-1.5 text-left font-medium">
              Devoluciones
            </th>
            {columns.map((column) => (
              <td key={column.day} className={cell}>
                {formatNumber(column.row?.devolucion ?? 0, country, 0)}
              </td>
            ))}
            <td className={cx(cell, "font-semibold")}>{formatNumber(totals.devolucion, country, 0)}</td>
          </tr>
          <tr className="border-t border-line-row bg-negative/10 text-sm font-bold text-negative-ink">
            <th
              scope="row"
              className="px-2.5 py-1.5 text-left"
              title="Devoluciones divididas por todas las guías del día, como en el informe manual. Un ~ marca días con menos de 10 guías cerradas."
            >
              Porcentaje devoluciones
            </th>
            {columns.map((column) => (
              <td key={column.day} className={cell}>
                {column.row ? (
                  <Fragment>
                    {formatPercent(column.row.pct_devolucion_total, 0)}
                    {column.row.sample_quality === "muestra_corta" && <ShortSampleMark />}
                  </Fragment>
                ) : (
                  "—"
                )}
              </td>
            ))}
            <td className={cell}>
              {formatPercent(totals.pctDevolucionTotal, 0)}
              {totals.cerradas < 10 && <ShortSampleMark />}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

const TITLE = "Resumen diario por estados";
const SUBTITLE = "Cada día con sus guías por estado, un bloque por plataforma";

export default function DailyStatusTableWidget({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<DailyStatusRow[]>(
    `/kpis/daily-status?country=${countryCode}`,
  );

  const blocks = useMemo(() => groupByPlatform(data ?? []), [data]);

  if (loading) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <SkeletonRows rows={8} />
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

  if (blocks.length === 0) {
    return (
      <Card title={TITLE} subtitle={SUBTITLE}>
        <EmptyState
          title="Ningún día con guías"
          instruction="No hay guías en el rango y la plataforma seleccionados. Amplía las fechas o elige «Todas» arriba."
        />
      </Card>
    );
  }

  return (
    <Card title={TITLE} subtitle={SUBTITLE} bodyClassName="p-0">
      {blocks.map((block) => (
        <section key={block.code} className="border-b border-line-subtle last:border-b-0">
          <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
            <h4 className="text-base font-semibold text-ink">{block.name}</h4>
            <BlockSummary block={block} country={country} />
          </header>
          <DailyStatusTable block={block} country={country} />
        </section>
      ))}
    </Card>
  );
}
