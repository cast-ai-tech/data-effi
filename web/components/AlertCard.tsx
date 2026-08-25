"use client";

/**
 * One alert: severity, money at stake, what was found, what to do, where to go.
 *
 * Shared by the copilot panel and the notification centre so the two never
 * drift into saying the same thing in two layouts. Every card carries exactly
 * one action and one link; a card with three suggestions is a card nobody
 * acts on.
 */

import Link from "next/link";

import { Chip, cx } from "@/components/ui";
import { FALLBACK_COUNTRY, formatMoney } from "@/lib/format";
import type { AlertLike } from "@/lib/types";

export function AlertCard({
  alert,
  onNavigate,
  className,
}: {
  alert: AlertLike;
  /** Called when the reader follows the link, so a slide-over can close. */
  onNavigate?: () => void;
  className?: string;
}) {
  const critical = alert.severity === "critical";

  return (
    <article
      className={cx(
        "rounded-control border bg-surface p-3.5",
        critical
          ? "border-negative/30"
          : alert.severity === "warning"
            ? "border-warning/30"
            : "border-line",
        className,
      )}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <Chip tone={critical ? "negative" : alert.severity === "warning" ? "warning" : "neutral"}>
          {critical ? "CRÍTICA" : alert.severity === "warning" ? "ATENCIÓN" : "INFO"}
        </Chip>
        {alert.impact_amount !== null && (
          <span className="text-xs font-semibold text-negative-ink">
            {formatMoney(alert.impact_amount, {
              ...FALLBACK_COUNTRY,
              currency_symbol: "",
              currency_code: alert.impact_currency ?? "",
              decimal_places: 0,
            })}{" "}
            {alert.impact_currency}
          </span>
        )}
      </div>

      <h4 className="text-base font-semibold text-ink">{alert.title}</h4>
      <p className="mt-1 text-sm leading-[1.55] text-ink-2">{alert.finding}</p>
      <p className="mt-2 text-sm leading-[1.5] text-ink-muted">{alert.action}</p>

      {alert.deep_link && (
        <Link
          href={alert.deep_link}
          onClick={onNavigate}
          className="mt-2.5 inline-block text-sm font-semibold no-underline"
        >
          Ver detalle →
        </Link>
      )}
    </article>
  );
}
