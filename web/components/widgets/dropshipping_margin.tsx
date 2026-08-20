"use client";

/**
 * The dropshipping margin chain, per product.
 *
 * Effi already records what you charged, what you paid the supplier and what
 * the freight cost - per guide. That is the whole P&L of a dropshipping
 * operation and nobody was reading it.
 *
 * THE POINT OF THE TABLE is the pair of columns at the right: delivery rate
 * next to break-even delivery rate. In COD you buy the stock and pay the
 * freight for every dispatch, delivered or not; so every product has a delivery
 * rate below which it loses money on each one. A product under its own
 * break-even is not "a bit weak" - it is a machine for turning ad spend into
 * losses, and the row is painted so you cannot scroll past it.
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

import { Card, Chip, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { DropshippingMarginRow } from "@/lib/types";

/** Figures that must line up on the right edge. */
const RIGHT_ALIGNED = new Set([
  "shipments",
  "units",
  "revenue",
  "supplier_cost",
  "gross_margin_pct",
  "net_contribution",
  "contribution_per_shipment",
]);

/** Nulls sort to the bottom instead of pretending to be zero. */
const numericSort: SortingFn<DropshippingMarginRow> = (rowA, rowB, columnId) => {
  const a = rowA.getValue<number | null>(columnId);
  const b = rowB.getValue<number | null>(columnId);
  const aMissing = a === null || a === undefined || !Number.isFinite(a);
  const bMissing = b === null || b === undefined || !Number.isFinite(b);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  return a === b ? 0 : a < b ? -1 : 1;
};

/**
 * True when the product delivers below the rate it needs to break even.
 *
 * Both figures have to exist. Without a supplier cost there is no break-even,
 * and guessing one would paint rows red on the strength of a missing number.
 */
function losesMoney(row: DropshippingMarginRow): boolean {
  const delivery = row.delivery_rate_pct;
  const breakeven = row.breakeven_delivery_pct;
  if (delivery === null || breakeven === null) return false;
  if (!Number.isFinite(delivery) || !Number.isFinite(breakeven)) return false;
  return delivery < breakeven;
}

