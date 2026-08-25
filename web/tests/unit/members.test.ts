import { describe, expect, it } from "vitest";

import {
  BUSINESS_MODELS,
  BUSINESS_MODEL_META,
  businessModelLabel,
  isBusinessModel,
  scopePayload,
  toggleCountry,
  visibleCountries,
} from "@/lib/members";

const COMPANY = ["GT", "HN", "CR", "EC", "DO"];

describe("business model", () => {
  it("knows the two sides of the business and nothing else", () => {
    expect([...BUSINESS_MODELS]).toEqual(["ecommerce", "proveeduria"]);
    for (const model of BUSINESS_MODELS) {
      expect(BUSINESS_MODEL_META[model].label).toBeTruthy();
      expect(BUSINESS_MODEL_META[model].detail).toBeTruthy();
    }
    expect(isBusinessModel("ecommerce")).toBe(true);
    expect(isBusinessModel("retail")).toBe(false);
  });

  it("says 'sin modelo' when the admin has not answered", () => {
    expect(businessModelLabel(null)).toBe("Sin modelo");
    expect(businessModelLabel(undefined)).toBe("Sin modelo");
    expect(businessModelLabel("proveeduria")).toBe("Proveeduría");
  });
});

describe("toggleCountry", () => {
  it("adds a country on the first click and removes it on the second", () => {
    const once = toggleCountry([], "HN", COMPANY);
    expect(once).toEqual(["HN"]);
    expect(toggleCountry(once, "HN", COMPANY)).toEqual([]);
  });

  it("keeps the company's order, not the click order", () => {
    const selected = toggleCountry(toggleCountry([], "CR", COMPANY), "GT", COMPANY);
    expect(selected).toEqual(["GT", "CR"]);
  });

  it("is case-insensitive and ignores a country the company does not operate", () => {
    expect(toggleCountry(["gt"], "hn", COMPANY)).toEqual(["GT", "HN"]);
    expect(toggleCountry(["GT"], "XX", COMPANY)).toEqual(["GT"]);
  });
});

describe("scopePayload", () => {
  it("sends the chosen countries", () => {
    expect(scopePayload(["GT", "HN"], COMPANY)).toEqual({ country_scope: ["GT", "HN"] });
  });

  it("clears the scope instead of sending an empty list", () => {
    expect(scopePayload([], COMPANY)).toEqual({ clear_country_scope: true });
  });

  it("treats 'every country' as no scope at all", () => {
    expect(scopePayload(["DO", "GT", "HN", "CR", "EC"], COMPANY)).toEqual({
      clear_country_scope: true,
    });
  });
});

describe("visibleCountries", () => {
  it("shows every company country when the member has no scope", () => {
    expect(visibleCountries(null, COMPANY)).toEqual(COMPANY);
  });

  it("shows only the scoped ones, in company order", () => {
    expect(visibleCountries(["CR", "GT"], COMPANY)).toEqual(["GT", "CR"]);
  });
});
