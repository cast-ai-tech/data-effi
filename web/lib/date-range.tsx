"use client";

/**
 * The date range the whole dashboard reads from.
 *
 * ONE range, in the URL, for every tab. Not per widget and not per page: an
 * operator who narrows to last week and then opens Logística is still looking
 * at last week, and the link they paste into WhatsApp shows their colleague
 * exactly the same screen. A dashboard whose filter cannot be linked forces
 * people to describe the filter in prose, and prose is where the mistake
 * happens.
 *
 * The URL is the single source of truth. There is no second copy of the range
 * in React state to drift out of sync with it.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import { FALLBACK_COUNTRY, formatDate, type FormatCountry } from "@/lib/format";
import { useApi, type AsyncState } from "@/lib/hooks";

// ---------------------------------------------------------------------------
// The range itself
// ---------------------------------------------------------------------------

/** Both ends `null` means "todo el histórico": send no parameters at all. */
export interface DateRange {
  readonly from: string | null;
  readonly to: string | null;
}

export const MAX_RANGE: DateRange = { from: null, to: null };

/**
 * The shortcuts, in the order they are offered.
 *
 * Order is load-bearing for `inferMode`: on the first day of a month "Hoy" and
 * "Este mes" resolve to the same pair of dates, and "Hoy" is the one a reader
 * would name, so it has to come first.
 */
export const PRESET_KEYS = [
  "hoy",
  "ayer",
  "7d",
  "14d",
  "28d",
  "30d",
  "este_mes",
  "mes_pasado",
  "maximo",
] as const;

export type PresetKey = (typeof PRESET_KEYS)[number];

/** A range that matches no shortcut was picked by hand on the calendar. */
export type RangeMode = PresetKey | "personalizado";

export const PRESET_LABELS: Record<PresetKey, string> = {
  hoy: "Hoy",
  ayer: "Ayer",
  "7d": "Últimos 7 días",
  "14d": "Últimos 14 días",
  "28d": "Últimos 28 días",
  "30d": "Últimos 30 días",
  este_mes: "Este mes",
  mes_pasado: "Mes pasado",
  maximo: "Máximo",
};

// ---------------------------------------------------------------------------
// Date arithmetic
//
// All of it on local dates. `new Date("2026-07-01")` parses as UTC and renders
// as June 30th for everyone west of Greenwich - which is every user of this
// product - so an ISO string is never handed to the Date constructor here.
// ---------------------------------------------------------------------------

/** `YYYY-MM-DD` for a local date. */
export function toIso(date: Date): string {
  return (
    `${date.getFullYear()}-` +
    `${String(date.getMonth() + 1).padStart(2, "0")}-` +
    `${String(date.getDate()).padStart(2, "0")}`
  );
}

/** A local `Date` at midnight from `YYYY-MM-DD`, or `null` if it is not one. */
export function fromIso(value: string | null | undefined): Date | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

export function addDays(date: Date, days: number): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

/** Day 0 of the next month is the last day of this one, leap years included. */
export function endOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0);
}

/**
 * Turn a shortcut into two dates.
 *
 * "Últimos N días" ENDS YESTERDAY, as it does in the ads manager this selector
 * is modelled on. Today is a partial day - guides are still being created while
 * you read - so folding it into a 7-day window quietly drags every average
 * down. That is also what keeps "Hoy" from being a redundant tail of "Últimos
 * 7 días".
 */
export function resolvePreset(preset: PresetKey, today: Date = new Date()): DateRange {
  switch (preset) {
    case "hoy":
      return { from: toIso(today), to: toIso(today) };

    case "ayer": {
      const yesterday = addDays(today, -1);
      return { from: toIso(yesterday), to: toIso(yesterday) };
    }

    case "7d":
    case "14d":
    case "28d":
    case "30d": {
      const days = Number(preset.slice(0, -1));
      return { from: toIso(addDays(today, -days)), to: toIso(addDays(today, -1)) };
    }

    case "este_mes":
      return { from: toIso(startOfMonth(today)), to: toIso(today) };

    case "mes_pasado": {
      // Month -1 rolls the year back on its own: January's previous month is
      // December of the year before, no special case needed.
      const previous = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      return { from: toIso(startOfMonth(previous)), to: toIso(endOfMonth(previous)) };
    }

    case "maximo":
      return MAX_RANGE;
  }
}

