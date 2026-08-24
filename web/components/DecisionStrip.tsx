"use client";

/**
 * A strip of verdicts above a dashboard tab: "corta este producto", "cambia
 * de transportadora en Antioquia", "llama primero a Cali".
 *
 * The verdicts are computed in SQL and are deterministic; no model is involved
 * until the reader presses "Explicar", which asks the same endpoint with
 * `narrative=true` and shows whatever prose comes back. If the model is out of
 * budget the strip says so quietly and the verdicts stand.
 *
 * Tones: `cut/call/switch` are accent because they are the actionable ones;
 * `keep/ok` are positive because they say money is being made; `watch/hold`
 * are muted ink. Warning is never used here - it means a degraded connector,
 * and a "vigilar" verdict is not a fault in the plumbing.
 */

import Link from "next/link";
import { useState } from "react";

import { Chip, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { FALLBACK_COUNTRY, formatMoney } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Decision, DecisionScope, DecisionsResponse, Verdict } from "@/lib/types";

export const VERDICT_META: Record<
  Verdict,
  { label: string; tone: "accent" | "positive" | "neutral" }
> = {
  cut: { label: "Cortar", tone: "accent" },
  call: { label: "Llamar", tone: "accent" },
  switch: { label: "Cambiar", tone: "accent" },
  keep: { label: "Seguir", tone: "positive" },
  ok: { label: "Bien", tone: "positive" },
  watch: { label: "Vigilar", tone: "neutral" },
  hold: { label: "Esperar", tone: "neutral" },
};

const SCOPE_LABEL: Record<DecisionScope, string> = {
  products: "Productos",
  carriers: "Transportadoras por zona",
  office: "Rescate en oficina",
  cash: "Caja",
};

/** Action first, then watch, then the reassuring ones. */
const VERDICT_ORDER: Verdict[] = ["cut", "call", "switch", "watch", "hold", "keep", "ok"];

export function decisionsPath(countryCode: string, scope: DecisionScope, narrative = false) {
  return `/ai/decisions?country=${encodeURIComponent(countryCode)}&scope=${scope}${
    narrative ? "&narrative=true" : ""
  }`;
}

export function DecisionStrip({
  countryCode,
  scope,
  max = 3,
}: {
  countryCode: string;
  scope: DecisionScope;
  max?: number;
}) {
  const { data, loading, error } = useApi<DecisionsResponse>(
    countryCode ? decisionsPath(countryCode, scope) : null,
    [countryCode, scope],
  );
  const [narrative, setNarrative] = useState<string | null>(null);
  const [narrativeNote, setNarrativeNote] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);

  const label = `Decisiones · ${SCOPE_LABEL[scope]}`;

  if (loading && !data) {
    return (
      <section role="region" aria-label={label} className="mb-5">
        <Skeleton className="h-12 w-full" />
      </section>
    );
  }

  // No endpoint, no data, or nothing worth saying: the strip takes no space.
  // A dashboard with an empty "Decisiones" box would read as a broken feature.
  if (error || !data) return null;

  const items = [...data.items]
    .sort((a, b) => VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict))
    .slice(0, max);
  if (items.length === 0) return null;

  async function explain() {
    setExplaining(true);
    setNarrativeNote(null);
    try {
      const response = await api.get<DecisionsResponse>(
        decisionsPath(countryCode, scope, true),
      );
      if (response.narrative) {
        setNarrative(response.narrative);
      } else {
        setNarrativeNote(
          response.degraded_reason ??
            "La explicación no está disponible ahora. Los veredictos siguen siendo válidos.",
        );
      }
    } catch {
      setNarrativeNote(
        "La explicación no está disponible ahora. Los veredictos siguen siendo válidos.",
      );
    } finally {
      setExplaining(false);
    }
  }

  return (
    <section
      role="region"
      aria-label={label}
      className="mb-5 rounded-[12px] border border-line bg-surface"
    >
      <div className="flex items-center justify-between gap-3 border-b border-line-subtle px-4 py-2.5">
        <h2 className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
          {label}
        </h2>
        {narrative === null && (
          <button
            type="button"
            onClick={() => void explain()}
            disabled={explaining}
            className="text-[11.5px] font-medium text-accent disabled:text-ink-dim"
          >
            {explaining ? "Explicando…" : "Explicar"}
          </button>
        )}
      </div>

      <ul className="divide-y divide-line-row">
        {items.map((item) => (
          <DecisionRow key={item.key} item={item} />
        ))}
      </ul>

      {narrative && (
        <p className="whitespace-pre-line border-t border-line-subtle px-4 py-3 text-[12.5px] leading-[1.65] text-ink-body">
          {narrative}
        </p>
      )}
      {narrativeNote && (
        <p className="border-t border-line-subtle px-4 py-2.5 text-[11.5px] text-ink-dim">
          {narrativeNote}
        </p>
      )}
      {data.degraded && !narrative && !narrativeNote && data.degraded_reason && (
        <p className="border-t border-line-subtle px-4 py-2.5 text-[11.5px] text-ink-dim">
          {data.degraded_reason}
        </p>
      )}
    </section>
  );
}

function DecisionRow({ item }: { item: Decision }) {
  const meta = VERDICT_META[item.verdict] ?? VERDICT_META.watch;
  const money =
    item.impact_amount !== null
      ? `${formatMoney(item.impact_amount, {
          ...FALLBACK_COUNTRY,
          currency_symbol: "",
          currency_code: item.impact_currency ?? "",
          decimal_places: 0,
        })} ${item.impact_currency ?? ""}`.trim()
      : null;

  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <Chip tone={meta.tone} className="mt-0.5 shrink-0">
        {meta.label}
      </Chip>
      <div className="min-w-0 flex-1">
        <p className="text-[12.5px] font-semibold text-ink">{item.label}</p>
        <p className="mt-0.5 text-[12px] leading-[1.5] text-ink-2">{item.headline}</p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        {money && (
          <span
            className={cx(
              "text-[11px] font-semibold",
              // Money lost is negative; money still to be made reads positive.
              item.verdict === "keep" || item.verdict === "ok" ? "text-positive" : "text-negative",
            )}
          >
            {money}
          </span>
        )}
        {item.deep_link && (
          <Link href={item.deep_link} className="text-[11.5px] font-semibold no-underline">
            Ver →
          </Link>
        )}
      </div>
    </li>
  );
}
