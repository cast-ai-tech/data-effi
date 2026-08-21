"use client";

/**
 * The copilot: a 420px slide-over with three blocks.
 *
 * It reads like an analyst, not like a chatbot. No avatars, no sparkles in the
 * prose, no "¡Claro que sí!". Every alert carries a number, an amount of money
 * and exactly one action. Answers show the SQL that produced them, because a
 * figure you cannot audit is a figure you should not act on.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button, Chip, Skeleton, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatMoney, FALLBACK_COUNTRY } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Alert, AskResult, Brief } from "@/lib/types";

const SUGGESTIONS = [
  "¿Qué producto me está quemando plata?",
  "¿Cuál transportadora tiene la peor efectividad?",
  "¿Qué ciudades están en rojo?",
  "¿Cuánta plata tengo atascada en guías viejas?",
];

export function CopilotPanel({
  open,
  onClose,
  countryCode,
}: {
  open: boolean;
  onClose: () => void;
  countryCode: string | null;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    if (open) document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/40"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[420px] flex-col border-l border-line bg-sidebar"
        role="dialog"
        aria-label="Copiloto"
      >
        <header className="flex items-center justify-between border-b border-line-subtle px-4 py-3.5">
          <h2 className="text-[14px] font-bold">Copiloto</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[6px] px-2 py-1 text-[13px] text-ink-muted hover:bg-white/[0.05]"
            aria-label="Cerrar"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-4">
          <BriefBlock countryCode={countryCode} />
          <AlertsBlock countryCode={countryCode} onNavigate={onClose} />
          <AskBlock countryCode={countryCode} />
        </div>
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// Daily brief
// ---------------------------------------------------------------------------

function BriefBlock({ countryCode }: { countryCode: string | null }) {
  const { data, loading, error } = useApi<Brief>(
    countryCode ? `/ai/brief?country=${countryCode}` : null,
  );

  return (
    <section>
      <h3 className="mb-2 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        Resumen del día
      </h3>

      {!countryCode && (
        <p className="text-[12px] text-ink-dim">
          Abre un país para ver su resumen.
        </p>
      )}

      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-11/12" />
          <Skeleton className="h-3 w-4/5" />
        </div>
      )}

      {error && (
        <p className="text-[12px] text-ink-dim">
          El resumen no está disponible ahora. Los tableros siguen funcionando.
        </p>
      )}

      {data && (
        <div className="rounded-[10px] border border-line bg-surface p-3.5">
          <p className="whitespace-pre-line text-[12.5px] leading-[1.65] text-ink-body">
            {data.summary}
          </p>
          {data.degraded && (
            <Chip tone="warning" className="mt-2.5">
              Modo degradado
            </Chip>
          )}
          {data.cached && !data.degraded && (
            <p className="mt-2 text-[10.5px] text-ink-faint">Generado una vez al día</p>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

function AlertsBlock({
  countryCode,
  onNavigate,
}: {
  countryCode: string | null;
  onNavigate: () => void;
}) {
  const { data, loading } = useApi<{ alerts: Alert[] }>(
    countryCode ? `/ai/alerts?country=${countryCode}` : "/ai/alerts",
  );

  const alerts = data?.alerts ?? [];

  return (
    <section>
      <h3 className="mb-2 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        Alertas
      </h3>

      {loading && <Skeleton className="h-20 w-full" />}

      {!loading && alerts.length === 0 && (
        <p className="rounded-[10px] border border-line bg-surface p-3.5 text-[12px] text-ink-dim">
          Nada que reportar. Ninguna transportadora, producto ni zona cruzó su
          umbral en este periodo.
        </p>
      )}

      <div className="space-y-2.5">
        {alerts.map((alert) => (
          <article
            key={`${alert.code}-${alert.title}`}
            className={cx(
              "rounded-[10px] border bg-surface p-3.5",
              alert.severity === "critical"
                ? "border-negative/30"
                : alert.severity === "warning"
                  ? "border-warning/30"
                  : "border-line",
            )}
          >
            <div className="mb-1.5 flex items-center gap-2">
              <Chip tone={alert.severity === "critical" ? "negative" : "warning"}>
                {alert.severity === "critical" ? "CRÍTICA" : "ATENCIÓN"}
              </Chip>
              {alert.impact_amount !== null && (
                <span className="text-[11px] font-semibold text-negative">
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

            <h4 className="text-[12.5px] font-semibold text-ink">{alert.title}</h4>
            <p className="mt-1 text-[12px] leading-[1.55] text-ink-2">{alert.finding}</p>
            <p className="mt-2 text-[12px] leading-[1.5] text-ink-muted">{alert.action}</p>

            <Link
              href={alert.deep_link}
              onClick={onNavigate}
              className="mt-2.5 inline-block text-[11.5px] font-semibold no-underline"
            >
              Ver detalle →
            </Link>
          </article>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Ask your data
// ---------------------------------------------------------------------------

function AskBlock({ countryCode }: { countryCode: string | null }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [pending, setPending] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function ask(text: string) {
    if (!text.trim() || pending) return;
    setPending(true);
    setResult(null);
    setShowSql(false);
    try {
      const answer = await api.post<AskResult>("/ai/ask", {
        question: text,
        country_code: countryCode ?? undefined,
      });
      setResult(answer);
    } catch (error) {
      setResult({
        answer:
          error instanceof ApiError
            ? error.message
            : "No se pudo consultar. Inténtalo de nuevo.",
        sql: null,
        columns: [],
        rows: [],
        row_count: 0,
        rejected: true,
        rejection_reason: null,
        suggestions: SUGGESTIONS,
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <section>
      <h3 className="mb-2 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        Pregúntale a tus datos
      </h3>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
        className="flex gap-2"
      >
        <input
          ref={inputRef}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="¿Qué quieres saber?"
          className="min-w-0 flex-1 rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[12.5px] text-ink placeholder:text-ink-dim focus:border-accent focus:outline-none"
        />
        <Button type="submit" size="sm" disabled={pending || !question.trim()}>
          {pending ? "..." : "Ir"}
        </Button>
      </form>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {(result?.suggestions?.length ? result.suggestions : SUGGESTIONS).map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => {
              setQuestion(suggestion);
              void ask(suggestion);
            }}
            className="rounded-full border border-line-input bg-surface px-2.5 py-1 text-[11px] text-ink-2 hover:border-accent hover:text-accent"
          >
            {suggestion}
          </button>
        ))}
      </div>

      {result && (
        <div className="mt-3 rounded-[10px] border border-line bg-surface p-3.5">
          <p className="text-[12.5px] leading-[1.6] text-ink-body">{result.answer}</p>

          {result.rows.length > 0 && (
            <div className="mt-3 max-h-56 overflow-auto rounded-[8px] border border-line-row">
              <table className="w-full text-[11px]">
                <thead className="sticky top-0 bg-sunken">
                  <tr>
                    {result.columns.map((column) => (
                      <th scope="col"
                        key={column}
                        className="whitespace-nowrap px-2 py-1.5 text-left font-semibold text-ink-dim"
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 50).map((row, index) => (
                    <tr key={index} className="border-t border-line-row">
                      {result.columns.map((column) => (
                        <td key={column} className="whitespace-nowrap px-2 py-1.5 text-ink-2">
                          {String(row[column] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.sql && (
            <>
              <button
                type="button"
                onClick={() => setShowSql(!showSql)}
                className="mt-2.5 text-[11px] text-ink-muted underline-offset-2 hover:text-accent"
              >
                {showSql ? "Ocultar consulta" : "Ver consulta"}
              </button>
              {showSql && (
                <pre className="mt-2 overflow-x-auto rounded-[8px] bg-sunken p-2.5 text-[10.5px] leading-[1.5] text-ink-muted">
                  {result.sql}
                </pre>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
