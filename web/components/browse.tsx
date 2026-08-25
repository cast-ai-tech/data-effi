"use client";

/**
 * The two controls a server-paginated, country-scoped table always needs.
 *
 * They live here rather than in `ui.tsx` because they know about countries and
 * about pages - concepts `ui.tsx` deliberately does not carry.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { Button, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { countryFlag } from "@/lib/format";
import { LAST_COUNTRY_KEY, pickRedirectCountry } from "@/lib/country";
import { useApi, usePersistentState } from "@/lib/hooks";
import { pageWindow } from "@/lib/orders";
import type { Country } from "@/lib/types";

/**
 * The screen exists, the country does not.
 *
 * Reached by typing a code by hand, or by a link to a country somebody
 * deactivated. Naming the code back to the reader is what separates "you typed
 * XX" from "this app is broken".
 */
export function CountryMissing({
  code,
  countries,
  section,
}: {
  code: string;
  countries: Country[];
  /** Path segment to keep when offering the other countries, e.g. "orders". */
  section?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-14 text-center">
      <p className="text-base font-semibold text-ink-2">
        {code === ""
          ? "Falta el país en la dirección"
          : `${code} no está activo en tu workspace`}
      </p>
      <p className="max-w-md text-sm leading-relaxed text-ink-dim">
        Cada país tiene su propia moneda y sus propios datos, así que esta
        pantalla siempre pertenece a uno. Elige cuál quieres ver, o activa el país
        que falta en Configuración.
      </p>
      {countries.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          {countries.map((country) => (
            <Link
              key={country.code}
              href={`/${country.code.toLowerCase()}${section ? `/${section}` : ""}`}
              className="flex items-center gap-1.5 rounded-control border border-line-input px-3 py-1.5 text-sm font-semibold no-underline hover:border-accent"
            >
              <span aria-hidden>{countryFlag(country.code)}</span>
              {country.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The old country-less routes, kept alive as redirects.
 *
 * `/orders` was a real URL for a while and is sitting in people's tabs and
 * chat logs. Answering it with a 404 because the information architecture
 * changed underneath them punishes the reader for something they did not do,
 * so it forwards - to the country they last worked in, and carrying the query
 * string, because `?customer=…` is the whole payload of a deep link.
 */
export function CountryRedirect({ section }: { section: string }) {
  const router = useRouter();
  const search = useSearchParams();
  const { data } = useApi<Country[]>("/config/countries");
  const [lastCountry] = usePersistentState<string | null>(LAST_COUNTRY_KEY, null);

  const active = useMemo(
    () => (data ?? []).filter((country) => country.is_active),
    [data],
  );

  useEffect(() => {
    if (active.length === 0) return;

    const target = pickRedirectCountry(active, lastCountry);
    if (!target) return;

    const query = search.toString();
    router.replace(
      `/${target.code.toLowerCase()}/${section}${query ? `?${query}` : ""}`,
      { scroll: false },
    );
  }, [active, lastCountry, router, search, section]);

  if (data !== null && active.length === 0) {
    return (
      <EmptyState
        title="No tienes ningún país activo"
        instruction="Activa al menos un país en Configuración para poder ver sus datos."
      />
    );
  }

  return <SkeletonRows rows={8} />;
}

/**
 * Where you are in a list the browser never fully received.
 *
 * The count is the point: an operator who sees fifty rows needs to know they
 * are fifty of 1.649, or they will read the page as the whole operation.
 */
export function PaginationFooter({
  page,
  pageSize,
  total,
  loading,
  noun,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  loading: boolean;
  /** Plural noun for the total, e.g. "guías" or "clientes". */
  noun: string;
  onPageChange: (page: number) => void;
}) {
  const span = pageWindow(page, pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-4 py-3">
      <p className="text-sm text-ink-dim">
        {total === 0
          ? `Sin ${noun}`
          : `${span.first}–${span.last} de ${total} ${noun}`}
      </p>

      <div className="flex items-center gap-2">
        <span className="text-sm text-ink-dim">
          Página {page} de {span.pageCount}
        </span>
        <button
          type="button"
          onClick={() => onPageChange(page - 1)}
          disabled={!span.hasPrevious || loading}
          className={PAGE_BUTTON}
        >
          Anterior
        </button>
        <button
          type="button"
          onClick={() => onPageChange(page + 1)}
          disabled={!span.hasNext || loading}
          className={PAGE_BUTTON}
        >
          Siguiente
        </button>
      </div>
    </div>
  );
}

const PAGE_BUTTON =
  "rounded-control border border-line-input px-3 py-1.5 text-sm font-semibold text-ink-2 " +
  "transition-colors hover:border-accent hover:text-accent-ink " +
  "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-line-input disabled:hover:text-ink-2";

/**
 * A 422 from a filter the operator can see and change.
 *
 * The server's own message is shown verbatim - it names the parameter and the
 * accepted values, which is more than this component could ever guess - and the
 * way out is offered next to it. The generic "No se pudo cargar / Reintentar"
 * is wrong here: retrying the same rejected filter fails the same way forever.
 */
export function FilterError({
  message,
  onClear,
}: {
  message: string;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <p className="text-base font-semibold text-warning-ink">
        El servidor no aceptó este filtro
      </p>
      <p className="max-w-md text-sm leading-relaxed text-ink-dim">{message}</p>
      <Button size="sm" variant="ghost" className="mt-1" onClick={onClear}>
        Quitar filtros
      </Button>
    </div>
  );
}

/**
 * The line that explains a column full of reference codes.
 *
 * Rendered once above the table, never per row: repeating it in fifty cells
 * would bury the data it is explaining.
 */
export function PiiNotice({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="mb-4 rounded-control border border-line-input bg-sunken px-3 py-2 text-sm leading-relaxed text-ink-muted"
      role="note"
    >
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Detail-card primitives
// ---------------------------------------------------------------------------

export function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-control border border-line bg-surface px-3.5 py-3">
      <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </h3>
      <div className="space-y-1.5">{children}</div>
    </section>
  );
}

export function Line({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  const empty = value === null || value === undefined || value === "";

  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-sm text-ink-dim">{label}</span>
      <span
        className={cx(
          "min-w-0 flex-1 truncate text-right text-sm",
          empty ? "text-ink-dim" : "text-ink-2",
          mono && !empty && "font-mono",
        )}
      >
        {empty ? "—" : value}
      </span>
    </div>
  );
}
