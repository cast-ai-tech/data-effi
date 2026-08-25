"use client";

/**
 * The live rows of an upload: name, progress, and then a result the operator
 * can act on. Shared by the global upload screen and the per-country one, so
 * a failed file explains itself the same way on both.
 */

import { useEffect, useState } from "react";

import { Chip } from "@/components/ui";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useNotifications } from "@/lib/notifications";
import type { BatchDetail, UploadJob } from "@/lib/types";

/**
 * How long a row waits for a live event before asking the API itself. The
 * events are the normal path; this is the net under them for an API that is
 * asleep or an event that was emitted before the row subscribed.
 */
const JOB_FALLBACK_MS = 15_000;

export const TERMINAL_STATUSES: ReadonlySet<UploadJob["status"]> = new Set([
  "done",
  "failed",
  "duplicate",
]);

// Same four the API accepts (pipeline.models.BatchKind).
export const KINDS = [
  { value: "shipments", label: "Guías" },
  { value: "movements", label: "Movimientos de dinero" },
  { value: "ads", label: "Inversión en pauta" },
  { value: "cs", label: "Confirmación de servicio" },
] as const;

// Mirrors pipeline.readers.SUPPORTED_EXTENSIONS. The picker must not refuse a
// file the API would accept, or the reverse.
export const ACCEPTED_EXTENSIONS = ".csv,.xlsx,.xlsm,.xls,.txt,.tsv,.html,.htm";

export function JobRow({
  job: initial,
  onReprocess,
}: {
  job: UploadJob;
  onReprocess?: () => void;
}) {
  const [job, setJob] = useState(initial);
  const finished = TERMINAL_STATUSES.has(job.status);
  const { subscribe } = useNotifications();

  // The row moves when the API says so: `upload_job.updated` carries the new
  // status, the batch id and the error text, which is everything the row
  // shows. No polling loop per file.
  useEffect(() => {
    if (finished) return;
    return subscribe("upload_job.updated", (event) => {
      if (event.payload.job_id !== job.id) return;
      const status = event.payload.status as UploadJob["status"] | undefined;
      if (!status) return;
      setJob((current) => ({
        ...current,
        status,
        batch_id:
          typeof event.payload.batch_id === "string" ? event.payload.batch_id : current.batch_id,
        error: typeof event.payload.error === "string" ? event.payload.error : current.error,
        finished_at: TERMINAL_STATUSES.has(status)
          ? (current.finished_at ?? event.created_at)
          : current.finished_at,
      }));
    });
  }, [subscribe, job.id, finished]);

  // The net: if no event has moved this row in fifteen seconds, ask directly,
  // and keep asking at the same pace until it lands somewhere terminal.
  const [fallbackTick, setFallbackTick] = useState(0);
  useEffect(() => {
    if (finished) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const next = await api.get<UploadJob>(`/ingest/jobs/${job.id}`);
        if (cancelled) return;
        setJob(next);
        if (!TERMINAL_STATUSES.has(next.status)) setFallbackTick((n) => n + 1);
      } catch {
        // Give up quietly; the history table below is the durable record.
      }
    }, JOB_FALLBACK_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // `job.status` is in the deps on purpose: every change restarts the
    // fifteen-second clock, so the fallback only fires when nothing happened.
  }, [job.id, job.status, finished, fallbackTick]);

  const tone =
    job.status === "done"
      ? "positive"
      : job.status === "failed"
        ? "negative"
        : job.status === "duplicate"
          ? "warning"
          : "accent";

  const label =
    job.status === "queued"
      ? "En cola"
      : job.status === "processing"
        ? "Procesando…"
        : job.status === "done"
          ? "Listo"
          : job.status === "duplicate"
            ? "Ya estaba cargado"
            : "Falló";

  return (
    <div className="border-t border-line-row py-3 first:border-t-0">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-base font-medium text-ink">{job.filename}</p>
          <p className="text-xs text-ink-dim">
            {formatBytes(job.size_bytes)} · {job.kind}
          </p>
        </div>
        <Chip tone={tone}>{label}</Chip>
      </div>

      {!finished && (
        <div className="mt-2 h-[4px] overflow-hidden rounded-full bg-track">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-accent" />
        </div>
      )}

      {job.status === "failed" && job.error && (
        <p className="mt-2 rounded-control border border-negative/25 bg-negative/[0.06] px-3 py-2 text-sm leading-relaxed text-negative-soft">
          {job.error}
        </p>
      )}

      {job.status === "duplicate" && (
        <div className="mt-1.5 space-y-1.5">
          <p className="text-sm leading-relaxed text-ink-dim">
            Mismo contenido que una carga anterior, así que no se insertó nada. Eso es lo
            que evita que subir dos veces el mismo archivo duplique tus cifras.
          </p>
          {onReprocess && (
            <p className="text-sm leading-relaxed text-ink-dim">
              Si Master Data cambió desde entonces —por ejemplo al corregir cómo se lee una
              columna— el mismo archivo puede producir datos distintos.{" "}
              <button
                type="button"
                onClick={onReprocess}
                className="font-medium text-accent-ink underline underline-offset-2"
              >
                Vuelve a procesarlo
              </button>{" "}
              y se reemplaza lo cargado antes, sin duplicar.
            </p>
          )}
        </div>
      )}

      {job.status === "done" && job.batch_id && <BatchResult batchId={job.batch_id} />}
    </div>
  );
}

export function BatchResult({ batchId }: { batchId: string }) {
  const { data } = useApi<BatchDetail>(`/ingest/batches/${batchId}`);
  if (!data) return null;

  const { batch, report } = data;
  const unmapped = report.unmapped_columns ?? [];
  const sanity = report.sanity_issues ?? [];

  return (
    <div className="mt-2 space-y-1.5">
      {report.profile?.label && (
        <p className="text-sm text-accent-ink">Detectado: {report.profile.label}</p>
      )}
      <p className="text-sm text-ink-2">
        {batch.rows_total} filas · <b className="text-positive-ink">{batch.rows_inserted} nuevas</b> ·{" "}
        {batch.rows_updated} actualizadas · {batch.rows_skipped} sin cambios ·{" "}
        <b className={batch.rows_failed > 0 ? "text-negative-ink" : ""}>
          {batch.rows_failed} con error
        </b>
      </p>

      {batch.discrepancy_count > 0 && (
        <p className="text-xs text-warning-ink">
          {batch.discrepancy_count} valores de dinero cambiaron respecto a una carga previa.
          Se guardó el rastro.
        </p>
      )}

      {unmapped.length > 0 && (
        <details className="text-xs text-ink-dim">
          <summary className="cursor-pointer hover:text-ink-muted">
            {unmapped.length} columnas del archivo no se usaron
          </summary>
          <p className="mt-1 leading-relaxed">{unmapped.join(" · ")}</p>
        </details>
      )}

      {sanity.slice(0, 3).map((issue, index) => (
        <p key={index} className="text-xs text-warning-ink">
          Fila {issue.row}: {issue.message}
        </p>
      ))}
      {sanity.length > 3 && (
        <p className="text-xs text-ink-dim">y {sanity.length - 3} avisos más</p>
      )}
    </div>
  );
}
