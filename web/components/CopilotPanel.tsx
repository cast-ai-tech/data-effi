"use client";

/**
 * The copilot: a 420px slide-over with three blocks.
 *
 * It reads like an analyst, not like a chatbot. No avatars, no sparkles in the
 * prose, no "¡Claro que sí!". Every alert carries a number, an amount of money
 * and exactly one action. Answers show the SQL that produced them, because a
 * figure you cannot audit is a figure you should not act on.
 */

import { useEffect, useRef, useState } from "react";

import { AlertCard } from "@/components/AlertCard";
import { Button, Chip, Drawer, Skeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
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
      <Drawer onClose={onClose} label="Copiloto" title="Copiloto" bodyClassName="space-y-5">
          <BriefBlock countryCode={countryCode} />
          <AlertsBlock countryCode={countryCode} onNavigate={onClose} />
          <AskBlock countryCode={countryCode} />
      </Drawer>
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
      <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
        Resumen del día
      </h3>

      {!countryCode && (
        <p className="text-sm text-ink-dim">
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
        <p className="text-sm text-ink-dim">
          El resumen no está disponible ahora. Los tableros siguen funcionando.
        </p>
      )}

      {data && (
        <div className="rounded-control border border-line bg-surface p-3.5">
          <p className="whitespace-pre-line text-base leading-[1.65] text-ink-body">
            {data.summary}
          </p>
          {data.degraded && (
            <Chip tone="warning" className="mt-2.5">
              Modo degradado
            </Chip>
          )}
          {data.cached && !data.degraded && (
            <p className="mt-2 text-xs text-ink-faint">Generado una vez al día</p>
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
      <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
        Alertas
      </h3>

      {loading && <Skeleton className="h-20 w-full" />}

      {!loading && alerts.length === 0 && (
        <p className="rounded-control border border-line bg-surface p-3.5 text-sm text-ink-dim">
          Nada que reportar. Ninguna transportadora, producto ni zona cruzó su
          umbral en este periodo.
        </p>
      )}

      <div className="space-y-2.5">
        {alerts.map((alert) => (
          <AlertCard
            key={`${alert.code}-${alert.title}`}
            alert={alert}
            onNavigate={onNavigate}
          />
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
      <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
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
          className="min-w-0 flex-1 rounded-control border border-line-input bg-surface px-3 py-2 text-base text-ink placeholder:text-ink-dim focus:border-accent focus:outline-none"
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
            className="rounded-full border border-line-input bg-surface px-2.5 py-1 text-xs text-ink-2 hover:border-accent hover:text-accent-ink"
          >
            {suggestion}
          </button>
        ))}
      </div>

      {result && (
        <div className="mt-3 rounded-control border border-line bg-surface p-3.5">
          <p className="text-base leading-[1.6] text-ink-body">{result.answer}</p>

          {result.rows.length > 0 && (
            <div className="mt-3 max-h-56 overflow-auto rounded-control border border-line-row">
              <table className="w-full text-xs">
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
                className="mt-2.5 text-xs text-ink-muted underline-offset-2 hover:text-accent-ink"
              >
                {showSql ? "Ocultar consulta" : "Ver consulta"}
              </button>
              {showSql && (
                <pre className="mt-2 overflow-x-auto rounded-control bg-sunken p-2.5 text-xs leading-[1.5] text-ink-muted">
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
