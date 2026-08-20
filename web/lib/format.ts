/**
 * Formatting, driven entirely by `core.country`.
 *
 * There is no hardcoded "$" and no hardcoded "dd/MM/yyyy" anywhere in the UI.
 * Colombia writes 45.000 with no decimals; Mexico writes 1,234.56 with two;
 * Chile writes dates dd-MM-yyyy. All of that arrives from the API as data, and
 * this module is the only place that reads it.
 */

import type { Country } from "@/lib/types";

/** Minimal shape needed to format. Lets tests build one inline. */
export type FormatCountry = Pick<
  Country,
  | "currency_symbol"
  | "currency_code"
  | "decimal_places"
  | "thousands_sep"
  | "decimal_sep"
  | "date_format"
  | "locale"
>;

export const FALLBACK_COUNTRY: FormatCountry = {
  currency_symbol: "$",
  currency_code: "USD",
  decimal_places: 2,
  thousands_sep: ",",
  decimal_sep: ".",
  date_format: "dd/MM/yyyy",
  locale: "es",
};

function groupDigits(digits: string, separator: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
}

/**
 * A plain number with the country's separators.
 *
 * Deliberately not Intl.NumberFormat: the country row is the source of truth,
 * and the browser's idea of es-CO must not override what the workspace
 * configured.
 */
export function formatNumber(
  value: number | string | null | undefined,
  country: FormatCountry = FALLBACK_COUNTRY,
  decimals?: number,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";

  const places = decimals ?? country.decimal_places;
  const negative = numeric < 0;
  const fixed = Math.abs(numeric).toFixed(places);
  const [whole, fraction] = fixed.split(".");

  let out = groupDigits(whole, country.thousands_sep);
  if (fraction) out += country.decimal_sep + fraction;
  return negative ? `-${out}` : out;
}

/** Money, with the country's symbol. */
export function formatMoney(
  value: number | string | null | undefined,
  country: FormatCountry = FALLBACK_COUNTRY,
  options: { compact?: boolean; showCode?: boolean } = {},
): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";

  if (options.compact) {
    return `${numeric < 0 ? "-" : ""}${country.currency_symbol} ${formatCompact(
      Math.abs(numeric),
      country,
    )}`;
  }

  const body = formatNumber(numeric, country);
  const withSymbol = body.startsWith("-")
    ? `-${country.currency_symbol} ${body.slice(1)}`
    : `${country.currency_symbol} ${body}`;

  return options.showCode ? `${withSymbol} ${country.currency_code}` : withSymbol;
}

/** 1.2 M / 45,3 k - for axis labels and tight KPI cards. */
export function formatCompact(
  value: number | null | undefined,
  country: FormatCountry = FALLBACK_COUNTRY,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";

  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";

  if (abs >= 1_000_000_000) return `${sign}${formatNumber(abs / 1e9, country, 1)} MM`;
  if (abs >= 1_000_000) return `${sign}${formatNumber(abs / 1e6, country, 1)} M`;
  if (abs >= 1_000) return `${sign}${formatNumber(abs / 1e3, country, 1)} k`;
  return `${sign}${formatNumber(abs, country, 0)}`;
}

/** Percentages arrive from SQL already multiplied by 100. */
export function formatPercent(
  value: number | string | null | undefined,
  decimals = 1,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) return "—";
  return `${numeric.toFixed(decimals).replace(".", ",")}%`;
}

/** Signed delta, for the little arrow under a KPI. */
export function formatDelta(value: number | null | undefined, decimals = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(decimals).replace(".", ",")}`;
}

/** Applies the country's `date_format` pattern to an ISO date. */
export function formatDate(
  isoDate: string | Date | null | undefined,
  country: FormatCountry = FALLBACK_COUNTRY,
): string {
  if (!isoDate) return "—";

  const date = typeof isoDate === "string" ? parseIsoDate(isoDate) : isoDate;
  if (!date || Number.isNaN(date.getTime())) return "—";

  const dd = String(date.getDate()).padStart(2, "0");
  const MM = String(date.getMonth() + 1).padStart(2, "0");
  const yyyy = String(date.getFullYear());

  return country.date_format
    .replace("yyyy", yyyy)
    .replace("dd", dd)
    .replace("MM", MM)
    .replace("yy", yyyy.slice(2));
}

/** Short axis label: "12/08". */
export function formatDayShort(isoDate: string | null | undefined): string {
  const date = parseIsoDate(isoDate ?? "");
  if (!date) return "—";
  return `${String(date.getDate()).padStart(2, "0")}/${String(
    date.getMonth() + 1,
  ).padStart(2, "0")}`;
}

/**
 * Parse a date-only ISO string as LOCAL midnight.
 *
 * `new Date("2026-07-01")` is parsed as UTC, which renders as June 30th for
 * anyone west of Greenwich - i.e. every user of this product.
 */
export function parseIsoDate(value: string): Date | null {
  if (!value) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "hace 2 h", "hace 3 días" - for sync freshness. */
export function formatRelative(isoDateTime: string | null | undefined): string {
  if (!isoDateTime) return "nunca";
  const then = new Date(isoDateTime).getTime();
  if (Number.isNaN(then)) return "nunca";

  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "hace un momento";
  if (minutes < 60) return `hace ${minutes} min`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;

  const days = Math.floor(hours / 24);
  if (days === 1) return "hace 1 día";
  if (days < 30) return `hace ${days} días`;

  const months = Math.floor(days / 30);
  return months === 1 ? "hace 1 mes" : `hace ${months} meses`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Plural helper: pluralize(1, "guía", "guías") -> "1 guía". */
export function pluralize(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export const COUNTRY_FLAGS: Record<string, string> = {
  CO: "🇨🇴",
  MX: "🇲🇽",
  PE: "🇵🇪",
  EC: "🇪🇨",
  CL: "🇨🇱",
  PA: "🇵🇦",
  GT: "🇬🇹",
};

export function countryFlag(code: string): string {
  return COUNTRY_FLAGS[code?.toUpperCase()] ?? "🏳️";
}
