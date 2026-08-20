"use client";

/**
 * The small pieces every screen is built from.
 *
 * Hierarchy here comes from size and weight, not from boxes and borders - which
 * is what lets a screen carry this much data without feeling like a spreadsheet
 * threw up on it.
 */

import type { ReactNode } from "react";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

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
        "rounded-[12px] border border-line bg-surface overflow-hidden",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h3 className="text-[13px] font-semibold text-ink leading-tight truncate">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-[11px] text-ink-dim leading-snug">{subtitle}</p>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className={cx("p-4", bodyClassName)}>{children}</div>
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
      <p className="text-[13px] font-semibold text-ink-2">{title}</p>
      <p className="max-w-sm text-[12px] leading-relaxed text-ink-dim">{instruction}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <p className="text-[13px] font-semibold text-negative">No se pudo cargar</p>
      <p className="max-w-sm text-[12px] leading-relaxed text-ink-dim">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 rounded-[8px] border border-line-input px-3 py-1.5 text-[12px] text-ink-2 hover:border-accent hover:text-accent"
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

type Tone = "neutral" | "accent" | "positive" | "warning" | "negative";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-white/[0.06] text-ink-muted",
  accent: "bg-accent/15 text-accent",
  positive: "bg-positive/15 text-positive",
  warning: "bg-warning/15 text-warning",
  negative: "bg-negative/15 text-negative",
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
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold tracking-wide",
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
  return <span className={cx("inline-block size-[7px] rounded-full", colour)} />;
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
  size?: "sm" | "md";
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-[8px] font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const sizes = { sm: "px-3 py-1.5 text-[12px]", md: "px-4 py-2 text-[13px]" };
  const variants = {
    primary: "bg-accent text-on-accent hover:bg-accent-hover",
    ghost: "border border-line-input text-ink-2 hover:border-accent hover:text-accent",
    danger: "border border-negative/40 text-negative hover:bg-negative/10",
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
      {label && <span className="w-[52px] shrink-0 text-right text-[12px]">{label}</span>}
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
    return <span className="text-[11px] text-ink-dim">sin comparativo</span>;
  }

  const good = invert ? value < 0 : value > 0;
  const neutral = value === 0;
  const tone = neutral ? "text-ink-dim" : good ? "text-positive" : "text-negative";
  const arrow = neutral ? "→" : value > 0 ? "↑" : "↓";

  return (
    <span className={cx("inline-flex items-center gap-1 text-[11px] font-semibold", tone)}>
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
      <h2 className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        {children}
      </h2>
      {hint && <span className="text-[11px] text-ink-dim">{hint}</span>}
    </div>
  );
}
