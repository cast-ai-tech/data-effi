import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  DailyStatusMatrix,
  DailyStatusTable,
  groupByPlatform,
  matrixColumns,
  shortDayLabel,
  sumBlock,
} from "@/components/widgets/daily_status_table";
import { STATUS_GROUP_LABELS } from "@/lib/status";
import type { DailyStatusRow } from "@/lib/types";

/**
 * The daily table shows the operator's five words as columns, in their order
 * (migration 045). A day's row and the TOTAL GENERAL row must both add up
 * across the five groups to the day's guides: nothing falls between columns.
 */

const COUNTRY = {
  code: "EC",
  currency_code: "USD",
  currency_symbol: "$",
  decimal_places: 2,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-EC",
};

function row(overrides: Partial<DailyStatusRow>): DailyStatusRow {
  const base: DailyStatusRow = {
    country_code: "EC",
    platform_code: "effi",
    platform_name: "Effi",
    day: "2026-08-01",
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
    currency_code: "USD",
  };
  const merged = { ...base, ...overrides };
  merged.shipments =
    merged.entregada +
    merged.devolucion +
    merged.en_transito +
    merged.novedad +
    merged.indemnizacion;
  if (overrides.pct_devolucion_total === undefined && merged.shipments > 0) {
    // Igual que f_daily_status: todo lo no-entregado salvo indemnización / total.
    merged.pct_devolucion_total =
      ((merged.shipments - merged.entregada - merged.indemnizacion) /
        merged.shipments) *
      100;
  }
  return merged;
}

const ROWS: DailyStatusRow[] = [
  row({
    day: "2026-08-01",
    entregada: 26,
    devolucion: 15,
    en_transito: 0,
    novedad: 0,
    cerradas: 41,
  }),
  row({
    day: "2026-08-03",
    entregada: 52,
    devolucion: 13,
    en_transito: 1,
    novedad: 2,
    indemnizacion: 1,
    cerradas: 66,
  }),
  row({
    platform_code: "dropi",
    platform_name: "Dropi",
    day: "2026-08-10",
    entregada: 18,
    devolucion: 4,
    en_transito: 3,
    novedad: 1,
    cerradas: 22,
  }),
];

afterEach(cleanup);

describe("sumBlock", () => {
  it("adds the five groups and they account for every guide", () => {
    const totals = sumBlock(ROWS.filter((r) => r.platform_code === "effi"));
    expect(totals.shipments).toBe(110);
    expect(
      totals.entregada +
        totals.devolucion +
        totals.en_transito +
        totals.novedad +
        totals.indemnizacion,
    ).toBe(totals.shipments);
    expect(totals.indemnizacion).toBe(1);
    // Devolución = todo lo no-entregado salvo indemnización, sobre el total:
    // (110 - 78 entregadas - 1 indemnizada) / 110 = 31/110. Antes medía solo el
    // bucket de devueltas (28/110), y el total no cuadraba con las filas diarias.
    expect(totals.pctDevolucionTotal).toBeCloseTo(
      ((110 - 78 - 1) / 110) * 100,
      5,
    );
  });
});

describe("groupByPlatform", () => {
  it("makes one block per platform, biggest first", () => {
    const blocks = groupByPlatform(ROWS);
    expect(blocks.map((b) => b.code)).toEqual(["effi", "dropi"]);
    expect(blocks[0].rows.map((r) => r.day)).toEqual([
      "2026-08-01",
      "2026-08-03",
    ]);
  });
});

