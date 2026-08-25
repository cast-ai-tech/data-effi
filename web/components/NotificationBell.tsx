"use client";

/**
 * The bell in the header.
 *
 * An accent dot says "there is something unread": accent is the colour of
 * things you can act on. A number in `negative` appears ONLY for critical
 * notifications - money being lost right now. Warning is never used here; it
 * belongs to degraded connectors, and a bell that turned amber every morning
 * for the digest would teach the reader to ignore amber everywhere.
 */

import { useCallback, useEffect, useState } from "react";

import { NotificationCenter } from "@/components/NotificationCenter";
import { cx } from "@/components/ui";
import { useNotifications } from "@/lib/notifications";

export function bellLabel(unread: number, critical: number): string {
  if (unread === 0) return "Notificaciones, sin novedades";
  const base = `Notificaciones, ${unread} sin leer`;
  if (critical === 0) return base;
  return `${base} (${critical} ${critical === 1 ? "crítica" : "críticas"})`;
}

export function NotificationBell({ countryCode }: { countryCode: string | null }) {
  const { unreadCount, criticalUnread } = useNotifications();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

  // Escape and a click anywhere outside the bell or the centre close it, like
  // every other menu in the shell.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onClick(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (
        !target?.closest("[data-notification-bell]") &&
        !target?.closest("[data-notification-center]")
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <div data-notification-bell className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label={bellLabel(unreadCount, criticalUnread)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cx(
          "relative flex size-9 items-center justify-center rounded-full border border-line-strong bg-surface text-ink-muted transition-colors hover:text-ink-2",
          open && "text-accent-ink",
        )}
      >
        <BellIcon />
        {criticalUnread > 0 ? (
          <span
            data-testid="bell-badge"
            aria-hidden
            className="absolute -right-1 -top-1 flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-negative px-1 text-xs font-bold leading-none text-on-solid"
          >
            {criticalUnread > 99 ? "99+" : criticalUnread}
          </span>
        ) : unreadCount > 0 ? (
          <span
            data-testid="bell-dot"
            aria-hidden
            className="absolute right-[7px] top-[7px] size-[7px] rounded-full bg-accent"
          />
        ) : null}
      </button>

      <NotificationCenter open={open} onClose={close} countryCode={countryCode} />
    </div>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-4" aria-hidden>
      <path
        d="M4 6.5a4 4 0 1 1 8 0v2.8l1.2 1.9H2.8L4 9.3V6.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M6.5 13a1.5 1.5 0 0 0 3 0"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