export default function DropshippingMargin({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useApi<DropshippingMarginRow[]>(
    `/kpis/dropshipping-margin?country=${countryCode}`,
  );

  // Most negative contribution first: the products that cost you money are the
  // reason to open this widget, so they do not wait below the fold.
  const [sorting, setSorting] = useState<SortingState>([
    { id: "net_contribution", desc: false },
  ]);

  const rows = useMemo<DropshippingMarginRow[]>(() => data ?? [], [data]);

  const belowBreakeven = useMemo(() => rows.filter(losesMoney), [rows]);
  const unreviewed = useMemo(
    () => rows.filter((row) => !row.catalogue_reviewed).length,
    [rows],
  );

  /** What the losing products drain, so the legend can name a figure. */
  const lossFromBelowBreakeven = useMemo(
    () =>
      belowBreakeven.reduce(
        (total, row) => total + Math.min(0, row.net_contribution ?? 0),
        0,
      ),
    [belowBreakeven],
  );

  const columns = useMemo(() => {
    const column = createColumnHelper<DropshippingMarginRow>();

    return [
      column.accessor("product_name", {
        header: "Producto",
        cell: (info) => {
          const row = info.row.original;
          return (
            <div className="min-w-0">
              <span className="block truncate font-medium text-ink-body">
                {info.getValue()}
              </span>
              <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
                {row.sku && (
                  <span className="text-[10.5px] text-ink-dim">{row.sku}</span>
                )}
                <span className="text-[10.5px] text-ink-dim">{row.supplier_name}</span>
                {!row.catalogue_reviewed && (
                  <Chip tone="warning" className="cursor-help">
                    {/* Native title so the explanation travels with the chip. */}
                    <span title="El costo lo dedujimos de los reportes. Nadie lo confirmó.">
                      Costo estimado
                    </span>
                  </Chip>
                )}
              </span>
            </div>
          );
        },
      }),
      column.accessor("shipments", {
        header: "Guías",
        sortingFn: numericSort,
        cell: (info) => formatNumber(info.getValue(), country, 0),
      }),
      column.accessor("units", {
        header: "Unidades",
        sortingFn: numericSort,
        cell: (info) => formatNumber(info.getValue(), country, 0),
      }),
      column.accessor("revenue", {
        header: "Recaudo",
        sortingFn: numericSort,
        cell: (info) => formatMoney(info.getValue(), country),
      }),
      column.accessor("supplier_cost", {
        header: "Costo proveedor",
        sortingFn: numericSort,
        cell: (info) => formatMoney(info.getValue(), country),
      }),
      column.accessor("gross_margin_pct", {
        header: "Margen bruto %",
        sortingFn: numericSort,
        cell: (info) => formatPercent(info.getValue()),
      }),
      column.accessor("net_contribution", {
        header: "Contribución neta",
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
                    ? "text-negative"
                    : "text-positive",
              )}
            >
              {formatMoney(value, country)}
            </span>
          );
        },
      }),
      column.accessor("contribution_per_shipment", {
        header: "Contrib./guía",
        sortingFn: numericSort,
        cell: (info) => {
          const value = info.getValue();
          return (
            <span
              className={cx(
                value === null
                  ? "text-ink-dim"
                  : value < 0
                    ? "text-negative"
                    : "text-ink-2",
              )}
            >
              {formatMoney(value, country)}
            </span>
          );
        },
      }),
      // The two columns below sit side by side on purpose: one is only
      // readable against the other.
      column.accessor("delivery_rate_pct", {
        header: "% entrega",
        sortingFn: numericSort,
        cell: (info) => {
          const row = info.row.original;
          const value = info.getValue();
          return (
            <span
              className={cx(
                "font-semibold",
                losesMoney(row) ? "text-negative" : "text-ink-2",
              )}
            >
              {formatPercent(value)}
            </span>
          );
        },
      }),
      column.accessor("breakeven_delivery_pct", {
        header: "% entrega de equilibrio",
        sortingFn: numericSort,
        cell: (info) => (
          <span className="text-ink-muted">{formatPercent(info.getValue())}</span>
        ),
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

  const SUBTITLE =
    "Lo que cobras, lo que le pagas al proveedor y lo que queda, por producto.";

  if (loading) {
    return (
      <Card title="Cadena de márgenes" subtitle={SUBTITLE}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Cadena de márgenes" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Cadena de márgenes" subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no hay productos con despachos"
          instruction="Sube un reporte de guías desde Cargar datos. Los productos aparecen solos con el primer archivo, y el margen se calcula apenas cada uno tenga su costo cargado en Productos."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Cadena de márgenes"
      subtitle={SUBTITLE}
      bodyClassName="p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1040px] border-collapse text-[12px]">
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
                        "px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim",
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
                        <span aria-hidden className="text-[9px]">
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
            {table.getRowModel().rows.map((row) => {
              const bleeding = losesMoney(row.original);
              return (
                <tr
                  key={row.id}
                  className={cx(
                    "border-t border-line-row",
                    bleeding && "bg-negative/[0.06]",
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cx(
                        "px-3 py-2 text-ink-2",
                        RIGHT_ALIGNED.has(cell.column.id) ? "text-right" : "text-left",
                        cell.column.id === "product_name" && "max-w-[260px]",
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="space-y-1.5 border-t border-line-subtle px-4 py-3">
        <p
          className={cx(
            "text-[11.5px] leading-relaxed",
            belowBreakeven.length > 0 ? "text-negative" : "text-ink-dim",
          )}
        >
          {belowBreakeven.length > 0
            ? `Las filas resaltadas entregan por debajo de su punto de equilibrio: cada guía que despachas de esos ${belowBreakeven.length} productos pierde plata, porque el costo y el flete se pagan también por las que se devuelven (${formatMoney(lossFromBelowBreakeven, country)} en el período).`
            : "Ningún producto entrega por debajo de su punto de equilibrio: todos cubren el costo del proveedor y el flete de las guías que se devuelven."}
        </p>

        {unreviewed > 0 && (
          <p className="text-[11.5px] leading-relaxed text-ink-dim">
            {unreviewed === 1
              ? "Un producto todavía tiene el costo estimado a partir de los reportes, no confirmado por una persona. Ajústalo en Productos para que su margen sea real."
              : `${unreviewed} productos todavía tienen el costo estimado a partir de los reportes, no confirmado por una persona. Ajústalos en Productos para que su margen sea real.`}
          </p>
        )}
      </div>
    </Card>
  );
}
