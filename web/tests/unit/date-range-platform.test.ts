import { describe, expect, it } from "vitest";

import { MAX_RANGE, readPlatform, unwrapRangePayload, withRange } from "@/lib/date-range";

/**
 * The platform travels with the range (migrations 040/041): in the URL the
 * reader shares, in the query every widget sends, and back on the envelope
 * saying whether it was honoured.
 */
describe("withRange with a platform", () => {
  it("appends `platform` next to the range", () => {
    expect(withRange("/kpis/carriers?country=EC", { from: "2026-08-01", to: "2026-08-14" }, "creacion", "dropi")).toBe(
      "/kpis/carriers?country=EC&date_from=2026-08-01&date_to=2026-08-14&platform=dropi",
    );
  });

  it("says nothing when no platform is chosen: absent means todas, server-side too", () => {
    expect(withRange("/kpis/carriers?country=EC", MAX_RANGE, "creacion", null)).toBe(
      "/kpis/carriers?country=EC",
    );
  });
});

describe("readPlatform", () => {
  it("accepts catalogue codes and refuses anything else", () => {
    expect(readPlatform("effi")).toBe("effi");
    expect(readPlatform("manual_xlsx")).toBe("manual_xlsx");
    expect(readPlatform("Effi")).toBeNull();
    expect(readPlatform("dropi; drop table")).toBeNull();
    expect(readPlatform("")).toBeNull();
    expect(readPlatform(null)).toBeNull();
  });
});

describe("unwrapRangePayload reads what the server applied", () => {
  const base = { rows: [{ shipments: 3 }], date_basis: "creacion" };

  it("reports the platform the server narrowed to", () => {
    expect(unwrapRangePayload({ ...base, platform: "effi" }).platformApplied).toBe("effi");
  });

  it("keeps null as null: this endpoint mixed every platform and said so", () => {
    expect(unwrapRangePayload({ ...base, platform: null }).platformApplied).toBeNull();
  });

  it("distinguishes 'did not say' from 'todas'", () => {
    expect(unwrapRangePayload(base).platformApplied).toBeUndefined();
  });

  it("still hands the rows through", () => {
    expect(unwrapRangePayload<{ shipments: number }[]>({ ...base, platform: "dropi" }).data).toEqual([
      { shipments: 3 },
    ]);
  });
});