/**
 * Which shortcut a range corresponds to, so a shared link arrives with the
 * right button lit instead of always reading "Personalizado".
 *
 * `today` is nullable because the provider does not know the reader's date
 * until after hydration; without it every bounded range is simply custom.
 */
export function inferMode(range: DateRange, today: Date | null): RangeMode {
  if (!range.from && !range.to) return "maximo";
  if (!today) return "personalizado";

  for (const key of PRESET_KEYS) {
    if (key === "maximo") continue;
    const candidate = resolvePreset(key, today);
    if (candidate.from === range.from && candidate.to === range.to) return key;
  }
  return "personalizado";
}

/** An inverted range is the one mistake the calendar cannot always prevent. */
export function isValidRange(range: DateRange): boolean {
  if (!range.from || !range.to) return true;
  return range.from <= range.to;
}

export const INVERTED_RANGE_MESSAGE =
  "La fecha final no puede ser anterior a la inicial.";

/** The label on the button, in the active country's date format. */
export function formatRangeLabel(
  range: DateRange,
  country: FormatCountry = FALLBACK_COUNTRY,
): string {
  if (!range.from && !range.to) return PRESET_LABELS.maximo;
  if (range.from && range.to) {
    return range.from === range.to
      ? formatDate(range.from, country)
      : `${formatDate(range.from, country)} – ${formatDate(range.to, country)}`;
  }
  if (range.from) return `Desde ${formatDate(range.from, country)}`;
  return `Hasta ${formatDate(range.to, country)}`;
}

/**
 * Append the range to an API path.
 *
 * The one place the wire names live. Twenty widgets each writing
 * `if (dateFrom) params.set("date_from", dateFrom)` is twenty chances to spell
 * it `from` in one of them and never notice.
 */
export function withRange(
  path: string,
  range: DateRange,
  field: DateField = DEFAULT_FIELD,
  platform: string | null = null,
): string {
  const params = new URLSearchParams();
  if (range.from) params.set("date_from", range.from);
  if (range.to) params.set("date_to", range.to);
  // The default is the server's default too, so saying it adds nothing to the
  // URL a reader has to scan.
  if (field !== DEFAULT_FIELD) params.set("date_field", field);
  // Migrations 040/041. Absent means "todas", which is also the server's default.
  if (platform) params.set("platform", platform);

  const query = params.toString();
  return query ? `${path}${path.includes("?") ? "&" : "?"}${query}` : path;
}

// ---------------------------------------------------------------------------
// What the server says it filtered on
// ---------------------------------------------------------------------------

/**
 * Which date column the API filtered on.
 *
 * A COD guide has several dates and they answer different questions, so this is
 * NOT one global fact about the dashboard - it is a property of each endpoint,
 * and two cards side by side can be filtered on different clocks.
 *
 * `null` is the important one: that endpoint ignores the range entirely.
 * `undefined` is a fourth state and not the same as `null` - it means the server
 * said nothing, so we do not know either way and must not claim we do.
 */
export type FilteredBasis =
  | "creacion"
  | "despacho"
  | "entrega"
  | "movimiento"
  | "interaccion"
  | "pauta";
export type DateBasis = FilteredBasis | null;

/**
 * The three the caller may ASK for.
 *
 * A strict subset of the bases above: `interaccion` and `pauta` belong to other
 * sources and are never a choice - the endpoints that use them have no other
 * option, and offering them in the picker would promise something it cannot do.
 */
export const DATE_FIELDS = ["creacion", "despacho", "entrega"] as const;
export type DateField = (typeof DATE_FIELDS)[number];

export const DEFAULT_FIELD: DateField = "creacion";

/** Short, for the segmented control in the header. */
export const FIELD_LABELS: Record<DateField, string> = {
  creacion: "Creación",
  despacho: "Despacho",
  entrega: "Entrega",
};

/** What each choice actually answers, for the tooltip. */
export const FIELD_QUESTIONS: Record<DateField, string> = {
  creacion:
    "¿Cuánto vendí en este rango? Todas las guías tienen fecha de creación, " +
    "así que ninguna queda fuera.",
  despacho:
    "¿Cuánto despaché en este rango? Deja fuera las guías que aún no salieron " +
    "en una relación de despacho.",
  entrega:
    "¿Cuánto entregué en este rango? Deja fuera toda guía sin entregar: las " +
    "que van en tránsito, en novedad o devueltas.",
};

