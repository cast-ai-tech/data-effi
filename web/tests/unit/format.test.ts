import { afterEach, describe, expect, it, vi } from "vitest";

import {
  countryFlag,
  formatBytes,
  formatCompact,
  formatDate,
  formatDayShort,
  formatDelta,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRelative,
  parseIsoDate,
  pluralize,
  type FormatCountry,
} from "@/lib/format";

/**
 * The whole point of lib/format.ts is that the country row - not the browser
 * locale and not a hardcoded "$" - decides how a number reads. These fixtures
 * are the three shapes that actually differ in production.
 */
const CO: FormatCountry = {
  currency_symbol: "$",
  currency_code: "COP",
  decimal_places: 0,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-CO",
};

const MX: FormatCountry = {
  currency_symbol: "$",
  currency_code: "MXN",
  decimal_places: 2,
  thousands_sep: ",",
  decimal_sep: ".",
  date_format: "dd/MM/yyyy",
  locale: "es-MX",
};

const CL: FormatCountry = {
  currency_symbol: "$",
  currency_code: "CLP",
  decimal_places: 0,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd-MM-yyyy",
  locale: "es-CL",
};

/** The em dash the UI shows instead of a missing figure. */
const EM_DASH = "—";

describe("formatNumber", () => {
  it("groups thousands with the country's separator", () => {
    expect(formatNumber(45000, CO)).toBe("45.000");
    expect(formatNumber(45000, MX)).toBe("45,000.00");
  });

  it("respects the country's decimal places and separator", () => {
    expect(formatNumber(1234.56, CO)).toBe("1.235");
    expect(formatNumber(1234.56, MX)).toBe("1,234.56");
  });

  it("honours an explicit decimals override", () => {
    expect(formatNumber(1234.56, CO, 2)).toBe("1.234,56");
    expect(formatNumber(1234.56, MX, 0)).toBe("1,235");
  });

  it("keeps the minus sign in front", () => {
    expect(formatNumber(-45000, CO)).toBe("-45.000");
  });

  it("accepts numeric strings, the way the API sends decimals", () => {
    expect(formatNumber("45000", CO)).toBe("45.000");
    expect(formatNumber("1234.56", MX)).toBe("1,234.56");
  });

  it("groups long numbers in threes", () => {
    expect(formatNumber(1234567890, CO)).toBe("1.234.567.890");
  });

  it("renders an em dash for every kind of missing value", () => {
    expect(formatNumber(null, CO)).toBe(EM_DASH);
    expect(formatNumber(undefined, CO)).toBe(EM_DASH);
    expect(formatNumber("", CO)).toBe(EM_DASH);
    expect(formatNumber(Number.NaN, CO)).toBe(EM_DASH);
    expect(formatNumber("no-soy-un-numero", CO)).toBe(EM_DASH);
    expect(formatNumber(Number.POSITIVE_INFINITY, CO)).toBe(EM_DASH);
  });

  it("never leaks the string NaN or null into the UI", () => {
    for (const value of [null, undefined, "", Number.NaN] as const) {
      const rendered = formatNumber(value, CO);
      expect(rendered).not.toContain("NaN");
      expect(rendered).not.toContain("null");
      expect(rendered).not.toContain("undefined");
    }
  });

  it("formats zero as a real zero, not as missing", () => {
    expect(formatNumber(0, CO)).toBe("0");
    expect(formatNumber(0, MX)).toBe("0.00");
  });
});