describe("DailyStatusTable", () => {
  it("shows the five words as columns, in the operator's order", () => {
    const [block] = groupByPlatform(ROWS);
    render(<DailyStatusTable block={block} country={COUNTRY} />);

    const headers = screen
      .getAllByRole("columnheader")
      .map((th) => th.textContent?.trim());
    expect(headers.slice(1, 6)).toEqual([
      STATUS_GROUP_LABELS.entregada,
      STATUS_GROUP_LABELS.en_transito,
      STATUS_GROUP_LABELS.novedad,
      STATUS_GROUP_LABELS.devolucion,
      STATUS_GROUP_LABELS.indemnizacion,
    ]);
    expect(headers.join(" ")).not.toMatch(/en calle|muerta|en camino/i);
  });

  it("puts every guide of a day in exactly one of the five columns", () => {
    const [block] = groupByPlatform(ROWS);
    render(<DailyStatusTable block={block} country={COUNTRY} />);

    const body = screen.getAllByRole("rowgroup")[1];
    const secondDay = within(body).getAllByRole("row")[1];
    const cells = within(secondDay)
      .getAllByRole("cell")
      .map((td) => td.textContent?.trim());
    // Fecha, Entregado, En tránsito, Novedad, Devolución, Indemnización, Total
    expect(cells.slice(1, 7)).toEqual(["52", "1", "2", "13", "1", "69"]);
  });

  it("fills a missing day with zeros instead of skipping it", () => {
    const [block] = groupByPlatform(ROWS);
    render(
      <DailyStatusTable
        block={block}
        country={COUNTRY}
        fillDays={["2026-08-01", "2026-08-02", "2026-08-03"]}
      />,
    );
    expect(screen.getByText("sin guías")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The matrix the printable report uses: states down, days across
// ---------------------------------------------------------------------------

describe("shortDayLabel", () => {
  it("heads a column the way the sheet does", () => {
    expect(shortDayLabel("2026-08-01")).toBe("1-ago");
    expect(shortDayLabel("2026-12-25")).toBe("25-dic");
    expect(shortDayLabel("no-es-fecha")).toBe("no-es-fecha");
  });
});

describe("matrixColumns", () => {
  it("uses the block's own days when no calendar is given", () => {
    const [block] = groupByPlatform(ROWS);
    expect(matrixColumns(block).map((c) => c.day)).toEqual([
      "2026-08-01",
      "2026-08-03",
    ]);
  });

  it("fills a calendar with empty columns for the days without guides", () => {
    const [block] = groupByPlatform(ROWS);
    const columns = matrixColumns(block, [
      "2026-08-01",
      "2026-08-02",
      "2026-08-03",
    ]);
    expect(columns.map((c) => c.day)).toEqual([
      "2026-08-01",
      "2026-08-02",
      "2026-08-03",
    ]);
    expect(columns[1].row).toBeNull();
  });
});

describe("DailyStatusMatrix", () => {
  it("puts the five words down the side and the days across the top", () => {
    const [block] = groupByPlatform(ROWS);
    render(<DailyStatusMatrix block={block} country={COUNTRY} />);

    const headers = screen
      .getAllByRole("columnheader")
      .map((th) => th.textContent?.trim());
    expect(headers).toEqual(["Estado", "1-ago", "3-ago", "Total general"]);

    const rowHeaders = screen
      .getAllByRole("rowheader")
      .map((th) => th.textContent?.trim());
    expect(rowHeaders).toEqual([
      STATUS_GROUP_LABELS.entregada,
      STATUS_GROUP_LABELS.en_transito,
      STATUS_GROUP_LABELS.novedad,
      STATUS_GROUP_LABELS.devolucion,
      STATUS_GROUP_LABELS.indemnizacion,
      "Total general",
      "Devoluciones",
      "Porcentaje devoluciones",
    ]);
  });

  it("closes with the totals, the returns and the return percentage per day", () => {
    const [block] = groupByPlatform(ROWS);
    render(<DailyStatusMatrix block={block} country={COUNTRY} />);

    const foot = screen.getAllByRole("rowgroup")[2];
    const [totalRow, returnsRow, pctRow] = within(foot).getAllByRole("row");
    const cells = (row: HTMLElement) =>
      within(row)
        .getAllByRole("cell")
        .map((td) => td.textContent?.trim());
    expect(cells(totalRow)).toEqual(["41", "69", "110"]);
    expect(cells(returnsRow)).toEqual(["15", "13", "28"]);
    // % = todo lo no-entregado salvo indemnización / total (no solo el bucket de
    // devueltas). Día 1: (41-26-0)/41 = 15/41 -> "37%". Día 2: (69-52-1)/69 =
    // 16/69 -> "23%". Total: (110-78-1)/110 = 31/110 -> "28%".
    expect(cells(pctRow).map((c) => c?.replace("~", ""))).toEqual([
      "37%",
      "23%",
      "28%",
    ]);
  });
});
