"use client";

/**
 * What the freight really costs, per carrier and service.
 *
 * "Average freight" is a trap: a carrier that moves your heavy products looks
 * expensive and a carrier that moves the light ones looks cheap, and neither
 * number says anything. FREIGHT PER KILO is the comparable figure, so it is the
 * one set in bold with a bar next to it - it is what you take into the
 * negotiation.
 *
 * The components column exists because a bill is not one number: base freight,
 * handling and the collection fee move for different reasons, and the fee for
 * collecting cash is the one nobody looks at.
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

import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { useRangedApi } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import type { FreightAnalysisRow } from "@/lib/types";
import { CHART } from "@/lib/chart-palette";

/** A service level nobody filled in. Not worth repeating in every label. */
const NO_SERVICE = "Sin servicio";

const RIGHT_ALIGNED = new Set([
  "shipments",
  "avg_weight_kg",
  "avg_freight",
  "freight_per_kg",
  "freight_share_of_value_pct",
]);

/**
 * The three parts of a freight bill.
 *
 * Deliberately three greys and not three semantic colours: none of these is
 * good or bad on its own, and green/amber/red here would claim a verdict the
 * data does not support.
 */
const COMPONENTS = [
  { key: "avg_freight_base" as const, label: "Flete base", colour: CHART.neutral },
  { key: "avg_handling" as const, label: "Manejo", colour: CHART.neutralBar },
  { key: "avg_collection_fee" as const, label: "Recaudo", colour: CHART.dim },
];

/** Nulls sort to the bottom instead of pretending to be zero. */
const numericSort: SortingFn<FreightAnalysisRow> = (rowA, rowB, columnId) => {
  const a = rowA.getValue<number | null>(columnId);
  const b = rowB.getValue<number | null>(columnId);
  const aMissing = a === null || a === undefined || !Number.isFinite(a);
  const bMissing = b === null || b === undefined || !Number.isFinite(b);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  return a === b ? 0 : a < b ? -1 : 1;
};

function laneLabel(row: FreightAnalysisRow): string {
  return row.service_level && row.service_level !== NO_SERVICE
    ? `${row.carrier_name} · ${row.service_level}`
    : row.carrier_name;
}

/** The three components drawn to scale inside the cell. */
function ComponentBar({
  row,
  country,
}: {
  row: FreightAnalysisRow;
  country: FormatCountry;
}) {
  const parts = COMPONENTS.map((component) => ({
    ...component,
    value: row[component.key] ?? 0,
  }));
  const total = parts.reduce((sum, part) => sum + part.value, 0);

  if (total <= 0) {
    return <span className="text-xs text-ink-dim">sin desglose</span>;
  }

  const title = parts
    .map((part) => `${part.label}: ${formatMoney(part.value, country)}`)
    .join(" · ");

  return (
    <div className="flex h-[8px] w-full min-w-[92px] overflow-hidden rounded-full bg-track" title={title}>
      {parts.map((part) => (
        <div
          key={part.key}
          style={{ width: `${(part.value / total) * 100}%`, background: part.colour }}
          aria-hidden
        />
      ))}
      <span className="sr-only">{title}</span>
    </div>
  );
}

