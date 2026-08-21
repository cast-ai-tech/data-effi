"use client";

/**
 * The country a screen is about, taken from the route.
 *
 * Every data screen lives under `/[country]/…` because a country is not a
 * filter here - it is the unit the whole product is organised by. Each one has
 * its own currency, its own separators, its own date format and its own
 * carriers, and a table that mixes two of them puts 21,99 next to 45.000 in the
 * same column. That is unreadable, and worse, it invites the reader to compare
 * them.
 *
 * Putting the country in the URL rather than in component state is what makes a
 * screen linkable: a message that says "mira las guías de Ecuador" can carry a
 * URL that actually opens them.
 */

import { useParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { useApi, usePersistentState } from "@/lib/hooks";
import type { Country } from "@/lib/types";

/** Where a country-less URL sends the operator back to. */
export const LAST_COUNTRY_KEY = "dataeffi.country.last";

export interface RouteCountry {
  /** Uppercased code from the path segment. "" when the route has none. */
  code: string;
  /** The full row, with its formatting rules. Null until it is confirmed. */
  country: Country | null;
  /** Every active country, for the sidebar and for redirects. */
  countries: Country[];
  loading: boolean;
  /**
   * True once the workspace answered and this code is not one of its countries.
   *
   * Deliberately distinct from `country === null`: while the request is in
   * flight the country is also null, and showing "EC no está activo" for half a
   * second on every load would be a lie that flashes.
   */
  missing: boolean;
}

export function useRouteCountry(): RouteCountry {
  const params = useParams<{ country: string }>();
  const code = (params.country ?? "").toUpperCase();

  const { data, loading } = useApi<Country[]>("/config/countries");

  const countries = useMemo(
    () => (data ?? []).filter((item) => item.is_active),
    [data],
  );
  const country = useMemo(
    () => countries.find((item) => item.code === code) ?? null,
    [countries, code],
  );

  // Remembered so that a country-less link - a bookmark from before the routes
  // moved, or a shared /orders URL - lands where this operator actually works
  // instead of on whichever country sorts first.
  const [, setLastCountry] = usePersistentState<string | null>(LAST_COUNTRY_KEY, null);
  useEffect(() => {
    if (country) setLastCountry(country.code);
    // `setLastCountry` is stable per key; listing it would re-run this on every
    // render of the hook that owns it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country]);

  return {
    code,
    country,
    countries,
    loading,
    missing: !loading && data !== null && country === null,
  };
}

/**
 * Which country a country-less URL should open.
 *
 * The one the operator last worked in, when it is still active - falling back
 * to the first active country, and to null when the workspace has none. An
 * operator who runs Ecuador should not be dropped into Colombia just because
 * it sorts first, and a country that was deactivated since their last visit
 * must not send them to a screen that immediately says it does not exist.
 */
export function pickRedirectCountry(
  countries: Country[],
  lastCountry: string | null,
): Country | null {
  if (countries.length === 0) return null;
  const remembered = countries.find((country) => country.code === lastCountry);
  return remembered ?? countries[0];
}
