import { describe, expect, it } from "vitest";

import {
  guidePlatforms,
  judgeFile,
  platformsForKind,
  shortPlatformName,
} from "@/lib/upload-platform";
import type { DetectResult, Platform } from "@/lib/types";

/**
 * The rule the per-country upload screen follows: a recognised report goes to
 * its own platform, or nowhere. Pure, so it is tested without a browser.
 */
function platform(overrides: Partial<Platform>): Platform {
  return {
    platform_code: "effi",
    platform_name: "Effi (fulfillment COD)",
    tier: 3,
    scope: "country",
    category: "fulfillment",
    auth_type: "session",
    availability: "available",
    direction: "in",
    data_domains: ["shipments", "movements"],
    requires_consent: true,
    setup_hint: null,
    docs_url: null,
    available_countries: ["EC"],
    ...overrides,
  };
}

function detected(code: string | null, name: string | null = null): DetectResult {
  return {
    filename: "x.xlsx",
    format: "xlsx",
    profile_code: code ? `${code}_guias` : null,
    profile_label: code ? `${code} · guías` : null,
    detected_platform_code: code,
    detected_platform_name: name,
    detected_country_code: null,
    detected_country_raw: null,
    row_count: 4,
    column_count: 40,
    mapped_columns: {},
    unmapped_columns: [],
  };
}

describe("guidePlatforms", () => {
  it("keeps the platforms whose files carry guides and drops ads, CS and planned ones", () => {
    const list = guidePlatforms([
      platform({ platform_code: "effi" }),
      platform({ platform_code: "dropi", category: "fulfillment", tier: 2 }),
      platform({ platform_code: "manual_xlsx", category: "archivos", scope: "global" }),
      platform({ platform_code: "meta_ads", category: "pauta", data_domains: ["ads"] }),
      platform({ platform_code: "cs_sheet", category: "otros", data_domains: ["cs"] }),
      platform({ platform_code: "hoko", availability: "planned" }),
    ]);
    expect(list.map((p) => p.platform_code)).toEqual(["effi", "dropi", "manual_xlsx"]);
  });
});

describe("platformsForKind", () => {
  const catalogue = [
    platform({ platform_code: "effi", auth_type: "session" }),
    platform({ platform_code: "dropi", tier: 2, auth_type: "file" }),
    platform({ platform_code: "manual_xlsx", category: "archivos", scope: "global", auth_type: "file" }),
    platform({ platform_code: "ads_manual", category: "pauta", auth_type: "file", data_domains: ["ads"] }),
    platform({ platform_code: "meta_ads", category: "pauta", auth_type: "oauth2", data_domains: ["ads"], availability: "planned" }),
    platform({ platform_code: "cs_sheet", category: "archivos", auth_type: "file", data_domains: ["cs"], scope: "global" }),
    platform({ platform_code: "webhook_generic", category: "automatizacion", auth_type: "webhook", data_domains: ["shipments", "ads", "cs"], scope: "global" }),
  ];

  it("offers Effi, Dropi and the manual upload for guides - Effi's export is a file too", () => {
    expect(platformsForKind(catalogue, "shipments").map((p) => p.platform_code)).toEqual([
      "effi",
      "dropi",
      "manual_xlsx",
    ]);
  });

  it("offers only the manual ad sheet for ad spend: OAuth platforms are not a file", () => {
    expect(platformsForKind(catalogue, "ads").map((p) => p.platform_code)).toEqual(["ads_manual"]);
  });

  it("offers the CS sheet for confirmations and never a webhook", () => {
    expect(platformsForKind(catalogue, "cs").map((p) => p.platform_code)).toEqual(["cs_sheet"]);
  });

  it("offers nothing for a report type it does not know", () => {
    expect(platformsForKind(catalogue, "inventario")).toEqual([]);
  });
});

describe("judgeFile", () => {
  it("suggests the file's platform when nothing was chosen", () => {
    expect(judgeFile(null, detected("effi", "Effi (fulfillment COD)"))).toEqual({
      kind: "suggest",
      platform: "effi",
      label: "Effi (fulfillment COD)",
    });
  });

  it("says nothing when nothing was chosen and the file is not recognised", () => {
    expect(judgeFile(null, detected(null))).toBeNull();
    expect(judgeFile(null, null)).toBeNull();
  });

  it("refuses an Effi export chosen as Dropi, naming both", () => {
    expect(judgeFile("dropi", detected("effi", "Effi (fulfillment COD)"))).toEqual({
      kind: "mismatch",
      chosen: "dropi",
      detected: "effi",
      label: "Effi (fulfillment COD)",
    });
  });

  it("accepts the chosen platform when the file agrees or says nothing", () => {
    expect(judgeFile("effi", detected("effi"))).toEqual({ kind: "ok", platform: "effi" });
    expect(judgeFile("manual_xlsx", detected(null))).toEqual({ kind: "ok", platform: "manual_xlsx" });
    expect(judgeFile("dropi", null)).toEqual({ kind: "ok", platform: "dropi" });
  });
});

describe("shortPlatformName", () => {
  it("drops the catalogue's parenthetical", () => {
    expect(shortPlatformName("Effi (fulfillment COD)")).toBe("Effi");
    expect(shortPlatformName("Carga manual Excel/CSV")).toBe("Carga manual Excel/CSV");
  });
});
