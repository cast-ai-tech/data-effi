"use client";

/**
 * Which platform's guides the dashboard is looking at: Effi, Dropi, the manual
 * upload - or all of them.
 *
 * The operator's hand-made report has one block per platform because the two
 * do not behave alike: different carriers, different return rates, different
 * words for the same status. This control narrows every card to one of them
 * (migrations 040/041). Which cards actually obeyed is reported ON each card,
 * because four endpoints cannot separate platforms and must say so rather
 * than quietly mixing.
 *
 * The options come from the connections that exist, never from a list typed
 * here: a platform with no connection has no guides to show, and a list that
 * needs editing when a connector is added is a list that is wrong by the time
 * someone remembers.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { cx } from "@/components/ui";
import { useDateRange } from "@/lib/date-range";
import { useApi } from "@/lib/hooks";
import type { Connection } from "@/lib/types";

/** Platforms whose connections carry guides. Ads and CS sheets never do. */
const GUIDE_CATEGORIES = new Set(["fulfillment", "tienda", "archivos", "otros"]);

export interface PlatformOption {
  code: string;
  name: string;
}

/**
 * Distinct guide-carrying platforms among the connections, in first-seen
 * order. Exported so the report page can reuse the same rule.
 */
export function platformOptions(
  connections: readonly Connection[] | null | undefined,
  countryCode?: string | null,
): PlatformOption[] {
  const seen = new Map<string, string>();
  for (const connection of connections ?? []) {
    if (!GUIDE_CATEGORIES.has(connection.category)) continue;
    // A global connection (manual upload) belongs to every country.
    if (countryCode && connection.country_code && connection.country_code !== countryCode) {
      continue;
    }
    if (!seen.has(connection.platform_code)) {
      seen.set(connection.platform_code, connection.platform_name);
    }
  }
  return [...seen.entries()].map(([code, name]) => ({ code, name }));
}

export function PlatformPicker({ countryCode }: { countryCode?: string | null }) {
  const { platform, setPlatform } = useDateRange();
  const { data: connections } = useApi<Connection[]>("/config/connections");

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Cerrar al hacer clic fuera y con Escape, igual que rango y estados.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const options = useMemo(
    () => platformOptions(connections, countryCode),
    [connections, countryCode],
  );

  // With one platform there is nothing to choose between; the control would
  // only add a button that does nothing. But a platform picked in the URL that
  // is not among the options still has to be visible and clearable - hiding
  // the control while the filter is active is how a filter becomes invisible.
  if (options.length < 2 && !platform) return null;

  const known = options.some((option) => option.code === platform);
  const current = options.find((option) => option.code === platform);
  // Qué dice el botón cuando está cerrado: el filtro tiene que leerse sin
  // abrirlo, o deja de ser un filtro y pasa a ser una sorpresa.
  const label = platform
    ? current
      ? shortName(current.name)
      : platform
    : "Todas";

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="true"
        title="Plataforma que cargó las guías"
        className={cx(
          "flex items-center gap-2 rounded-control border bg-surface px-3 py-1.5 text-sm transition-colors",
          open
            ? "border-accent text-ink"
            : "border-line-strong text-ink-2 hover:border-accent",
        )}
      >
        <span className="font-medium">Plataforma</span>
        <span className={cx(platform ? "font-medium text-accent" : "text-ink-dim")}>
          {label}
        </span>
        <span aria-hidden className="text-ink-dim">
          ▾
        </span>
      </button>

      {open && (
        <div
          role="group"
          aria-label="Plataforma que cargó las guías"
          className="absolute right-0 top-[calc(100%+6px)] z-50 w-[min(92vw,264px)] overflow-hidden rounded-card border border-line-strong bg-surface shadow-pop"
        >
          <div className="flex flex-col p-1">
            <PickerButton
              active={platform === null}
              onClick={() => {
                setPlatform(null);
                setOpen(false);
              }}
              title="Todas las plataformas juntas"
            >
              Todas
            </PickerButton>
            {options.map((option) => (
              <PickerButton
                key={option.code}
                active={platform === option.code}
                onClick={() => {
                  setPlatform(option.code);
                  setOpen(false);
                }}
                title={`Solo las guías que entraron por ${option.name}`}
              >
                {shortName(option.name)}
              </PickerButton>
            ))}
            {platform && !known && (
              <PickerButton
                active
                onClick={() => {
                  setPlatform(null);
                  setOpen(false);
                }}
                title="Esta plataforma viene del enlace y no tiene conexión aquí. Clic para quitar el filtro."
              >
                {platform} ×
              </PickerButton>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PickerButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={title}
      className={cx(
        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-hover-strong",
        active ? "font-medium text-ink" : "text-ink-2",
      )}
    >
      {children}
    </button>
  );
}

/** "Effi (fulfillment COD)" is a catalogue name; the button says "Effi". */
export function shortName(name: string): string {
  return name.replace(/\s*\(.*\)\s*$/, "").trim() || name;
}