describe("formatMoney", () => {
  it("puts the country's symbol in front of a Colombian figure", () => {
    expect(formatMoney(45000, CO)).toBe("$ 45.000");
  });

  it("uses two decimals and a dot separator for Mexico", () => {
    expect(formatMoney(1234.56, MX)).toBe("$ 1,234.56");
  });

  it("keeps the minus BEFORE the symbol", () => {
    expect(formatMoney(-1000, CO)).toBe("-$ 1.000");
    expect(formatMoney(-1234.56, MX)).toBe("-$ 1,234.56");
  });

  it("appends the currency code on request", () => {
    expect(formatMoney(45000, CO, { showCode: true })).toBe("$ 45.000 COP");
  });

  it("compacts when asked, keeping the sign in front of the symbol", () => {
    expect(formatMoney(1_200_000, CO, { compact: true })).toBe("$ 1,2 M");
    expect(formatMoney(-1_200_000, CO, { compact: true })).toBe("-$ 1,2 M");
  });

  it("renders an em dash for missing values instead of '$ NaN'", () => {
    expect(formatMoney(null, CO)).toBe(EM_DASH);
    expect(formatMoney(undefined, CO)).toBe(EM_DASH);
    expect(formatMoney("", CO)).toBe(EM_DASH);
    expect(formatMoney(Number.NaN, CO)).toBe(EM_DASH);
  });
});

describe("formatPercent", () => {
  it("uses a comma as the decimal separator", () => {
    expect(formatPercent(87.5)).toBe("87,5%");
    expect(formatPercent(12.34)).toBe("12,3%");
  });

  it("honours the decimals argument", () => {
    expect(formatPercent(12.345, 2)).toBe("12,35%");
    expect(formatPercent(12.345, 0)).toBe("12%");
  });

  it("accepts numeric strings", () => {
    expect(formatPercent("87.5")).toBe("87,5%");
  });

  it("renders an em dash for missing values", () => {
    expect(formatPercent(null)).toBe(EM_DASH);
    expect(formatPercent(undefined)).toBe(EM_DASH);
    expect(formatPercent("")).toBe(EM_DASH);
    expect(formatPercent(Number.NaN)).toBe(EM_DASH);
  });
});

describe("formatDelta", () => {
  it("signs a positive delta and uses a comma decimal", () => {
    expect(formatDelta(3.2)).toBe("+3,2");
  });

  it("leaves the native minus on a negative delta", () => {
    expect(formatDelta(-3.2)).toBe("-3,2");
  });

  it("renders an em dash for missing values", () => {
    expect(formatDelta(null)).toBe(EM_DASH);
    expect(formatDelta(undefined)).toBe(EM_DASH);
    expect(formatDelta(Number.NaN)).toBe(EM_DASH);
  });
});

describe("formatCompact", () => {
  it("abbreviates thousands", () => {
    expect(formatCompact(45_300, CO)).toBe("45,3 k");
    expect(formatCompact(45_300, MX)).toBe("45.3 k");
  });

  it("abbreviates millions and billions", () => {
    expect(formatCompact(1_200_000, CO)).toBe("1,2 M");
    expect(formatCompact(2_500_000_000, CO)).toBe("2,5 MM");
  });

  it("leaves small numbers whole and without decimals", () => {
    expect(formatCompact(999, CO)).toBe("999");
    expect(formatCompact(0, CO)).toBe("0");
  });

  it("keeps the sign on negatives", () => {
    expect(formatCompact(-45_300, CO)).toBe("-45,3 k");
  });

  it("renders an em dash for missing values", () => {
    expect(formatCompact(null, CO)).toBe(EM_DASH);
    expect(formatCompact(undefined, CO)).toBe(EM_DASH);
    expect(formatCompact(Number.NaN, CO)).toBe(EM_DASH);
  });
});

describe("formatDate", () => {
  it("applies the country's date_format to the SAME ISO date", () => {
    expect(formatDate("2026-07-01", CO)).toBe("01/07/2026");
    expect(formatDate("2026-07-01", CL)).toBe("01-07-2026");
  });

  it("zero-pads single-digit days and months", () => {
    expect(formatDate("2026-01-05", CO)).toBe("05/01/2026");
  });

  it("accepts a Date object", () => {
    expect(formatDate(new Date(2026, 6, 1), CO)).toBe("01/07/2026");
  });

  it("renders an em dash for missing or unparseable dates", () => {
    expect(formatDate(null, CO)).toBe(EM_DASH);
    expect(formatDate(undefined, CO)).toBe(EM_DASH);
    expect(formatDate("", CO)).toBe(EM_DASH);
    expect(formatDate("no es una fecha", CO)).toBe(EM_DASH);
  });
});

