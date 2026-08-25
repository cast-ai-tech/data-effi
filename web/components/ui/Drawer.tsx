"use client";

/**
 * A panel that slides in from the right: order detail, product form, copilot,
 * notifications. One implementation so every panel closes the same way
 * (Escape, tap on the scrim, the ✕) and is full-width on a phone.
 */

import { useEffect } from "react";
import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

const WIDTHS = {
  md: "sm:max-w-[440px]",
  lg: "sm:max-w-[560px]",
} as const;

export function Drawer({
  onClose,
  label,
  title,
  subtitle,
  actions,
  header,
  width = "md",
  children,
  bodyClassName,
  ...rest
}: {
  onClose: () => void;
  /** What assistive tech announces. Falls back to `title` when it is a string. */
  label?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Buttons next to the ✕. */
  actions?: ReactNode;
  /** Replaces the default header entirely (still gets the ✕ from `actions`). */
  header?: ReactNode;
  width?: keyof typeof WIDTHS;
  children: ReactNode;
  bodyClassName?: string;
} & Record<`data-${string}`, string | boolean | undefined>) {
  // Escape closes; the page behind must not scroll while the panel is open.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const ariaLabel = label ?? (typeof title === "string" ? title : undefined);

  return (
    <>
      <div className="fixed inset-0 z-40 bg-scrim" onClick={onClose} aria-hidden />
      <aside
        {...rest}
        className={cx(
          "fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-sidebar shadow-pop",
          WIDTHS[width],
        )}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
      >
        {header ?? (
          <header className="flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-4 sm:px-5">
            <div className="min-w-0">
              {title && <h2 className="truncate text-lg font-bold text-ink">{title}</h2>}
              {subtitle && <div className="mt-1 text-sm text-ink-muted">{subtitle}</div>}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {actions}
              <CloseButton onClose={onClose} />
            </div>
          </header>
        )}
        <div
          className={cx(
            "flex-1 overflow-y-auto p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] sm:p-5",
            bodyClassName,
          )}
        >
          {children}
        </div>
      </aside>
    </>
  );
}

export function CloseButton({ onClose, label = "Cerrar" }: { onClose: () => void; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClose}
      className="flex size-11 items-center justify-center rounded-control text-lg text-ink-muted transition-colors hover:bg-hover-strong hover:text-ink"
      aria-label={label}
    >
      <span aria-hidden>✕</span>
    </button>
  );
}
