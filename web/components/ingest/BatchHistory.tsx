"use client";

/**
 * The durable record of every load. Refreshes on its own: `useApi` watches
 * the revision, which the provider bumps on `batch.finished`. The button stays
 * for an API whose events are not flowing.
 *
 * With `countryCode` it lists that country's loads only - the per-country
 * upload screen must not show Colombia's files under Ecuador's flag.
 */

import { Button, Card, Chip, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { BatchSummary } from "@/lib/types";

export function BatchHistory({ countryCode }: { countryCode?: string | null }) {
  const query = countryCode
    ? `/ingest/batches?page=1&page_size=20&country=${countryCode}`
    : "/ingest/batches?page=1&page_size=20";
  const { data, loading, error, reload } = useApi<{ items: BatchSummary[]; total: number }>(
    query,
    [query],
  );

  return (
    <Card
      title="Historial de cargas"
      actions={
        <Button size="sm" variant="ghost" onClick={reload}>
          Actualizar
        </Button>
      }
    >
      {loading && <SkeletonRows rows={4} />}

      {!loading && error && <ErrorState message={error.message} onRetry={reload} />}

      {!loading && !error && (data?.items ?? []).length === 0 && (
        <EmptyState
          title="Todavía no hay cargas"
          instruction="Sube tu primer reporte arriba y aparecerá aquí con su resultado."
        />
      )}

      {!loading && (data?.items ?? []).length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-ink-dim">
                <th scope="col" className="pb-2 text-left font-semibold">Archivo</th>
                <th scope="col" className="pb-2 text-left font-semibold">Conexión</th>
                <th scope="col" className="pb-2 text-right font-semibold">Filas</th>
                <th scope="col" className="pb-2 text-right font-semibold">Nuevas</th>
                <th scope="col" className="pb-2 text-right font-semibold">Actualizadas</th>
                <th scope="col" className="pb-2 text-right font-semibold">Errores</th>
                <th scope="col" className="pb-2 text-right font-semibold">Cuándo</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((batch) => (
                <tr key={batch.batch_id} className="border-t border-line-row">
                  <td className="py-2.5 pr-3">
                    <span className="text-ink">{batch.source_name}</span>
                    {batch.status === "failed" && (
                      <Chip tone="negative" className="ml-2">
                        Falló
                      </Chip>
                    )}
                  </td>
                  <td className="py-2.5 pr-3 text-ink-muted">
                    {countryFlag(batch.country_code)} {batch.connection_name}
                  </td>
                  <td className="py-2.5 text-right text-ink-2">{batch.rows_total}</td>
                  <td className="py-2.5 text-right text-positive-ink">{batch.rows_inserted}</td>
                  <td className="py-2.5 text-right text-ink-2">{batch.rows_updated}</td>
                  <td
                    className={cx(
                      "py-2.5 text-right",
                      batch.rows_failed > 0 ? "text-negative-ink" : "text-ink-dim",
                    )}
                  >
                    {batch.rows_failed}
                  </td>
                  <td className="py-2.5 text-right text-ink-dim">
                    {formatRelative(batch.started_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