export default function FreightAnalysis({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<FreightAnalysisRow[]>(
    `/kpis/freight?country=${countryCode}`,
  );

  const [sorting, setSorting] = useState<SortingState>([
    { id: "shipments", desc: true },
  ]);

  const rows = useMemo<FreightAnalysisRow[]>(() => data ?? [], [data]);

  /** The scale for the per-kilo bar: dearest lane fills it. */
  const maxPerKg = useMemo(
    () =>
      rows.reduce((max, row) => {
        const value = row.freight_per_kg;
        return value !== null && Number.isFinite(value) ? Math.max(max, value) : max;
      }, 0),
    [rows],
  );

  const totals = useMemo(() => {
    const discount = rows.reduce((total, row) => total + (row.discount_value ?? 0), 0);
    const freight = rows.reduce((total, row) => total + (row.freight_total ?? 0), 0);
    const returnFreight = rows.reduce(
      (total, row) => total + (row.return_freight_total ?? 0),
      0,
    );
    // Weighted by guides: a lane with 8 guides must not move the average.
    let discountWeighted = 0;
    let discountWeight = 0;
    for (const row of rows) {
      if (row.avg_discount_pct === null || !Number.isFinite(row.avg_discount_pct)) continue;
      discountWeighted += row.avg_discount_pct * row.shipments;
      discountWeight += row.shipments;
    }

    return {
      discount,
      freight,
      returnFreight,
      discountPct: discountWeight > 0 ? discountWeighted / discountWeight : null,
    };
  }, [rows]);

  const columns = useMemo(() => {
    const column = createColumnHelper<FreightAnalysisRow>();

    return [
      column.accessor((row) => laneLabel(row), {
        id: "lane",
        header: "Transportadora y servicio",
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
      column.accessor("avg_weight_kg", {
        header: "Peso prom.",
        sortingFn: numericSort,
        cell: (info) => {
          const value = info.getValue();
          return value === null ? "—" : `${formatNumber(value, country, 2)} kg`;
        },
      }),
      column.accessor("avg_freight", {
        header: "Flete prom.",
        sortingFn: numericSort,
        cell: (info) => formatMoney(info.getValue(), country),
      }),
      // The comparable number. Everything else on this row is context for it.
      column.accessor("freight_per_kg", {
        header: "Flete por kilo",
        sortingFn: numericSort,
        cell: (info) => (
          <span className="block text-base font-semibold text-ink">
            {formatMoney(info.getValue(), country)}
          </span>
        ),
      }),
      column.accessor("freight_share_of_value_pct", {
        header: "% del valor",
        sortingFn: numericSort,
        cell: (info) => formatPercent(info.getValue()),
      }),
      column.accessor((row) => row.freight_per_kg, {
        id: "per_kg_bar",
        header: "Comparativo por kilo",
        enableSorting: false,
        cell: (info) => (
          <MicroBar value={info.getValue()} max={maxPerKg} tone="accent" />
        ),
      }),
      column.display({
        id: "components",
        header: "Base · manejo · recaudo",
        cell: (info) => <ComponentBar row={info.row.original} country={country} />,
      }),
    ];
  }, [country, maxPerKg]);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const SUBTITLE =
    "Flete por kilo, por componente, y cuánto vale tu descuento negociado.";

  if (loading) {
    return (
      <Card title="Análisis de flete" subtitle={SUBTITLE}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Análisis de flete" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Análisis de flete" subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no hay fletes para comparar"
          instruction="Sube un reporte de guías que traiga el peso y el valor del flete desde Cargar datos. Sin el peso no se puede calcular el flete por kilo, que es el único número comparable entre transportadoras."
        />
      </Card>
    );
  }

  return (
    <Card title="Análisis de flete" subtitle={SUBTITLE} bodyClassName="p-0">
      <div className="data-table">
        <table className="w-full min-w-[900px] border-collapse text-sm">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  const sortable = header.column.getCanSort();
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
                      {sortable ? (
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
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
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
                      cell.column.id === "lane" && "max-w-[220px]",
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="space-y-1.5 border-t border-line-subtle px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-dim">
          {COMPONENTS.map((component) => (
            <span key={component.key} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block size-[8px] rounded-sm"
                style={{ background: component.colour }}
              />
              {component.label}
            </span>
          ))}
        </div>

        <p className="text-sm leading-relaxed text-ink-dim">
          {totals.discount > 0
            ? `Tu descuento negociado (${formatPercent(totals.discountPct)} en promedio) te ahorró ${formatMoney(totals.discount, country)} en el período: sin él, el flete habría costado ${formatMoney(totals.freight + totals.discount, country)} en vez de ${formatMoney(totals.freight, country)}.`
            : "Todavía no hay un descuento negociado registrado en los reportes: el flete se está pagando a tarifa plena."}
        </p>

        {totals.returnFreight > 0 && (
          <p className="text-sm leading-relaxed text-ink-dim">
            Aparte, las devoluciones costaron {formatMoney(totals.returnFreight, country)}{" "}
            en flete de regreso, que ninguna guía entregada compensa.
          </p>
        )}
      </div>
    </Card>
  );
}
