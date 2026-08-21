import { describe, expect, it } from "vitest";

import { pickRedirectCountry } from "@/lib/country";
import type { Country } from "@/lib/types";

function makeCountry(code: string, name: string): Country {
  return {
    code,
    name,
    currency_code: code === "EC" ? "USD" : "COP",
    currency_symbol: "$",
    decimal_places: code === "EC" ? 2 : 0,
    thousands_sep: code === "EC" ? "," : ".",
    decimal_sep: code === "EC" ? "." : ",",
    date_format: "dd/MM/yyyy",
    locale: "es",
    timezone: "America/Bogota",
    geo_level1_label: "Provincia",
    is_active: true,
    maturation_days: 21,
    maturation_days_suggested: 21,
  };
}

const COLOMBIA = makeCountry("CO", "Colombia");
const ECUADOR = makeCountry("EC", "Ecuador");

/**
 * Where `/orders` sends someone now that orders live under a country.
 *
 * These URLs are in open tabs and pasted messages, so the redirect has to land
 * somewhere defensible rather than somewhere arbitrary.
 */
describe("pickRedirectCountry", () => {
  it("opens the country the operator last worked in", () => {
    expect(pickRedirectCountry([COLOMBIA, ECUADOR], "EC")).toBe(ECUADOR);
  });

  it("falls back to the first active country when nothing is remembered", () => {
    expect(pickRedirectCountry([COLOMBIA, ECUADOR], null)).toBe(COLOMBIA);
  });

  /**
   * The remembered country can have been deactivated since the last visit.
   * Honouring it would forward the reader straight to "PE no está activo",
   * which is a worse answer than the country they do have.
   */
  it("ignores a remembered country that is no longer in the list", () => {
    expect(pickRedirectCountry([COLOMBIA, ECUADOR], "PE")).toBe(COLOMBIA);
  });

  it("returns null when the workspace has no active country", () => {
    expect(pickRedirectCountry([], "EC")).toBeNull();
  });
});