describe("parseIsoDate", () => {
  /**
   * `new Date("2026-07-01")` is parsed as UTC midnight, which reads as June
   * 30th for anyone west of Greenwich - i.e. every user of this product. The
   * date-only branch must build a LOCAL date instead.
   */
  it("treats a date-only string as LOCAL midnight, not UTC", () => {
    const date = parseIsoDate("2026-07-01");
    expect(date).not.toBeNull();
    expect(date?.getDate()).toBe(1);
    expect(date?.getMonth()).toBe(6);
    expect(date?.getFullYear()).toBe(2026);
    expect(date?.getHours()).toBe(0);
  });

  it("does not drift on the first day of the year", () => {
    const date = parseIsoDate("2026-01-01");
    expect(date?.getDate()).toBe(1);
    expect(date?.getMonth()).toBe(0);
    expect(date?.getFullYear()).toBe(2026);
  });

  it("still parses a full timestamp", () => {
    const date = parseIsoDate("2026-07-01T15:30:00Z");
    expect(date?.getTime()).toBe(Date.UTC(2026, 6, 1, 15, 30, 0));
  });

  it("returns null for empty or invalid input", () => {
    expect(parseIsoDate("")).toBeNull();
    expect(parseIsoDate("no es una fecha")).toBeNull();
  });
});

describe("formatDayShort", () => {
  it("renders a dd/MM axis label from a local-parsed date", () => {
    expect(formatDayShort("2026-08-12")).toBe("12/08");
    expect(formatDayShort("2026-01-05")).toBe("05/01");
  });

  it("renders an em dash for missing values", () => {
    expect(formatDayShort(null)).toBe(EM_DASH);
    expect(formatDayShort("")).toBe(EM_DASH);
  });
});

describe("formatRelative", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function atNow(offsetMs: number): string {
    return new Date(Date.now() - offsetMs).toISOString();
  }

  it("describes minutes, hours, days and months in Spanish", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 7, 20, 12, 0, 0));

    expect(formatRelative(atNow(10_000))).toBe("hace un momento");
    expect(formatRelative(atNow(5 * 60_000))).toBe("hace 5 min");
    expect(formatRelative(atNow(59 * 60_000))).toBe("hace 59 min");
    expect(formatRelative(atNow(3 * 3_600_000))).toBe("hace 3 h");
    expect(formatRelative(atNow(23 * 3_600_000))).toBe("hace 23 h");
    expect(formatRelative(atNow(24 * 3_600_000))).toBe("hace 1 día");
    expect(formatRelative(atNow(5 * 24 * 3_600_000))).toBe("hace 5 días");
    expect(formatRelative(atNow(45 * 24 * 3_600_000))).toBe("hace 1 mes");
    expect(formatRelative(atNow(120 * 24 * 3_600_000))).toBe("hace 4 meses");
  });

  it("says 'nunca' when there is no timestamp at all", () => {
    expect(formatRelative(null)).toBe("nunca");
    expect(formatRelative(undefined)).toBe("nunca");
    expect(formatRelative("")).toBe("nunca");
    expect(formatRelative("no es una fecha")).toBe("nunca");
  });
});

describe("formatBytes", () => {
  it("scales from bytes to megabytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("pluralize", () => {
  it("picks the singular only for exactly one", () => {
    expect(pluralize(1, "guía", "guías")).toBe("1 guía");
    expect(pluralize(0, "guía", "guías")).toBe("0 guías");
    expect(pluralize(2, "guía", "guías")).toBe("2 guías");
  });
});

describe("countryFlag", () => {
  it("returns the flag for a known country code", () => {
    expect(countryFlag("CO")).toBe("\u{1F1E8}\u{1F1F4}");
    expect(countryFlag("MX")).toBe("\u{1F1F2}\u{1F1FD}");
  });

  it("is case insensitive", () => {
    expect(countryFlag("cl")).toBe(countryFlag("CL"));
  });

  it("falls back to a neutral flag for an unknown code", () => {
    // White flag + variation selector, the neutral placeholder.
    expect(countryFlag("ZZ")).toBe("\u{1F3F3}️");
    expect(countryFlag("")).toBe("\u{1F3F3}️");
  });
});
