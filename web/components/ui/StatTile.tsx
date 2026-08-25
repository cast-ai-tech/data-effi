import type { ReactNode } from "react";

import { HelpTip } from "@/components/HelpTip";
import { Delta } from "@/components/ui";
import { cx } from "@/components/ui/cx";
import type { GlossaryKey } from "@/lib/glossary";

/**
 * One big number with a label above and a plain sentence below. The number
 * is the biggest thing on the tile on purpose: this is what the reader came
 * for.
 */
export function StatTile({
  label,
  value,
  hint,
  tone,
  help,
  delta,
  deltaSuffix,
  invertDelta,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "positive" | "negative" | "warning";
  help?: GlossaryKey;
  delta?: number | null;
  deltaSuffix?: string;
  invertDelta?: boolean;
  className?: string;
}) {
  const colour =
    tone === "negative"
      ? "text-negative-ink"
      : tone === "positive"
        ? "text-positive-ink"
        : tone === "warning"
          ? "text-warning-ink"
          : "text-ink";
  return (
    <div className={cx("rounded-card border border-line bg-surface p-4 shadow-card sm:p-5", className)}>
      <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.06em] text-ink-muted">
        <span className="min-w-0 truncate">{label}</span>
        {help && <HelpTip term={help} />}
      </p>
      <p className={cx("mt-2 text-3xl font-bold leading-none tracking-tight tabular-nums sm:text-4xl", colour)}>
        {value}
      </p>
      {(hint || delta !== undefined) && (
        <p className="mt-2.5 flex flex-wrap items-center gap-x-2 text-sm text-ink-muted">
          {delta !== undefined && <Delta value={delta} suffix={deltaSuffix} invert={invertDelta} />}
          {hint && <span>{hint}</span>}
        </p>
      )}
    </div>
  );
}