/** Reads as "Rango sobre {label}." on the card. */
export const BASIS_LABELS: Record<FilteredBasis, string> = {
  creacion: "la fecha de creación de la guía",
  despacho: "la fecha de despacho",
  entrega: "la fecha de entrega",
  movimiento: "la fecha del movimiento de dinero",
  interaccion: "la fecha de la gestión de servicio al cliente",
  pauta: "la fecha del gasto en pauta",
};

/**
 * The caveat each basis carries, for the card's tooltip.
 *
 * Only the ones with a real trap have one; the rest say what they mean.
 */
export const BASIS_CAVEATS: Partial<Record<FilteredBasis, string>> = {
  creacion:
    "Es la única fecha que toda guía tiene, así que ninguna queda fuera del " +
    "conteo por no tenerla.",
  entrega:
    "Una guía sin entregar no tiene fecha de entrega, así que este filtro deja " +
    "fuera justo las guías abiertas que hay que vigilar.",
  despacho:
    "Una guía que todavía no salió en una relación de despacho no tiene esta " +
    "fecha, así que queda fuera del conteo.",
  interaccion:
    "No es la fecha de la guía: muchas gestiones de servicio al cliente ni " +
    "siquiera están ligadas a una.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const FILTERED_BASES: readonly string[] = [
  "creacion",
  "despacho",
  "entrega",
  "movimiento",
  "interaccion",
  "pauta",
];

function readBasis(value: unknown): DateBasis | undefined {
  if (value === null) return null;
  if (typeof value === "string" && FILTERED_BASES.includes(value)) {
    return value as FilteredBasis;
  }
  if (value === "none" || value === "ninguna" || value === "") return null;
  return undefined;
}

/**
 * Pull the rows and the basis out of a KPI response.
 *
 * The shape the API actually sends is `KpiResponse` - `{rows, date_basis,
 * date_from, date_to}` - which is why `rows` is tried first. The basis lives on
 * the envelope rather than on each row precisely so an EMPTY result can still
 * say what it filtered on: "no hay datos" and "no hay guías CREADAS en ese
 * rango" are different sentences, and only the second one is useful.
 *
 * The other shapes are tolerated so a widget keeps rendering if the envelope
 * ever changes again, instead of blanking on `data.map is not a function`.
 */
export interface RangePayload<T> {
  data: T | null;
  dateBasis: DateBasis | undefined;
  /**
   * Guides that can never fall in any range on the chosen date because they do
   * not have it yet. `null` when no range is applied, or when the server did
   * not say.
   */
  excludedNoDate: number | null;
  /**
   * Which platform the server actually narrowed to (migrations 040/041).
   * `null` = it mixed every platform, whether because none was asked for or
   * because that endpoint cannot separate them. `undefined` = it did not say.
   */
  platformApplied: string | null | undefined;
}

