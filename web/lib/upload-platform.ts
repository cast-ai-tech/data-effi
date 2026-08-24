/**
 * Which platform a file belongs to, decided BEFORE it is uploaded.
 *
 * The rule the per-country upload screen follows (migration 042): the operator
 * says which platform's export this is; if Data Effi recognises the report
 * shape and it belongs to a different platform, the upload is refused here,
 * not discovered on the dashboard weeks later as Effi's guides counted under
 * Dropi. Pure functions, so the rule is testable without a browser.
 */

import type { DetectResult, Platform } from "@/lib/types";

/** Platforms whose files carry guides. Ads and CS sheets never do. */
const GUIDE_CATEGORIES = new Set(["fulfillment", "tienda", "archivos", "otros"]);

/**
 * The platforms a country can upload guides for, from the catalogue the API
 * already filters by country. Planned integrations are listed by the
 * connections screen so the operator knows they are coming; here they would
 * only be a button that fails, so they are left out.
 */
export function guidePlatforms<T extends Platform>(platforms: readonly T[] | null | undefined): T[] {
  return platformsForKind(platforms, "shipments");
}

/** What each report type is called in the catalogue's `data_domains`. */
const DOMAIN_OF_KIND: Record<string, string> = {
  shipments: "shipments",
  movements: "movements",
  ads: "ads",
  cs: "cs",
};

/** Ways a platform's data can arrive as a FILE someone uploads. */
const FILE_AUTH_TYPES = new Set(["file", "session"]);

/**
 * The platforms whose export of THIS kind of report can be uploaded here.
 *
 * Guides and money: Effi, Dropi, the manual upload. Ad spend: the manual ad
 * sheet - Meta, TikTok and Google connect by OAuth and are not a file. CS: the
 * confirmation sheet. Effi is tier 3 with a session login, but its export is a
 * file too, which is exactly the case migration 042 exists for.
 */
export function platformsForKind<T extends Platform>(
  platforms: readonly T[] | null | undefined,
  kind: string,
): T[] {
  const domain = DOMAIN_OF_KIND[kind];
  if (!domain) return [];
  return (platforms ?? []).filter(
    (platform) =>
      platform.availability !== "planned" &&
      FILE_AUTH_TYPES.has(platform.auth_type) &&
      platform.data_domains.includes(domain) &&
      (domain !== "shipments" || GUIDE_CATEGORIES.has(platform.category)),
  );
}

export type FileVerdict =
  | { kind: "ok"; platform: string }
  | { kind: "suggest"; platform: string; label: string }
  | { kind: "mismatch"; chosen: string; detected: string; label: string };

/**
 * Compare what the operator chose with what the file says about itself.
 *
 *   - nothing chosen, file recognised  -> suggest the file's platform
 *   - chosen equals detected           -> ok
 *   - chosen differs from detected     -> mismatch (the upload will refuse it)
 *   - file not recognised              -> ok with whatever was chosen; a generic
 *                                         CSV says nothing about where it came
 *                                         from, and that is what manual_xlsx
 *                                         is for
 */
export function judgeFile(chosen: string | null, detected: DetectResult | null): FileVerdict | null {
  const detectedPlatform = detected?.detected_platform_code ?? null;
  const label = detected?.detected_platform_name ?? detected?.profile_label ?? detectedPlatform ?? "";

  if (!chosen) {
    return detectedPlatform ? { kind: "suggest", platform: detectedPlatform, label } : null;
  }
  if (detectedPlatform && detectedPlatform !== chosen) {
    return { kind: "mismatch", chosen, detected: detectedPlatform, label };
  }
  return { kind: "ok", platform: chosen };
}

/** "Effi (fulfillment COD)" is a catalogue name; the button says "Effi". */
export function shortPlatformName(name: string): string {
  return name.replace(/\s*\(.*\)\s*$/, "").trim() || name;
}
