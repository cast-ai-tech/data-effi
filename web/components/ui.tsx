"use client";

/**
 * The small pieces every screen is built from.
 *
 * Hierarchy here comes from size and weight, not from boxes and borders - which
 * is what lets a screen carry this much data without feeling like a spreadsheet
 * threw up on it.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

export { cx } from "@/components/ui/cx";
export { ThemeToggle, useTheme } from "@/components/ui/ThemeToggle";

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cx(
        "rounded-card border border-line bg-surface shadow-card overflow-hidden",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-3.5 sm:px-5">
          <div className="min-w-0">
            {title && (
              <h3 className="text-md font-semibold text-ink leading-tight truncate">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-1 text-sm text-ink-muted leading-snug">{subtitle}</p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className={cx("p-4 sm:p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading and empty states
// ---------------------------------------------------------------------------

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div className={cx("space-y-2", className)} role="status" aria-label="Cargando">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-8 w-full" />
      ))}
    </div>
  );
}

/**
 * An empty state tells you what to DO. "Sin datos" with a shrug is a dead end;
 * every one of these carries the next action.
 */
export function EmptyState({
  title,
  instruction,
  action,
}: {
  title: string;
  instruction: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <p className="text-md font-semibold text-ink-2">{title}</p>
      <p className="max-w-md text-base leading-relaxed text-ink-muted">{instruction}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <p className="text-md font-semibold text-negative-ink">No se pudo cargar</p>
      <p className="max-w-md text-base leading-relaxed text-ink-muted">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 min-h-11 rounded-control border border-line-input px-4 text-base font-medium text-ink-2 hover:border-accent hover:text-accent-ink"
        >
          Reintentar
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chips, badges, pills
// ---------------------------------------------------------------------------

export type Tone = "neutral" | "accent" | "positive" | "warning" | "negative";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-hover-strong text-ink-muted",
  accent: "bg-accent/15 text-accent-ink",
  positive: "bg-positive/15 text-positive-ink",
  warning: "bg-warning/15 text-warning-ink",
  negative: "bg-negative/15 text-negative-ink",
};

export function Chip({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Coloured dot + label. Used for sync health and traffic lights. */
export function StatusDot({ tone }: { tone: Tone }) {
  const colour =
    tone === "positive"
      ? "bg-positive"
      : tone === "warning"
        ? "bg-warning"
        : tone === "negative"
          ? "bg-negative"
          : tone === "accent"
            ? "bg-accent"
            : "bg-ink-dim";
  return <span className={cx("inline-block size-2.5 rounded-full", colour)} />;
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

export function Button({
  children,
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-control font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  // Every size is a comfortable tap target: 36px is the floor for a
  // secondary action, 44px for anything a phone user must hit.
  const sizes = {
    sm: "min-h-9 px-3 text-sm",
    md: "min-h-11 px-4 text-base",
    lg: "min-h-12 px-5 text-md",
  };
  const variants = {
    primary: "bg-accent text-on-accent hover:bg-accent-hover",
    ghost:
      "border border-line-input bg-surface text-ink-2 hover:border-accent-deep hover:text-accent-ink",
    danger: "bg-negative-ink text-on-solid hover:opacity-90",
  };
  return (
    <button className={cx(base, sizes[size], variants[variant], className)} {...props}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Micro-bar: a bar drawn inside a table cell
// ---------------------------------------------------------------------------

export function MicroBar({
  value,
  max,
  tone = "accent",
  label,
}: {
  value: number | null | undefined;
  max: number;
  tone?: Tone;
  label?: string;
}) {
  const safeValue = Number.isFinite(value ?? NaN) ? (value as number) : 0;
  const pct = max > 0 ? Math.min(100, Math.max(0, (safeValue / max) * 100)) : 0;
  const fill =
    tone === "positive"
      ? "bg-positive"
      : tone === "warning"
        ? "bg-warning"
        : tone === "negative"
          ? "bg-negative"
          : "bg-accent";

  return (
    <div className="flex items-center gap-2">
      {label && <span className="w-[52px] shrink-0 text-right text-sm">{label}</span>}
      <div className="h-[6px] flex-1 min-w-[40px] overflow-hidden rounded-full bg-track">
        <div className={cx("h-full rounded-full", fill)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delta indicator
// ---------------------------------------------------------------------------

export function Delta({
  value,
  suffix = "",
  invert = false,
}: {
  value: number | null | undefined;
  suffix?: string;
  /** For metrics where down is good, like return rate or cost. */
  invert?: boolean;
}) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className="text-xs text-ink-dim">sin comparativo</span>;
  }

  const good = invert ? value < 0 : value > 0;
  const neutral = value === 0;
  const tone = neutral ? "text-ink-dim" : good ? "text-positive-ink" : "text-negative-ink";
  const arrow = neutral ? "→" : value > 0 ? "↑" : "↓";

  return (
    <span className={cx("inline-flex items-center gap-1 text-xs font-semibold", tone)}>
      <span aria-hidden>{arrow}</span>
      {Math.abs(value).toFixed(1).replace(".", ",")}
      {suffix}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Section heading
// ---------------------------------------------------------------------------

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-2">
      <h2 className="text-xs font-bold uppercase tracking-[0.06em] text-ink-muted">
        {children}
      </h2>
      {hint && <span className="text-xs text-ink-dim">{hint}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Progressive disclosure for long lists
//
// A table widget that renders every row it receives stops being a widget: with
// 47 cities the office table is taller than the screen and pushes everything
// below it out of view. Cutting the list silently is worse - the reader cannot
// tell whether the rest exists. So the list is capped, the cap is stated, and
// opening it is one click.
// ---------------------------------------------------------------------------

/** How many rows a capped list shows, and grows by, at a time. */
export const PAGE_STEP = 10;

/**
 * Reveal a long list in steps.
 *
 * Returns the slice to render plus what the button needs. Collapsing is offered
 * only once the list is fully open, because a "ver menos" next to "ver más"
 * reads as a pair of opposite actions when it is really an undo.
 */
export function useShowMore<T>(rows: readonly T[], step: number = PAGE_STEP) {
  const [visible, setVisible] = useState(step);

  // A new country or a refreshed load must not keep the previous expansion:
  // the reader is looking at a different list now.
  useEffect(() => {
    setVisible(step);
  }, [rows, step]);

  const shown = visible >= rows.length ? rows : rows.slice(0, visible);
  const remaining = rows.length - shown.length;

  return {
    shown,
    remaining,
    total: rows.length,
    isExpanded: remaining === 0 && rows.length > step,
    showMore: () => setVisible((n) => n + step),
    collapse: () => setVisible(step),
  };
}

export function ShowMore({
  remaining,
  total,
  shownCount,
  onMore,
  onCollapse,
  noun = "filas",
  step = PAGE_STEP,
}: {
  remaining: number;
  total: number;
  shownCount: number;
  onMore: () => void;
  onCollapse: () => void;
  noun?: string;
  step?: number;
}) {
  if (total <= step) return null;

  return (
    <div className="mt-2 flex items-center justify-between gap-3 border-t border-line-subtle pt-2.5">
      <span className="text-xs text-ink-dim">
        {shownCount} de {total} {noun}
      </span>
      {remaining > 0 ? (
        <button
          type="button"
          onClick={onMore}
          className="rounded-md border border-line-strong px-2.5 py-1 text-sm font-medium text-ink-2 transition-colors hover:bg-sunken"
        >
          Ver {Math.min(step, remaining)} más
        </button>
      ) : (
        <button
          type="button"
          onClick={onCollapse}
          className="rounded-md px-2.5 py-1 text-sm font-medium text-ink-dim transition-colors hover:text-ink-2"
        >
          Ver menos
        </button>
      )}
    </div>
  );
}
