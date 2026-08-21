import { describe, expect, it } from "vitest";

import {
  MAX_RANGE,
  addDays,
  endOfMonth,
  formatRangeLabel,
  fromIso,
  inferMode,
  isValidRange,
  resolvePreset,
  startOfMonth,
  toIso,
  unwrapRangePayload,
  withRange,
} from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";

/**
 * Date arithmetic is where a filter quietly lies.
 *
 * Every case here is one that has shipped broken in some dashboard somewhere:
 * "mes pasado" in January landing in month -1, a February that forgot the leap
 * day, a "últimos 7 días" that silently included today and dragged the average
 * down, an ISO string parsed as UTC and rendered a day early. The arithmetic is
 * pure precisely so it can be pinned down here instead of eyeballed on screen.
 */

/** Local midnight, never `new Date("...")`, which parses as UTC. */
function day(year: number, month: number, date: number): Date {
  return new Date(year, month - 1, date);
}

describe("toIso / fromIso", () => {
  it("round-trips a local date without shifting it a day", () => {
    expect(toIso(day(2026, 7, 1))).toBe("2026-07-01");
    expect(toIso(fromIso("2026-07-01")!)).toBe("2026-07-01");
  });

  it("pads single-digit months and days", () => {
    expect(toIso(day(2026, 1, 5))).toBe("2026-01-05");
  });

  it("rejects anything that is not a plain ISO date", () => {
    expect(fromIso("ayer")).toBeNull();
    expect(fromIso("2026-7-1")).toBeNull();
    expect(fromIso("")).toBeNull();
    expect(fromIso(null)).toBeNull();
  });
});

describe("addDays / startOfMonth / endOfMonth", () => {
  it("crosses a month backwards", () => {
    expect(toIso(addDays(day(2026, 3, 2), -7))).toBe("2026-02-23");
  });

  it("crosses a year backwards", () => {
    expect(toIso(addDays(day(2026, 1, 3), -5))).toBe("2025-12-29");
  });

  it("knows February in a leap year and in an ordinary one", () => {
    expect(toIso(endOfMonth(day(2024, 2, 10)))).toBe("2024-02-29");
    expect(toIso(endOfMonth(day(2026, 2, 10)))).toBe("2026-02-28");
  });

  it("starts and ends a 31-day month", () => {
    expect(toIso(startOfMonth(day(2026, 8, 20)))).toBe("2026-08-01");
    expect(toIso(endOfMonth(day(2026, 8, 20)))).toBe("2026-08-31");
  });
});