/** A count only counts if it is a real, non-negative number. */
function readExcluded(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function readPlatformApplied(payload: Record<string, unknown>): string | null | undefined {
  if (!("platform" in payload)) return undefined;
  const value = payload.platform;
  if (value === null) return null;
  return typeof value === "string" && value !== "" ? value : undefined;
}

export function unwrapRangePayload<T>(payload: unknown): RangePayload<T> {
  if (payload === null || payload === undefined) {
    return { data: null, dateBasis: undefined, excludedNoDate: null, platformApplied: undefined };
  }

  if (Array.isArray(payload)) {
    const first: unknown = payload[0];
    const dateBasis =
      isRecord(first) && "date_basis" in first ? readBasis(first.date_basis) : undefined;
    return { data: payload as T, dateBasis, excludedNoDate: null, platformApplied: undefined };
  }

  if (isRecord(payload) && "date_basis" in payload) {
    const dateBasis = readBasis(payload.date_basis);
    const excludedNoDate = readExcluded(payload.excluded_no_date);
    const platformApplied = readPlatformApplied(payload);
    for (const key of ["rows", "data", "items"]) {
      if (key in payload) {
        return { data: payload[key] as T, dateBasis, excludedNoDate, platformApplied };
      }
    }
    return { data: payload as T, dateBasis, excludedNoDate, platformApplied };
  }

  return { data: payload as T, dateBasis: undefined, excludedNoDate: null, platformApplied: undefined };
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

export interface DateRangeContextValue {
  range: DateRange;
  mode: RangeMode;
  /** Which of the guide's three dates the range is measured against. */
  field: DateField;
  /** The reader's today, once hydrated. `null` on the server. */
  today: Date | null;
  setRange: (range: DateRange) => void;
  setPreset: (preset: PresetKey) => void;
  setField: (field: DateField) => void;
  /**
   * How many guides the chosen date leaves out, as last reported by the API.
   *
   * Hoisted to the provider rather than shown per card because it is a property
   * of (country, date_field), identical on all eighteen of them. Saying it once,
   * loudly, beats saying it eighteen times until nobody reads it.
   */
  excludedNoDate: number | null;
  reportExcluded: (count: number | null) => void;
  /**
   * Which platform's guides the dashboard is narrowed to (`effi`, `dropi`,
   * `manual_xlsx`...). `null` = todas. Lives in the URL like the range, for
   * the same reason: a link that says "Dropi, última semana" has to open as
   * Dropi, última semana.
   */
  platform: string | null;
  setPlatform: (platform: string | null) => void;
}

const DateRangeContext = createContext<DateRangeContextValue | null>(null);

/** Ignore anything that is not a plain ISO date rather than forwarding it. */
function readParam(value: string | null): string | null {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

/** A `field` we do not recognise is not forwarded: the API answers 422 for it. */
function readField(value: string | null): DateField {
  return DATE_FIELDS.includes(value as DateField) ? (value as DateField) : DEFAULT_FIELD;
}

/** Catalogue codes are lower-case slugs; anything else is not forwarded. */
export function readPlatform(value: string | null): string | null {
  return value && /^[a-z0-9_]{1,40}$/.test(value) ? value : null;
}

export function DateRangeProvider({ children }: { children: ReactNode }) {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  // The server has no idea what day it is where the reader sits, and guessing
  // produces a hydration mismatch across timezones. So: nothing until mounted.
  const [today, setToday] = useState<Date | null>(null);
  useEffect(() => {
    setToday(new Date());
  }, []);

  const from = readParam(search.get("from"));
  const to = readParam(search.get("to"));
  const field = readField(search.get("field"));
  const platform = readPlatform(search.get("platform"));

  const range = useMemo<DateRange>(() => ({ from, to }), [from, to]);
  const mode = useMemo(() => inferMode(range, today), [range, today]);

  // Everything the excluded count depends on. When any of it changes the old
  // count is about a question nobody is asking any more.
  const scope = `${pathname}|${field}|${from ?? ""}|${to ?? ""}|${platform ?? ""}`;

  const [excluded, setExcluded] = useState<{ scope: string; count: number } | null>(null);

  const reportExcluded = useCallback(
    (count: number | null) => {
      if (count === null) return;
      setExcluded((current) =>
        current && current.scope === scope && current.count >= count
          ? current
          : { scope, count },
      );
    },
    [scope],
  );

  // Read through the scope rather than clearing on a change: a reset effect in
  // the provider runs AFTER the children's, so it would wipe the count the
  // first widget just reported.
  const excludedNoDate = excluded && excluded.scope === scope ? excluded.count : null;

  /** Rewrites the query string, keeping every parameter it does not own. */
  const writeParams = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(search.toString());
      mutate(params);
      const query = params.toString();
      // `replace`, not `push`: changing the filter is not a navigation, and
      // stacking ten ranges in the history makes Back useless.
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, search],
  );

  const setRange = useCallback(
    (next: DateRange) =>
      writeParams((params) => {
        if (next.from) params.set("from", next.from);
        else params.delete("from");
        if (next.to) params.set("to", next.to);
        else params.delete("to");
      }),
    [writeParams],
  );

  const setPreset = useCallback(
    (preset: PresetKey) => setRange(resolvePreset(preset, today ?? new Date())),
    [setRange, today],
  );

  const setField = useCallback(
    (next: DateField) =>
      writeParams((params) => {
        if (next === DEFAULT_FIELD) params.delete("field");
        else params.set("field", next);
      }),
    [writeParams],
  );

  const setPlatform = useCallback(
    (next: string | null) =>
      writeParams((params) => {
        const clean = readPlatform(next);
        if (clean) params.set("platform", clean);
        else params.delete("platform");
      }),
    [writeParams],
  );

  const value = useMemo<DateRangeContextValue>(
    () => ({
      range,
      mode,
      field,
      today,
      setRange,
      setPreset,
      setField,
      excludedNoDate,
      reportExcluded,
      platform,
      setPlatform,
    }),
    [
      range,
      mode,
      field,
      today,
      setRange,
      setPreset,
      setField,
      excludedNoDate,
      reportExcluded,
      platform,
      setPlatform,
    ],
  );

  return <DateRangeContext.Provider value={value}>{children}</DateRangeContext.Provider>;
}

const INERT_RANGE: DateRangeContextValue = {
  range: MAX_RANGE,
  mode: "maximo",
  field: DEFAULT_FIELD,
  today: null,
  setRange: () => {},
  setPreset: () => {},
  setField: () => {},
  excludedNoDate: null,
  reportExcluded: () => {},
  platform: null,
  setPlatform: () => {},
};

/**
 * Read the active range.
 *
 * Falls back to "Máximo" with inert setters when no provider is mounted, so a
 * widget rendered inside a test or a stray page shows the full history rather
 * than crashing.
 */
export function useDateRange(): DateRangeContextValue {
  return useContext(DateRangeContext) ?? INERT_RANGE;
}

// ---------------------------------------------------------------------------
// Reporting the basis back up to the widget frame
// ---------------------------------------------------------------------------

type BasisReporter = (basis: DateBasis | undefined) => void;
type PlatformReporter = (platform: string | null | undefined) => void;

const ReportBasisContext = createContext<BasisReporter | null>(null);
const ReportPlatformContext = createContext<PlatformReporter | null>(null);

/**
 * Lets the widget frame label a card without every widget having to thread a
 * badge through its own header.
 *
 * `onBasis` and `onPlatform` must be stable - they are effect dependencies.
 * `onPlatform` is optional so a frame that only cares about dates keeps
 * working; the platform report is then simply dropped.
 */
export function DateBasisScope({
  onBasis,
  onPlatform = null,
  children,
}: {
  onBasis: BasisReporter;
  onPlatform?: PlatformReporter | null;
  children: ReactNode;
}) {
  return (
    <ReportBasisContext.Provider value={onBasis}>
      <ReportPlatformContext.Provider value={onPlatform}>{children}</ReportPlatformContext.Provider>
    </ReportBasisContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// The hook every widget uses
// ---------------------------------------------------------------------------

export interface RangedState<T> extends AsyncState<T> {
  dateBasis: DateBasis | undefined;
  excludedNoDate: number | null;
  /** See `RangePayload.platformApplied`. */
  platformApplied: string | null | undefined;
}

/**
 * `useApi`, with the active range, date field and platform spliced into the URL.
 *
 * Pass the path exactly as before; the parameters are appended here and the
 * response is unwrapped here. Refetching is automatic: all three are part of
 * the path, so moving any of them changes the key `useApi` already watches.
 */
export function useRangedApi<T>(path: string | null, deps: unknown[] = []): RangedState<T> {
  const { range, field, platform, reportExcluded } = useDateRange();
  const report = useContext(ReportBasisContext);
  const reportPlatform = useContext(ReportPlatformContext);

  const fullPath = path === null ? null : withRange(path, range, field, platform);
  const { data: payload, error, loading, reload } = useApi<unknown>(fullPath, deps);

  const { data, dateBasis, excludedNoDate, platformApplied } = useMemo(
    () => unwrapRangePayload<T>(payload),
    [payload],
  );

  useEffect(() => {
    if (!loading) reportExcluded(excludedNoDate);
  }, [reportExcluded, excludedNoDate, loading]);

  useEffect(() => {
    // While a request is in flight the previous answer is stale; saying nothing
    // is better than labelling a card from the range it no longer shows.
    report?.(loading ? undefined : dateBasis);
  }, [report, dateBasis, loading]);

  useEffect(() => {
    reportPlatform?.(loading ? undefined : platformApplied);
  }, [reportPlatform, platformApplied, loading]);

  return { data, error, loading, reload, dateBasis, excludedNoDate, platformApplied };
}
