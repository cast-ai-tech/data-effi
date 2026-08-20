"use client";

/**
 * The product P&L, one row per product.
 *
 * The column that matters is Contrib./guía: a product can be the top of the
 * catalogue by contribution and still lose money on every single dispatch, and
 * that row is painted so you cannot scroll past it.
 */

import { useMemo, useState } from "react";
import type { ColumnDef, SortingState } from "@tanstack/react-table";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

import { Card, EmptyState, ErrorState, MicroBar, SkeletonRows, cx } from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import type { FormatCountry } from "@/lib/format";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { ProductRow } from "@/lib/types";

/** Mercancía + flete: what leaving the warehouse actually costs. */
function totalCost(row: ProductRow): number | null {
  if (row.cogs === null && row.freight === null) return null;
  return (row.cogs ?? 0) + (row.freight ?? 0);
}

function moneyTone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "text-ink-dim";
  if (value < 0) return "text-negative";
  if (value > 0) return "text-positive";
  return "text-ink-2";
}

function deliveryTone(value: number | null): "positive" | "warning" | "negative" | "neutral" {
  if (value === null || !Number.isFinite(value)) return "neutral";
  if (value >= 70) return "positive";
  if (value >= 55) return "warning";
  return "negative";
}

function buildColumns(country: FormatCountry): Array<ColumnDef<ProductRow>> {
  return [
    {
      id: "product_name",
      accessorFn: (row) => row.product_name,
      header: "Producto",
      cell: ({ row }) => (
        <div className="min-w-0">
          <p className="truncate text-[12.5px] text-ink">{row.original.product_name}</p>
          {row.original.sku && (
            <p className="truncate text-[10.5px] text-ink-dim">{row.original.sku}</p>
          )}
        </div>
      ),
      meta: { align: "left", width: "min-w-[180px]" },
    },
    {
      id: "shipments",
      accessorFn: (row) => row.shipments,
      header: "Guías",
      cell: ({ row }) => formatNumber(row.original.shipments, country, 0),
      meta: { align: "right" },
    },
    {
      id: "units",
      accessorFn: (row) => row.units ?? undefined,
      sortUndefined: "last",
      header: "Unidades",
      cell: ({ row }) => formatNumber(row.original.units, country, 0),
      meta: { align: "right" },
    },
    {
      id: "delivery_rate_pct",
      accessorFn: (row) => row.delivery_rate_pct ?? undefined,
      sortUndefined: "last",
      header: "% entrega",
      cell: ({ row }) => (
        <MicroBar
          value={row.original.delivery_rate_pct}
          max={100}
          tone={deliveryTone(row.original.delivery_rate_pct)}
          label={formatPercent(row.original.delivery_rate_pct)}
        />
      ),
      meta: { align: "left", width: "w-[128px]" },
    },
    {
      id: "revenue",
      accessorFn: (row) => row.revenue ?? undefined,
      sortUndefined: "last",
      header: "Recaudo",
      cell: ({ row }) => formatMoney(row.original.revenue, country),
      meta: { align: "right" },
    },
    {
      id: "cost",
      accessorFn: (row) => totalCost(row) ?? undefined,
      sortUndefined: "last",
      header: "Costo",
      cell: ({ row }) => formatMoney(totalCost(row.original), country),
      meta: { align: "right" },
    },
    {
      id: "contribution",
      accessorFn: (row) => row.contribution ?? undefined,
      sortUndefined: "last",
      header: "Contribución",
      cell: ({ row }) => (
        <span className={moneyTone(row.original.contribution)}>
          {formatMoney(row.original.contribution, country)}
        </span>
      ),
      meta: { align: "right" },
    },
    {
      id: "contribution_per_shipment",
      accessorFn: (row) => row.contribution_per_shipment ?? undefined,
      sortUndefined: "last",
      header: "Contrib./guía",
      cell: ({ row }) => (
        <span className={moneyTone(row.original.contribution_per_shipment)}>
          {formatMoney(row.original.contribution_per_shipment, country)}
        </span>
      ),
      meta: { align: "right" },
    },
    {
      id: "margin_pct",
      accessorFn: (row) => row.margin_pct ?? undefined,
      sortUndefined: "last",
      header: "Margen %",
      cell: ({ row }) => (
        <span className={moneyTone(row.original.margin_pct)}>
          {formatPercent(row.original.margin_pct)}
        </span>
      ),
      meta: { align: "right" },
    },
  ];
}

function alignOf(meta: unknown): string {
  const align = (meta as { align?: string } | undefined)?.align;
  return align === "left" ? "text-left" : "text-right";
}

function widthOf(meta: unknown): string {
  return (meta as { width?: string } | undefined)?.width ?? "";
}

export default function ProductTable({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useApi<ProductRow[]>(
    `/kpis/products?country=${countryCode}`,
    [countryCode],
  );

  const [sorting, setSorting] = useState<SortingState>([
    { id: "contribution", desc: true },
  ]);

  const columns = useMemo(() => buildColumns(country), [country]);
  const rows = useMemo(() => data ?? [], [data]);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const bleeding = rows.filter(
    (row) =>
      row.contribution_per_shipment !== null && row.contribution_per_shipment < 0,
  ).length;

  const subtitle = "Recaudo, costo y contribución por producto en el rango seleccionado";

  if (loading) {
    return (
      <Card title="Rentabilidad por producto" subtitle={subtitle}>
        <SkeletonRows rows={8} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Rentabilidad por producto" subtitle={subtitle}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Rentabilidad por producto" subtitle={subtitle}>
        <EmptyState
          title="Todavía no hay productos con despachos"
          instruction="Cargue el catálogo con SKU y costo unitario, y verifique que las guías traigan el SKU del producto. Sin ese cruce Data Effi no puede repartir el recaudo entre productos."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Rentabilidad por producto"
      subtitle={subtitle}
      actions={
        bleeding > 0 ? (
          <span className="text-[11px] text-negative">
            {bleeding} {bleeding === 1 ? "producto pierde" : "productos pierden"} plata por guía
          </span>
        ) : undefined
      }
      bodyClassName="p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-line-subtle">
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
                        "px-3 py-2 text-[10.5px] font-bold uppercase tracking-[0.06em] text-ink-faint",
                        alignOf(header.column.columnDef.meta),
                        widthOf(header.column.columnDef.meta),
                      )}
                    >
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cx(
                          "inline-flex items-center gap-1 uppercase tracking-[0.06em] hover:text-ink-2",
                          sorted && "text-ink-2",
                        )}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span aria-hidden className="text-[9px]">
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : "↕"}
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
              const perShipment = row.original.contribution_per_shipment;
              const losing = perShipment !== null && perShipment < 0;
              return (
                <tr
                  key={row.id}
                  className={cx(
                    "border-b border-line-row last:border-0",
                    losing ? "bg-negative/[0.06]" : "hover:bg-white/[0.02]",
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={cx(
                        "px-3 py-2 text-[12px] text-ink-2",
                        alignOf(cell.column.columnDef.meta),
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
    </Card>
  );
}