describe("resolvePreset", () => {
  const today = day(2026, 8, 20); // a Thursday, mid-month, nothing special

  it("Hoy is a single day", () => {
    expect(resolvePreset("hoy", today)).toEqual({ from: "2026-08-20", to: "2026-08-20" });
  });

  it("Ayer is a single day, the one before", () => {
    expect(resolvePreset("ayer", today)).toEqual({ from: "2026-08-19", to: "2026-08-19" });
  });

  it("Últimos 7 días ends yesterday and spans exactly seven days", () => {
    // Today is half-finished; folding it in drags every average down and makes
    // "Hoy" a redundant tail of this window.
    expect(resolvePreset("7d", today)).toEqual({ from: "2026-08-13", to: "2026-08-19" });
  });

  it("Últimos 14 / 28 / 30 días span exactly their number of days", () => {
    expect(resolvePreset("14d", today)).toEqual({ from: "2026-08-06", to: "2026-08-19" });
    expect(resolvePreset("28d", today)).toEqual({ from: "2026-07-23", to: "2026-08-19" });
    expect(resolvePreset("30d", today)).toEqual({ from: "2026-07-21", to: "2026-08-19" });
  });

  it("Últimos 7 días crosses a month boundary", () => {
    expect(resolvePreset("7d", day(2026, 3, 2))).toEqual({
      from: "2026-02-23",
      to: "2026-03-01",
    });
  });

  it("Últimos 30 días crosses a year boundary", () => {
    expect(resolvePreset("30d", day(2026, 1, 10))).toEqual({
      from: "2025-12-11",
      to: "2026-01-09",
    });
  });

  it("Este mes runs from the 1st to today, today included", () => {
    expect(resolvePreset("este_mes", today)).toEqual({
      from: "2026-08-01",
      to: "2026-08-20",
    });
  });

  it("Este mes on the 1st is a single day", () => {
    expect(resolvePreset("este_mes", day(2026, 8, 1))).toEqual({
      from: "2026-08-01",
      to: "2026-08-01",
    });
  });

  it("Mes pasado is the whole previous month", () => {
    expect(resolvePreset("mes_pasado", today)).toEqual({
      from: "2026-07-01",
      to: "2026-07-31",
    });
  });

  it("Mes pasado in January is December of the previous year", () => {
    expect(resolvePreset("mes_pasado", day(2026, 1, 15))).toEqual({
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("Mes pasado on the 1st of January still lands in December", () => {
    expect(resolvePreset("mes_pasado", day(2026, 1, 1))).toEqual({
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("Mes pasado in March keeps the leap day", () => {
    expect(resolvePreset("mes_pasado", day(2024, 3, 5))).toEqual({
      from: "2024-02-01",
      to: "2024-02-29",
    });
    expect(resolvePreset("mes_pasado", day(2026, 3, 5))).toEqual({
      from: "2026-02-01",
      to: "2026-02-28",
    });
  });

  it("Mes pasado from the 31st does not overflow into a short month", () => {
    // Naive `setMonth(month - 1)` on the 31st of March gives the 3rd of March.
    expect(resolvePreset("mes_pasado", day(2026, 3, 31))).toEqual({
      from: "2026-02-01",
      to: "2026-02-28",
    });
  });

  it("Máximo sends nothing at all", () => {
    expect(resolvePreset("maximo", today)).toEqual(MAX_RANGE);
    expect(resolvePreset("maximo", today)).toEqual({ from: null, to: null });
  });
});

describe("inferMode", () => {
  const today = day(2026, 8, 20);

  it("recognises every shortcut it produced", () => {
    for (const preset of ["ayer", "7d", "14d", "28d", "30d", "mes_pasado"] as const) {
      expect(inferMode(resolvePreset(preset, today), today)).toBe(preset);
    }
  });

  it("calls an empty range Máximo", () => {
    expect(inferMode(MAX_RANGE, today)).toBe("maximo");
    expect(inferMode({ from: null, to: null }, null)).toBe("maximo");
  });

  it("prefers Hoy over Este mes on the 1st, when both resolve alike", () => {
    const first = day(2026, 8, 1);
    expect(resolvePreset("hoy", first)).toEqual(resolvePreset("este_mes", first));
    expect(inferMode(resolvePreset("hoy", first), first)).toBe("hoy");
  });

  it("calls a hand-picked range personalizado", () => {
    expect(inferMode({ from: "2026-05-03", to: "2026-06-11" }, today)).toBe("personalizado");
  });

  it("cannot name a shortcut before it knows the reader's today", () => {
    // Server-side there is no reliable "today", and guessing one is how a
    // hydration mismatch gets shipped.
    expect(inferMode({ from: "2026-08-13", to: "2026-08-19" }, null)).toBe("personalizado");
  });
});

describe("isValidRange", () => {
  it("accepts an ordered range and a single day", () => {
    expect(isValidRange({ from: "2026-08-01", to: "2026-08-31" })).toBe(true);
    expect(isValidRange({ from: "2026-08-01", to: "2026-08-01" })).toBe(true);
  });

  it("accepts an open end", () => {
    expect(isValidRange({ from: "2026-08-01", to: null })).toBe(true);
    expect(isValidRange({ from: null, to: "2026-08-01" })).toBe(true);
    expect(isValidRange(MAX_RANGE)).toBe(true);
  });

  it("rejects an inverted range, which is the 422 the API answers with", () => {
    expect(isValidRange({ from: "2026-08-31", to: "2026-08-01" })).toBe(false);
  });
});

describe("withRange", () => {
  it("sends no parameters for Máximo", () => {
    expect(withRange("/kpis/aging?country=CO", MAX_RANGE)).toBe("/kpis/aging?country=CO");
    expect(withRange("/kpis/global", MAX_RANGE)).toBe("/kpis/global");
  });

  it("appends with & when the path already has a query", () => {
    expect(withRange("/kpis/aging?country=CO", { from: "2026-08-01", to: "2026-08-31" })).toBe(
      "/kpis/aging?country=CO&date_from=2026-08-01&date_to=2026-08-31",
    );
  });

  it("appends with ? when it does not", () => {
    expect(withRange("/kpis/global", { from: "2026-08-01", to: "2026-08-31" })).toBe(
      "/kpis/global?date_from=2026-08-01&date_to=2026-08-31",
    );
  });

  it("sends only the end that is set", () => {
    expect(withRange("/kpis/global", { from: "2026-08-01", to: null })).toBe(
      "/kpis/global?date_from=2026-08-01",
    );
    expect(withRange("/kpis/global", { from: null, to: "2026-08-31" })).toBe(
      "/kpis/global?date_to=2026-08-31",
    );
  });

  it("stays silent about the date field while it is the default", () => {
    // `creacion` is the server's default too, so spelling it out only adds
    // noise to a URL somebody has to read.
    const range = { from: "2026-08-01", to: "2026-08-31" };
    expect(withRange("/kpis/carriers?country=CO", range, "creacion")).toBe(
      "/kpis/carriers?country=CO&date_from=2026-08-01&date_to=2026-08-31",
    );
    expect(withRange("/kpis/carriers?country=CO", range)).toBe(
      withRange("/kpis/carriers?country=CO", range, "creacion"),
    );
  });

  it("sends the date field once it is not the default", () => {
    expect(
      withRange("/kpis/carriers?country=CO", { from: "2026-08-01", to: "2026-08-31" }, "entrega"),
    ).toBe(
      "/kpis/carriers?country=CO&date_from=2026-08-01&date_to=2026-08-31&date_field=entrega",
    );
    expect(withRange("/kpis/global", MAX_RANGE, "despacho")).toBe(
      "/kpis/global?date_field=despacho",
    );
  });
});

describe("formatRangeLabel", () => {
  const CHILE: FormatCountry = {
    currency_symbol: "$",
    currency_code: "CLP",
    decimal_places: 0,
    thousands_sep: ".",
    decimal_sep: ",",
    date_format: "dd-MM-yyyy",
    locale: "es-CL",
  };

  it("uses the country's own date format, never a hardcoded one", () => {
    expect(formatRangeLabel({ from: "2026-08-01", to: "2026-08-31" }, CHILE)).toBe(
      "01-08-2026 – 31-08-2026",
    );
  });

  it("shows a single day once, not twice", () => {
    expect(formatRangeLabel({ from: "2026-08-20", to: "2026-08-20" }, CHILE)).toBe(
      "20-08-2026",
    );
  });

  it("names an open end rather than inventing one", () => {
    expect(formatRangeLabel({ from: "2026-08-01", to: null }, CHILE)).toBe("Desde 01-08-2026");
    expect(formatRangeLabel({ from: null, to: "2026-08-31" }, CHILE)).toBe("Hasta 31-08-2026");
  });

  it("calls the empty range Máximo", () => {
    expect(formatRangeLabel(MAX_RANGE, CHILE)).toBe("Máximo");
  });
});

describe("unwrapRangePayload", () => {
  it("unwraps the KpiResponse envelope the API actually sends", () => {
    // `{rows, date_basis, date_from, date_to, excluded_no_date}` - see
    // api/schemas.py, KpiResponse.
    const payload = {
      rows: [{ bucket: "0-3", shipments: 12 }],
      date_basis: "creacion",
      date_from: "2026-08-01",
      date_to: "2026-08-31",
      excluded_no_date: 0,
    };
    expect(unwrapRangePayload(payload)).toEqual({
      data: payload.rows,
      dateBasis: "creacion",
      excludedNoDate: 0,
    });
  });

  it("reads every basis the API can report", () => {
    const all = ["creacion", "despacho", "entrega", "movimiento", "interaccion", "pauta"];
    for (const basis of all) {
      expect(unwrapRangePayload({ rows: [], date_basis: basis }).dateBasis).toBe(basis);
    }
  });

  it("keeps the basis on an EMPTY result, which is when it matters most", () => {
    // "No hay datos" and "no hay guías CREADAS en ese rango" are different
    // sentences, and only the second one tells the reader what to do next.
    expect(unwrapRangePayload({ rows: [], date_basis: "interaccion" })).toEqual({
      data: [],
      dateBasis: "interaccion",
      excludedNoDate: null,
    });
  });

  it("treats an explicit null basis as 'this endpoint does not filter'", () => {
    expect(unwrapRangePayload({ date_basis: null, rows: [] }).dateBasis).toBeNull();
  });

  it("keeps 'unknown' distinct from 'does not filter'", () => {
    // undefined means the server said nothing. Collapsing it into null would
    // print "histórico completo" on a card that is in fact filtered.
    expect(unwrapRangePayload([]).dateBasis).toBeUndefined();
    expect(unwrapRangePayload(null).dateBasis).toBeUndefined();
    expect(unwrapRangePayload({ rows: [], date_basis: "otra_cosa" }).dateBasis).toBeUndefined();
  });

  it("still handles a bare array, in case an endpoint is left unwrapped", () => {
    const rows = [{ bucket: "0-3" }];
    expect(unwrapRangePayload<typeof rows>(rows)).toEqual({
      data: rows,
      dateBasis: undefined,
      excludedNoDate: null,
    });
  });

  it("survives an object response that carries no basis", () => {
    const payload = { total: 12 };
    expect(unwrapRangePayload(payload)).toEqual({
      data: payload,
      dateBasis: undefined,
      excludedNoDate: null,
    });
  });
});

describe("unwrapRangePayload - excluded_no_date", () => {
  it("reads the count of guides the chosen date leaves out", () => {
    // 989 of Ecuador's 1.649 guides have no delivery date - migration 020.
    const payload = { rows: [], date_basis: "entrega", excluded_no_date: 989 };
    expect(unwrapRangePayload(payload).excludedNoDate).toBe(989);
  });

  it("keeps zero, which means 'nothing was left out', not 'unknown'", () => {
    // `creacion` excludes nobody, and saying 0 is different from saying nothing.
    expect(
      unwrapRangePayload({ rows: [], date_basis: "creacion", excluded_no_date: 0 })
        .excludedNoDate,
    ).toBe(0);
  });

  it("reports null when the server did not count", () => {
    expect(
      unwrapRangePayload({ rows: [], date_basis: "creacion", excluded_no_date: null })
        .excludedNoDate,
    ).toBeNull();
    expect(unwrapRangePayload({ rows: [], date_basis: "creacion" }).excludedNoDate).toBeNull();
  });

  it("refuses a count that is not a usable number", () => {
    for (const junk of ["989", -1, Number.NaN, {}]) {
      expect(
        unwrapRangePayload({ rows: [], date_basis: "entrega", excluded_no_date: junk })
          .excludedNoDate,
      ).toBeNull();
    }
  });
});
