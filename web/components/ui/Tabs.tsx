"use client";

import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

/**
 * Underlined tabs. On a phone the row scrolls sideways instead of wrapping,
 * so the active tab is always the same shape.
 */
export function Tabs<K extends string>({
  value,
  onChange,
  items,
  label = "Secciones",
  className,
}: {
  value: K;
  onChange: (key: K) => void;
  items: ReadonlyArray<{ key: K; label: ReactNode; count?: number }>;
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cx(
        "-mx-4 flex gap-1 overflow-x-auto border-b border-line-strong px-4 sm:mx-0 sm:px-0",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.key)}
            className={cx(
              "-mb-px flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3.5 text-base transition-colors sm:px-4",
              active
                ? "border-accent font-semibold text-ink"
                : "border-transparent text-ink-muted hover:text-ink-2",
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span
                className={cx(
                  "rounded-full px-1.5 text-xs font-semibold",
                  active ? "bg-accent/15 text-accent-ink" : "bg-hover-strong text-ink-muted",
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
