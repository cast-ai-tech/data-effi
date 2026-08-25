import type { ReactNode } from "react";

import { HelpTipFor } from "@/components/HelpTip";
import { cx } from "@/components/ui/cx";
import type { GlossaryKey } from "@/lib/glossary";

/**
 * The top of every screen: one big title, one plain sentence under it, and
 * the screen's actions on the right (below the title on a phone).
 */
export function PageHeader({
  title,
  eyebrow,
  flag,
  subtitle,
  actions,
  help,
  className,
}: {
  title: ReactNode;
  /** Small label above the title, e.g. the section name. */
  eyebrow?: ReactNode;
  /** A country flag emoji, drawn big next to the title. */
  flag?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  help?: GlossaryKey | string;
  className?: string;
}) {
  return (
    <header
      className={cx(
        "mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-4",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-xs font-bold uppercase tracking-[0.06em] text-ink-muted">{eyebrow}</p>
        )}
        <h1 className="flex items-center gap-2.5 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
          {flag && (
            <span className="text-2xl leading-none sm:text-3xl" aria-hidden>
              {flag}
            </span>
          )}
          <span className="min-w-0">{title}</span>
          {help && <HelpTipFor help={help} />}
        </h1>
        {subtitle && (
          <div className="mt-1.5 max-w-2xl text-base leading-relaxed text-ink-muted">{subtitle}</div>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
