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
import { Chip, StatusDot, cx } from "@/components/ui";
import { api, clearTokens } from "@/lib/api";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi, usePersistentState } from "@/lib/hooks";
import type { Connection, Country, User } from "@/lib/types";

export type RangePreset = "hoy" | "7d" | "30d" | "cohorte";

export interface DateRange {
  preset: RangePreset;
  from?: string;
  to?: string;
}

/** Turns a preset into the ISO dates the API expects. */
export function resolveRange(preset: RangePreset): DateRange {
  const today = new Date();
  const iso = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
      date.getDate(),
    ).padStart(2, "0")}`;

  if (preset === "hoy") return { preset, from: iso(today), to: iso(today) };

  if (preset === "cohorte") {
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    return { preset, from: iso(first), to: iso(today) };
  }

  const days = preset === "7d" ? 7 : 30;
  const start = new Date(today);
  start.setDate(start.getDate() - days + 1);
  return { preset, from: iso(start), to: iso(today) };
}

const RANGE_LABELS: Record<RangePreset, string> = {
  hoy: "Hoy",
  "7d": "7 días",
  "30d": "30 días",
  cohorte: "Mes",
};

export function AppShell({
  children,
  range,
  onRangeChange,
}: {
  children: React.ReactNode;
  range?: DateRange;
  onRangeChange?: (range: DateRange) => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = usePersistentState("norte.sidebar.collapsed", false);
  const [copilotOpen, setCopilotOpen] = useState(false);

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
            N
          </div>
          {!collapsed && (
            <span className="text-[15px] font-bold tracking-tight">Norte</span>
          )}
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-2.5">
          <NavItem
            href="/global"
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
            <NavItem
              key={country.code}
              href={`/${country.code.toLowerCase()}`}
              active={currentCountry === country.code}
              collapsed={collapsed}
              icon={<span className="text-[15px] leading-none">{countryFlag(country.code)}</span>}
              label={country.name}
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
            {range && onRangeChange && (
              <div className="flex items-center gap-0.5 rounded-[8px] border border-line-strong bg-surface p-0.5">
                {(Object.keys(RANGE_LABELS) as RangePreset[]).map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => onRangeChange(resolveRange(preset))}
                    className={cx(
                      "rounded-[6px] px-2.5 py-1 text-[11.5px] font-medium transition-colors",
                      range.preset === preset
                        ? "bg-range-active text-ink"
                        : "text-ink-muted hover:text-ink-2",
                    )}
                  >
                    {RANGE_LABELS[preset]}
                  </button>
                ))}
              </div>
            )}

            <Link
              href="/settings"
              className="flex items-center gap-1.5 rounded-full border border-line-strong bg-surface px-2.5 py-1 text-[11px] no-underline"
              title={health.detail}
            >
              <StatusDot tone={health.tone} />
              <span className="text-ink-muted">{health.label}</span>
            </Link>
          </div>
        </header>

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
