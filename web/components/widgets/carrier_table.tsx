"use client";

/**
 * Carrier league table.
 *
 * The one screen that answers "which carrier is quietly eating my margin".
 * Delivery rate and return rate carry a micro-bar because a column of numbers
 * hides the spread; a column of bars does not. Contribution is coloured because
 * a negative one means that carrier costs more than it brings in.
 */

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingFn,
  type SortingState,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";

import type { WidgetProps } from "@/components/widgets/types";
import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows, cx } from "@/components/ui";
import { bestCarrierByZone } from "@/lib/carrier-zones";
import { useRangedApi } from "@/lib/date-range";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { CarrierRow, CarrierZoneRow } from "@/lib/types";

/** How many "best here" zones the footnote names before pointing at the widget. */
const ZONE_NOTES = 3;

/** Columns whose figures must line up on the right edge. */
const RIGHT_ALIGNED = new Set([
  "shipments",
  "avg_days_to_deliver",
  "p90_days_to_deliver",
  "avg_freight_per_shipment",
  "contribution",
]);

/** A carrier above 75% delivery is healthy; below 60% is bleeding money. */
function deliveryTone(pct: number | null): "positive" | "warning" | "negative" {
  if (pct === null || !Number.isFinite(pct)) return "negative";
  if (pct >= 75) return "positive";
  if (pct >= 60) return "warning";
  return "negative";
}

/**
 * Fewer than ten terminal guides behind the percentages (migration 021).
 *
 * The same rule the city traffic light has had since migration 003, arriving
 * here for the same reason: inside a narrow window a carrier can have two
 * terminal guides and print "50,0% de entrega" next to carriers measured over
 * hundreds. The reader compares them and moves volume away from a carrier that
 * was never measured.
 *
 * The rate is still shown - a blank cell explains nothing - but it is marked as
 * an estimate rather than presented as measured.
 */
function isShortSample(row: CarrierRow): boolean {
  return row.sample_quality === "muestra_corta";
}

const SHORT_SAMPLE_HINT =
  "Menos de 10 guías terminales en este rango: el porcentaje es un estimado, " +
  "no una medición. Amplíe el rango para compararlo con las demás.";

/** Deliberately quiet: a badge per row, not a banner. */
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

/** Nulls sort to the bottom instead of pretending to be zero. */
const numericSort: SortingFn<CarrierRow> = (rowA, rowB, columnId) => {
  const a = rowA.getValue<number | null>(columnId);
  const b = rowB.getValue<number | null>(columnId);
  const aMissing = a === null || a === undefined || !Number.isFinite(a);
  const bMissing = b === null || b === undefined || !Number.isFinite(b);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  return a === b ? 0 : a < b ? -1 : 1;
};

interface CarrierTotals {
  shipments: number;
  deliveryRate: number | null;
  returnRate: number | null;
  avgDays: number | null;
  avgFreight: number | null;
  contribution: number;
}

/**
 * Totals are WEIGHTED, never a mean of means: a carrier with 12 guides must not
 * move the country's delivery rate as much as one with 12.000.
 */
function computeTotals(rows: CarrierRow[]): CarrierTotals {
  let shipments = 0;
  let delivered = 0;
  let returned = 0;
  let contribution = 0;
  let daysWeight = 0;
  let daysSum = 0;
  let freightTotal = 0;
  let freightShipments = 0;

  for (const row of rows) {
    shipments += row.shipments;
    delivered += row.delivered;
    returned += row.returned;
    contribution += row.contribution ?? 0;

    if (row.avg_days_to_deliver !== null && row.delivered > 0) {
      daysSum += row.avg_days_to_deliver * row.delivered;
      daysWeight += row.delivered;
    }

    const freight =
      row.freight_total ??
      (row.avg_freight_per_shipment !== null
        ? row.avg_freight_per_shipment * row.shipments
        : null);
    if (freight !== null && row.shipments > 0) {
      freightTotal += freight;
      freightShipments += row.shipments;
    }
  }

  return {
    shipments,
    deliveryRate: shipments > 0 ? (delivered / shipments) * 100 : null,
    returnRate: shipments > 0 ? (returned / shipments) * 100 : null,
    avgDays: daysWeight > 0 ? daysSum / daysWeight : null,
    avgFreight: freightShipments > 0 ? freightTotal / freightShipments : null,
    contribution,
  };
}

