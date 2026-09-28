"use client";

/**
 * The dashboard filters, remembered between screens and between visits.
 *
 * The range, platform and status filters live in the URL of the dashboards
 * (`/global`, `/co`) - see lib/date-range.tsx. That is right for sharing a
 * link, but on its own it forgets: Osvaldo narrows Colombia to "Últimos 7
 * días", opens Órdenes to check a guide, clicks "Tablero" to go back... and
 * lands on the whole history, because Órdenes had no range in its URL to hand
 * back. The same happened after signing in the next morning.
 *
 * So the last filters used on a dashboard are kept here (localStorage, this
 * browser only) and:
 *   - the menu's dashboard links carry them, and
 *   - arriving at a dashboard with NO filters in the URL restores them.
 *
 * A shortcut is stored as the shortcut, not as its dates: "Últimos 7 días"
 * chosen on Monday still means the last seven days on Tuesday. A range picked
 * by hand on the calendar is stored as its two dates.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  PRESET_KEYS,
  fromIso,
  inferMode,
  readPlatform,
  readStatuses,
  resolvePreset,
  type PresetKey,
} from "@/lib/date-range";

export const FILTER_MEMORY_KEY = "masterdata.dashboard.filters";

/** The URL parameters this module owns. `tab` is handled apart. */
export const FILTER_PARAMS = ["from", "to", "platform", "statuses"] as const;

export interface RememberedFilters {
  /** A shortcut ("7d", "este_mes"...) or null for a hand-picked range. */
  preset: PresetKey | null;
  from: string | null;
  to: string | null;
  platform: string | null;
  /** Comma-separated status groups, or null for all five. */
  statuses: string | null;
  /** Last tab open on a country dashboard ("logistica"...). */
  tab: string | null;
}

const EMPTY: RememberedFilters = {
  preset: "maximo",
  from: null,
  to: null,
  platform: null,
  statuses: null,
  tab: null,
};

/** `/global` and a country's dashboard (`/co`): the screens the filters apply to. */
export function isDashboardPath(pathname: string): boolean {
  return pathname === "/global" || /^\/[a-z]{2}$/.test(pathname);
}

function isCountryDashboard(pathname: string): boolean {
  return /^\/[a-z]{2}$/.test(pathname);
}

function cleanTab(value: string | null | undefined): string | null {
  return value && /^[a-z_]{1,30}$/.test(value) ? value : null;
}

/** Whether the URL already says which filters to use. */
export function hasFilterParams(search: URLSearchParams): boolean {
  return FILTER_PARAMS.some((key) => search.has(key));
}

/** Read what a dashboard URL is filtering on, keeping the tab from before when this is not a country dashboard. */
export function captureFilters(
  pathname: string,
  search: URLSearchParams,
  today: Date,
  previous: RememberedFilters | null = null,
): RememberedFilters {
  const from = fromIso(search.get("from")) ? search.get("from") : null;
  const to = fromIso(search.get("to")) ? search.get("to") : null;
  const mode = inferMode({ from, to }, today);
  return {
    preset: mode === "personalizado" ? null : mode,
    from,
    to,
    platform: readPlatform(search.get("platform")),
    statuses: readStatuses(search.get("statuses"))?.join(",") ?? null,
    tab: isCountryDashboard(pathname)
      ? cleanTab(search.get("tab"))
      : (previous?.tab ?? null),
  };
}

/**
 * The remembered filters as URL parameters, dates recomputed for `today`.
 * `withTab` adds the dashboard tab (country links only).
 */
export function filterParams(
  filters: RememberedFilters | null,
  today: Date,
  withTab = false,
): URLSearchParams {
  const params = new URLSearchParams();
  if (!filters) return params;
  const range = filters.preset
    ? resolvePreset(filters.preset, today)
    : { from: filters.from, to: filters.to };
  if (range.from) params.set("from", range.from);
  if (range.to) params.set("to", range.to);
  if (filters.platform) params.set("platform", filters.platform);
  if (filters.statuses) params.set("statuses", filters.statuses);
  if (withTab && filters.tab) params.set("tab", filters.tab);
  return params;
}

