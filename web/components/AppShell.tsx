"use client";

/**
 * The frame every signed-in screen sits in: collapsible sidebar, sticky topbar
 * with the range/store selectors and sync health, and the floating copilot
 * button.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { CopilotPanel } from "@/components/CopilotPanel";
import { DateFieldPicker, ExcludedByFieldBand } from "@/components/DateFieldPicker";
import { DateRangePicker } from "@/components/DateRangePicker";
import { Chip, StatusDot, cx } from "@/components/ui";
import { api, clearTokens } from "@/lib/api";
import { DEFAULT_FIELD, useDateRange } from "@/lib/date-range";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi, usePersistentState } from "@/lib/hooks";
import type { Connection, Country, User } from "@/lib/types";

/**
 * The range selector lives HERE, not on the dashboard page, because it applies
 * to every tab. A filter that only exists on one screen is a filter the reader
 * has to re-apply after every click, and one they will forget is still on.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = usePersistentState("dataeffi.sidebar.collapsed", false);
  const [copilotOpen, setCopilotOpen] = useState(false);
  const { range, field } = useDateRange();

  const { data: countries } = useApi<Country[]>("/config/countries");
  const { data: user } = useApi<User>("/auth/me");
  const { data: connections } = useApi<Connection[]>("/config/connections");

  const activeCountries = useMemo(
    () => (countries ?? []).filter((country) => country.is_active),
    [countries],
  );

  const health = useMemo(() => summariseHealth(connections ?? []), [connections]);

  const currentCountry = useMemo(() => {
    const match = /^\/([a-z]{2})(?:\/|$)/.exec(pathname);
    return match ? match[1].toUpperCase() : null;
  }, [pathname]);

  /**
   * The picker is shown only where it does something.
   *
   * Órdenes, Clientes, Productos and Configuración carry their own filters and
   * their own paging; parking a date range above them that changes nothing on
   * screen is the same lie as an unlabelled unfiltered card. When one of those
   * screens starts honouring the range, add it here.
   */
  const rangeApplies = useMemo(
    () => pathname === "/global" || /^\/[a-z]{2}$/.test(pathname),
    [pathname],
  );

  // Which country's `date_format` the picker writes its dates in. On a screen
  // that is not a country dashboard - Órdenes, Conexiones - the first active
  // country stands in, which is right for the single-country workspaces that
  // are the common case and harmless for the rest.
  const formatCountry = useMemo(
    () =>
      activeCountries.find((country) => country.code === currentCountry) ??
      activeCountries[0],
    [activeCountries, currentCountry],
  );

  /**
   * The range, as a query string to hang off the dashboard links.
   *
   * Switching country must not silently reset the filter: an operator
   * comparing Colombia and México in July would be shown México's whole
   * history and never be told the window changed under them.
   */
  const rangeSuffix = useMemo(() => {
    const params = new URLSearchParams();
    if (range.from) params.set("from", range.from);
    if (range.to) params.set("to", range.to);
    // The chosen date travels with the range. Landing on México measured by
    // creation after reading Colombia by delivery would compare two different
    // questions and look like a difference in performance.
    if (field !== DEFAULT_FIELD) params.set("field", field);
    const query = params.toString();
    return query ? `?${query}` : "";
  }, [range, field]);

  async function signOut() {
    try {
      await api.post("/auth/logout", { refresh_token: "" });
    } catch {
      // Logging out must always succeed locally, even if the API is down.
    }
    clearTokens();
    router.push("/login");
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-page text-ink">
      <aside
        className={cx(
          "flex shrink-0 flex-col border-r border-line-strong bg-sidebar transition-[width] duration-150",
          collapsed ? "w-[64px]" : "w-[232px]",
        )}
      >
        <div className="flex items-center gap-2.5 border-b border-line-subtle px-[18px] py-5">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-[7px] bg-accent text-[14px] font-extrabold text-on-accent">
            DE
          </div>
          {!collapsed && (
            <span className="text-[15px] font-bold tracking-tight">Data Effi</span>
          )}
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-2.5">
          <NavItem
            href={`/global${rangeSuffix}`}
            active={pathname === "/global"}
            collapsed={collapsed}
            icon={<GridIcon />}
            label="Global"
          />

          {!collapsed && activeCountries.length > 0 && (
            <p className="px-2.5 pb-1.5 pt-3.5 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
              Países
            </p>
          )}
          {activeCountries.map((country) => (
            <CountryNav
              key={country.code}
              country={country}
              open={currentCountry === country.code}
              collapsed={collapsed}
              pathname={pathname}
              rangeSuffix={rangeSuffix}
            />
          ))}

          <div className="mt-3.5 border-t border-line-subtle pt-2.5" />

          <NavItem
            href="/ingest"
            active={pathname.startsWith("/ingest")}
            collapsed={collapsed}
            icon={<UploadIcon />}
            label="Cargar datos"
          />
          <NavItem
            href="/connections"
            active={pathname.startsWith("/connections")}
            collapsed={collapsed}
            icon={<PlugIcon />}
            label="Conexiones"
          />
          <NavItem
            href="/settings"
            active={pathname.startsWith("/settings")}
            collapsed={collapsed}
            icon={<GearIcon />}
            label="Configuración"
          />
        </nav>

        <div className="border-t border-line-subtle p-2.5">
          {!collapsed && user && (
            <div className="mb-2 px-2.5">
              <p className="truncate text-[12px] font-semibold text-ink-2">
                {user.full_name ?? user.email}
              </p>
              <p className="truncate text-[10.5px] text-ink-dim">{user.tenant_name}</p>
            </div>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            className="flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-2 text-[12px] text-ink-muted hover:bg-white/[0.04]"
            aria-label={collapsed ? "Expandir menú" : "Colapsar menú"}
          >
            <span aria-hidden>{collapsed ? "»" : "«"}</span>
            {!collapsed && <span>Colapsar</span>}
          </button>
          <button
            type="button"
            onClick={signOut}
            className="flex w-full items-center gap-2.5 rounded-[8px] px-2.5 py-2 text-[12px] text-ink-muted hover:bg-white/[0.04]"
          >
            <span aria-hidden>⏻</span>
            {!collapsed && <span>Cerrar sesión</span>}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line-strong bg-page px-5">
          <div className="min-w-0" />

          <div className="flex items-center gap-2.5">
            {/* WHICH date is asked for here; which date each widget actually
                used is reported on the card, because four endpoints have a
                fixed basis and cannot honour the choice. See `DateBasisNote`. */}
            {rangeApplies && (
              <>
                <DateFieldPicker />
                <DateRangePicker country={formatCountry} />
              </>
            )}

            <Link
              href="/connections"
              className="flex items-center gap-1.5 rounded-full border border-line-strong bg-surface px-2.5 py-1 text-[11px] no-underline"
              title={health.detail}
            >
              <StatusDot tone={health.tone} />
              <span className="text-ink-muted">{health.label}</span>
            </Link>
          </div>
        </header>

        {/* Directly under the header and above everything else: if the chosen
            date hid most of the operation, that has to be read before any
            number on the screen is. */}
        {rangeApplies && <ExcludedByFieldBand country={formatCountry} />}

        <main className="flex-1 overflow-y-auto px-5 py-5">{children}</main>
      </div>

      <button
        type="button"
        onClick={() => setCopilotOpen(true)}
        className="fixed bottom-6 right-6 z-40 flex size-12 items-center justify-center rounded-full bg-accent text-on-accent shadow-lg transition-transform hover:scale-105"
        aria-label="Abrir copiloto"
      >
        <SparkIcon />
      </button>

      <CopilotPanel
        open={copilotOpen}
        onClose={() => setCopilotOpen(false)}
        countryCode={currentCountry}
      />
    </div>
  );
}

/**
 * One country, and its sections when you are inside it.
 *
 * The sections hang off the country rather than sitting at the top level
 * because that is what they are: Ecuador's guides and Colombia's guides are
 * different lists in different currencies, and a single "Órdenes" entry would
 * have to invent an answer to "whose?".
 *
 * It expands on the country you are in and collapses the rest. There is no
 * toggle: an operator lands here to work one country at a time, and four
 * countries each showing four sections is sixteen rows of menu to read past.
 */
function CountryNav({
  country,
  open,
  collapsed,
  pathname,
  rangeSuffix,
}: {
  country: Country;
  open: boolean;
  collapsed: boolean;
  pathname: string;
  /** Carried onto the dashboard links only - see `rangeSuffix` in AppShell. */
  rangeSuffix: string;
}) {
  const base = `/${country.code.toLowerCase()}`;

  // `href` is what the reader navigates to; `match` is what decides whether the
  // item is lit. They differ on the dashboard, whose link carries the range.
  const sections = [
    { match: base, href: `${base}${rangeSuffix}`, label: "Tablero", icon: <GridIcon />, exact: true },
    { match: `${base}/orders`, href: `${base}/orders`, label: "Órdenes", icon: <ReceiptIcon />, exact: false },
    { match: `${base}/customers`, href: `${base}/customers`, label: "Clientes", icon: <PersonIcon />, exact: false },
    { match: `${base}/products`, href: `${base}/products`, label: "Productos", icon: <TagIcon />, exact: false },
  ];

  return (
    <>
      <NavItem
        href={`${base}${rangeSuffix}`}
        active={open}
        collapsed={collapsed}
        icon={<span className="text-[15px] leading-none">{countryFlag(country.code)}</span>}
        label={country.name}
      />

      {open && (
        <div
          className={cx(
            "flex flex-col gap-0.5",
            // Collapsed, the rail is 64px wide and an indent would push the
            // icons off-centre; the flag above is enough of an anchor.
            !collapsed && "ml-[19px] border-l border-line-subtle pl-2",
          )}
        >
          {sections.map((section) => (
            <NavItem
              key={section.match}
              href={section.href}
              active={
                section.exact
                  ? pathname === section.match
                  : pathname.startsWith(section.match)
              }
              collapsed={collapsed}
              icon={section.icon}
              label={section.label}
            />
          ))}
        </div>
      )}
    </>
  );
}

function NavItem({
  href,
  active,
  collapsed,
  icon,
  label,
}: {
  href: string;
  active: boolean;
  collapsed: boolean;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <Link
      href={href}
      title={collapsed ? label : undefined}
      className={cx(
        "flex items-center gap-2.5 rounded-[8px] px-2.5 py-2 text-[13px] no-underline transition-colors",
        active
          ? "bg-accent/[0.12] font-semibold text-accent"
          : "text-ink-nav hover:bg-white/[0.04] hover:text-ink-2",
      )}
    >
      <span className="flex size-4 shrink-0 items-center justify-center">{icon}</span>
      {!collapsed && <span className="truncate">{label}</span>}
    </Link>
  );
}

/** Worst connection wins: one broken source makes the whole pill amber or red. */
function summariseHealth(connections: Connection[]): {
  tone: "positive" | "warning" | "negative" | "neutral";
  label: string;
  detail: string;
} {
  if (connections.length === 0) {
    return { tone: "neutral", label: "Sin conexiones", detail: "Aún no has conectado ninguna fuente" };
  }

  const broken = connections.filter((c) => c.health === "error");
  const stale = connections.filter((c) => c.health === "stale" || c.health === "never_synced");

  if (broken.length > 0) {
    return {
      tone: "negative",
      label: `${broken.length} con error`,
      detail: broken.map((c) => `${c.connection_name}: ${c.last_error ?? "error"}`).join(" · "),
    };
  }
  if (stale.length > 0) {
    return {
      tone: "warning",
      label: `${stale.length} sin sincronizar`,
      detail: stale.map((c) => c.connection_name).join(" · "),
    };
  }

  const newest = connections
    .map((c) => c.last_sync_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .pop();

  return {
    tone: "positive",
    label: newest ? `Sincronizado ${formatRelative(newest)}` : "Al día",
    detail: "Todas las conexiones respondieron",
  };
}

function GridIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      {[
        [1, 1],
        [9, 1],
        [1, 9],
        [9, 9],
      ].map(([x, y]) => (
        <rect
          key={`${x}-${y}`}
          x={x}
          y={y}
          width="6"
          height="6"
          rx="1.5"
          stroke="currentColor"
          strokeWidth="1.4"
        />
      ))}
    </svg>
  );
}

function UploadIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <path
        d="M8 11V3m0 0L5 6m3-3 3 3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M2.5 11v1.5A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5V11" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

/** A slip of paper with lines: one guide, not a report about many. */
function ReceiptIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <path
        d="M3.5 2.2h9v11.6l-1.8-1.1-1.8 1.1-1.9-1.1-1.8 1.1-1.7-1.1V2.2Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M6 5.6h4M6 8.2h4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A person: the screen is about people, not about their parcels. */
function PersonIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <circle cx="8" cy="5.4" r="2.6" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M2.9 13.6c0-2.5 2.3-4.2 5.1-4.2s5.1 1.7 5.1 4.2"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A box with a tag: the catalogue, not a chart. */
function TagIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <path
        d="M2.5 5.6 8 2.6l5.5 3v4.8L8 13.4l-5.5-3V5.6Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M2.5 5.6 8 8.6m0 0 5.5-3M8 8.6v4.8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Two links of a chain: a source wired in, not a chart. */
function PlugIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <path
        d="M7.4 4.6 8.9 3.1a2.9 2.9 0 0 1 4.1 4.1L11.5 8.7"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M8.6 11.4 7.1 12.9a2.9 2.9 0 0 1-4.1-4.1L4.5 7.3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M6.3 9.7 9.7 6.3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <circle cx="8" cy="8" r="2.4" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 1.6v1.6M8 12.8v1.6M14.4 8h-1.6M3.2 8H1.6m10.1-4.5-1.1 1.1M5.4 10.6l-1.1 1.1m8.4 0-1.1-1.1M5.4 5.4 4.3 4.3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="size-5" aria-hidden>
      <path
        d="M10 2.5 11.6 7l4.4 1.6L11.6 10 10 14.5 8.4 10 4 8.6 8.4 7 10 2.5Z"
        fill="currentColor"
      />
    </svg>
  );
}

export { Chip };