export default function CarrierTable({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<CarrierRow[]>(
    `/kpis/carriers?country=${countryCode}`,
  );

  const [sorting, setSorting] = useState<SortingState>([
    { id: "shipments", desc: true },
  ]);

  const rows = useMemo<CarrierRow[]>(() => data ?? [], [data]);
  const totals = useMemo(() => computeTotals(rows), [rows]);
  const shortSamples = useMemo(() => rows.filter(isShortSample).length, [rows]);

  // The country average hides the decision. Under the table, the biggest
  // zones where a DIFFERENT carrier from the country's leader delivers best -
  // the only rows where "which carrier" has an answer other than "the same".
  const { data: zones } = useApi<{ rows: CarrierZoneRow[] }>(
    `/kpis/carrier-by-zone?country=${countryCode}`,
    [countryCode],
  );
  const zoneNotes = useMemo(() => {
    const leader = [...rows].sort((a, b) => b.shipments - a.shipments)[0]?.carrier_name;
    const best = bestCarrierByZone(zones?.rows ?? []);
    return [...best.entries()]
      .filter(([, winner]) => winner.carrier_name !== leader)
      .sort(([, a], [, b]) => b.shipments - a.shipments)
      .slice(0, ZONE_NOTES);
  }, [rows, zones]);

  const columns = useMemo(() => {
    const column = createColumnHelper<CarrierRow>();

    return [
      column.accessor("carrier_name", {
        header: "Transportadora",
        cell: (info) => (
          <span className="block truncate font-medium text-ink-body">
            {info.getValue()}
          </span>
        ),
      }),
      column.accessor("shipments", {
        header: "Guías",
        sortingFn: numericSort,
        cell: (info) => formatNumber(info.getValue(), country, 0),
      }),
      column.accessor("delivery_rate_pct", {
        header: "% entrega",
        sortingFn: numericSort,
        cell: (info) => {
          const value = info.getValue();
          return (
            <div className="flex items-center gap-1">
              <div className="min-w-0 flex-1">
                <MicroBar
                  value={value}
                  max={100}
                  tone={deliveryTone(value)}
                  label={formatPercent(value)}
                />
              </div>
              {isShortSample(info.row.original) && <ShortSampleMark />}
            </div>
          );
        },
      }),
      column.accessor("return_rate_pct", {
        header: "% devolución",
        sortingFn: numericSort,
        cell: (info) => (
          <div className="flex items-center gap-1">
            <div className="min-w-0 flex-1">
              <MicroBar
                value={info.getValue()}
                max={100}
                tone="negative"
                label={formatPercent(info.getValue())}
              />
            </div>
            {isShortSample(info.row.original) && <ShortSampleMark />}
          </div>
        ),
      }),
      column.accessor("avg_days_to_deliver", {
        header: "Días prom.",
        sortingFn: numericSort,
        cell: (info) => formatNumber(info.getValue(), country, 1),
      }),
      column.accessor("p90_days_to_deliver", {
        header: "p90 días",
        sortingFn: numericSort,
        cell: (info) => formatNumber(info.getValue(), country, 1),
      }),
      column.accessor("avg_freight_per_shipment", {
        header: "Flete prom.",
        sortingFn: numericSort,
        cell: (info) => formatMoney(info.getValue(), country),
      }),
      column.accessor("contribution", {
        header: "Contribución",
        sortingFn: numericSort,
        cell: (info) => {
          const value = info.getValue();
          return (
            <span
              className={cx(
                "font-semibold",
                value === null
                  ? "text-ink-dim"
                  : value < 0
                    ? "text-negative-ink"
                    : "text-positive-ink",
              )}
            >
              {formatMoney(value, country)}
            </span>
          );
        },
      }),
    ];
  }, [country]);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (loading) {
    return (
      <Card title="Transportadoras" subtitle="Entrega, devoluciones y contribución">
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Transportadoras" subtitle="Entrega, devoluciones y contribución">
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Transportadoras" subtitle="Entrega, devoluciones y contribución">
        <EmptyState
          title="Ninguna transportadora con despachos"
          instruction="No hay guías despachadas en el rango seleccionado. Amplía las fechas, o conecta la transportadora en Configuración → Conexiones para que sus guías empiecen a sincronizar."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Transportadoras"
      subtitle="Entrega, devoluciones y contribución. Haz clic en una columna para ordenar."
      bodyClassName="p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-sm">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={
                        sorted === "asc"
                          ? "ascending"
                          : sorted === "desc"
                            ? "descending"
                            : "none"
                      }
                      className={cx(
                        "px-3 py-2 text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim",
                        RIGHT_ALIGNED.has(header.column.id) ? "text-right" : "text-left",
                      )}
                    >
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cx(
                          "inline-flex items-center gap-1 uppercase hover:text-ink-2",
                          sorted && "text-ink-2",
                        )}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden className="text-xs">
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                        </span>
                      </button>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>

          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-t border-line-row">
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className={cx(
                      "px-3 py-2 text-ink-2",
                      RIGHT_ALIGNED.has(cell.column.id) ? "text-right" : "text-left",
                      cell.column.id === "carrier_name" && "max-w-[200px]",
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>

          <tfoot>
            <tr className="border-t border-line-strong bg-sunken text-sm font-semibold text-ink-2">
              <td className="px-3 py-2 text-left">Total ponderado</td>
              <td className="px-3 py-2 text-right">
                {formatNumber(totals.shipments, country, 0)}
              </td>
              <td className="px-3 py-2">
                <MicroBar
                  value={totals.deliveryRate}
                  max={100}
                  tone={deliveryTone(totals.deliveryRate)}
                  label={formatPercent(totals.deliveryRate)}
                />
              </td>
              <td className="px-3 py-2">
                <MicroBar
                  value={totals.returnRate}
                  max={100}
                  tone="negative"
                  label={formatPercent(totals.returnRate)}
                />
              </td>
              <td className="px-3 py-2 text-right">
                {formatNumber(totals.avgDays, country, 1)}
              </td>
              {/* A p90 cannot be summed out of per-carrier p90s. Saying "—" is
                  honest; inventing an average of percentiles is not. */}
              <td className="px-3 py-2 text-right text-ink-dim">—</td>
              <td className="px-3 py-2 text-right">
                {formatMoney(totals.avgFreight, country)}
              </td>
              <td
                className={cx(
                  "px-3 py-2 text-right",
                  totals.contribution < 0 ? "text-negative-ink" : "text-positive-ink",
                )}
              >
                {formatMoney(totals.contribution, country)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* A lone "~" beside a number is only discreet if something says what it
          means. Shown only when a row actually carries one. */}
      {shortSamples > 0 && (
        <p className="border-t border-line-subtle px-3 py-2 text-xs leading-snug text-ink-dim">
          <span className="font-semibold text-ink-2">~</span> {SHORT_SAMPLE_HINT}{" "}
          {shortSamples === 1
            ? "Afecta a 1 transportadora."
            : `Afecta a ${shortSamples} transportadoras.`}
        </p>
      )}

      {zoneNotes.length > 0 && (
        <p className="border-t border-line-subtle px-3 py-2 text-xs leading-snug text-ink-muted">
          Mejor transportadora aquí:{" "}
          {zoneNotes.map(([zone, winner], index) => (
            <span key={zone}>
              {index > 0 && " · "}
              {zone} → <span className="font-semibold text-accent-ink">{winner.carrier_name}</span>{" "}
              ({formatPercent(winner.delivery_rate_pct)})
            </span>
          ))}
          . Medido en 90 días; el detalle por ciudad está en «Transportadora por zona».
        </p>
      )}
    </Card>
  );
}