/** `?from=…&to=…` to hang off a dashboard link, or "" when there is nothing to carry. */
export function filterQuery(
  filters: RememberedFilters | null,
  today: Date,
  withTab = false,
): string {
  const query = filterParams(filters, today, withTab).toString();
  return query ? `?${query}` : "";
}

function parse(raw: string | null): RememberedFilters | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<RememberedFilters> | null;
    if (!value || typeof value !== "object") return null;
    const preset =
      typeof value.preset === "string" &&
      (PRESET_KEYS as readonly string[]).includes(value.preset)
        ? (value.preset as PresetKey)
        : null;
    const from = typeof value.from === "string" && fromIso(value.from) ? value.from : null;
    const to = typeof value.to === "string" && fromIso(value.to) ? value.to : null;
    return {
      preset: preset ?? (from || to ? null : "maximo"),
      from,
      to,
      platform: readPlatform(typeof value.platform === "string" ? value.platform : null),
      statuses:
        readStatuses(typeof value.statuses === "string" ? value.statuses : null)?.join(",") ?? null,
      tab: cleanTab(typeof value.tab === "string" ? value.tab : null),
    };
  } catch {
    return null;
  }
}

export function readRememberedFilters(): RememberedFilters | null {
  try {
    return parse(window.localStorage.getItem(FILTER_MEMORY_KEY));
  } catch {
    return null;
  }
}

export function writeRememberedFilters(filters: RememberedFilters): void {
  try {
    window.localStorage.setItem(FILTER_MEMORY_KEY, JSON.stringify(filters));
  } catch {
    // Private mode or a full quota: the filters simply are not remembered.
  }
}

/**
 * Keep the memory in sync with the dashboards and bring it back on arrival.
 *
 * Mounted once per screen (by AppShell). On a dashboard:
 *   - arriving with no filters in the URL restores the remembered ones
 *     (`replace`, so Back does not stop on the unfiltered version);
 *   - otherwise whatever the URL says becomes the memory - including
 *     "Máximo", so clearing the filters is remembered too.
 * Elsewhere it only reads, so the menu links can carry the filters.
 *
 * Returns the remembered filters, for the links.
 */
export function useFilterMemory(): RememberedFilters | null {
  const pathname = usePathname();
  const search = useSearchParams();
  const router = useRouter();
  const [memory, setMemory] = useState<RememberedFilters | null>(null);
  // Every screen mounts its own AppShell, so a fresh mount IS an arrival.
  const arriving = useRef(true);

  useEffect(() => {
    const stored = readRememberedFilters();
    const firstRun = arriving.current;
    arriving.current = false;

    if (!isDashboardPath(pathname)) {
      setMemory(stored);
      return;
    }

    const today = new Date();
    if (firstRun && !hasFilterParams(search) && stored) {
      const restored = new URLSearchParams(search.toString());
      filterParams(stored, today).forEach((value, key) => restored.set(key, value));
      if (isCountryDashboard(pathname) && !restored.has("tab") && stored.tab) {
        restored.set("tab", stored.tab);
      }
      if (restored.toString() !== search.toString()) {
        setMemory(stored);
        router.replace(`${pathname}?${restored.toString()}`, { scroll: false });
        return;
      }
    }

    const current = captureFilters(pathname, search, today, stored);
    if (!sameFilters(current, stored)) writeRememberedFilters(current);
    setMemory(current);
  }, [pathname, search, router]);

  return memory;
}

export function sameFilters(a: RememberedFilters | null, b: RememberedFilters | null): boolean {
  const x = a ?? EMPTY;
  const y = b ?? EMPTY;
  return (
    x.preset === y.preset &&
    x.from === y.from &&
    x.to === y.to &&
    x.platform === y.platform &&
    x.statuses === y.statuses &&
    x.tab === y.tab
  );
}
