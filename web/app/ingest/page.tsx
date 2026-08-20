"use client";

/**
 * Data upload.
 *
 * Every file becomes a live row: name, detected source, progress, and then a
 * result the user can act on. A failed file says WHY in words, not a status
 * code - "posible archivo en centavos" is worth more than "422".
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Button, Card, Chip, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag, formatBytes, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { BatchDetail, BatchSummary, Connection, UploadJob } from "@/lib/types";

const KINDS = [
  { value: "shipments", label: "Guías" },
  { value: "movements", label: "Movimientos de dinero" },
  { value: "cs", label: "Confirmación de servicio" },
] as const;

export default function IngestPage() {
  const { data: connections, loading: loadingConnections } = useApi<Connection[]>(
    "/config/connections",
  );

  const [connectionId, setConnectionId] = useState<string>("");
  const [kind, setKind] = useState<string>("shipments");
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!connectionId && connections && connections.length > 0) {
      setConnectionId(connections[0].connection_id);
    }
  }, [connections, connectionId]);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      if (!connectionId) {
        setError("Elige primero a qué conexión pertenecen estos archivos.");
        return;
      }
      setError(null);

      const form = new FormData();
      form.append("connection_id", connectionId);
      form.append("kind", kind);
      for (const file of Array.from(files)) form.append("files", file);

      try {
        const response = await api.post<{ jobs: UploadJob[] }>("/ingest/upload", form);
        setJobs((previous) => [...response.jobs, ...previous]);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "No se pudo subir. Revisa tu conexión.",
        );
      }
    },
    [connectionId, kind],
  );

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-[22px] font-bold tracking-tight">Cargar datos</h1>
        <p className="mt-1 text-[12px] text-ink-dim">
          Excel o CSV. Subir el mismo archivo dos veces no duplica nada.
        </p>
      </header>

      <Card className="mb-4">
        {loadingConnections ? (
          <SkeletonRows rows={2} />
        ) : (connections ?? []).length === 0 ? (
          <EmptyState
            title="Todavía no tienes conexiones"
            instruction="Crea una conexión de carga manual y vuelve aquí."
            action={
              <Link
                href="/settings"
                className="rounded-[8px] bg-accent px-3.5 py-2 text-[12px] font-semibold text-on-accent no-underline"
              >
                Ir a Configuración
              </Link>
            }
          />
        ) : (
          <>
            <div className="mb-4 grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[11.5px] font-medium text-ink-muted">
                  Conexión
                </span>
                <select
                  value={connectionId}
                  onChange={(event) => setConnectionId(event.target.value)}
                  className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none"
                >
                  {(connections ?? []).map((connection) => (
                    <option key={connection.connection_id} value={connection.connection_id}>
                      {countryFlag(connection.country_code)} {connection.connection_name} ·{" "}
                      {connection.platform_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block text-[11.5px] font-medium text-ink-muted">
                  Tipo de reporte
                </span>
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                  className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px] text-ink focus:border-accent focus:outline-none"
                >
                  {KINDS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                void upload(event.dataTransfer.files);
              }}
              onClick={() => inputRef.current?.click()}
              className={cx(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[12px] border-2 border-dashed px-6 py-12 text-center transition-colors",
                dragging
                  ? "border-accent bg-accent/[0.06]"
                  : "border-line-input bg-sunken hover:border-line-strong",
              )}
            >
              <p className="text-[14px] font-semibold text-ink-2">
                Arrastra tus reportes aquí
              </p>
              <p className="text-[12px] text-ink-dim">
                o haz clic para elegirlos · .csv, .xlsx · hasta 25 MB cada uno
              </p>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept=".csv,.xlsx,.xlsm,.txt,.tsv"
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) void upload(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>

            {error && (
              <p className="mt-3 rounded-[8px] border border-negative/30 bg-negative/[0.08] px-3 py-2 text-[12px] text-negative">
                {error}
              </p>
            )}
          </>
        )}
      </Card>

      {jobs.length > 0 && (
        <Card title="Procesando" className="mb-4">
          <div className="space-y-0">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        </Card>
      )}

      <BatchHistory />
    </AppShell>
  );
}

function JobRow({ job: initial }: { job: UploadJob }) {
  const [job, setJob] = useState(initial);
  const finished = ["done", "failed", "duplicate"].includes(job.status);

  useEffect(() => {
    if (finished) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const next = await api.get<UploadJob>(`/ingest/jobs/${job.id}`);
        if (cancelled) return;
        setJob(next);
        if (!["done", "failed", "duplicate"].includes(next.status)) {
          setTimeout(poll, 900);
        }
      } catch {
        // Give up quietly; the history table below is the durable record.
      }
    };

    const timer = setTimeout(poll, 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job.id, finished]);

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
          <p className="truncate text-[12.5px] font-medium text-ink">{job.filename}</p>
          <p className="text-[11px] text-ink-dim">
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
        <p className="mt-2 rounded-[8px] border border-negative/25 bg-negative/[0.06] px-3 py-2 text-[11.5px] leading-relaxed text-negative-soft">
          {job.error}
        </p>
      )}

      {job.status === "duplicate" && (
        <p className="mt-1.5 text-[11.5px] text-ink-dim">
          Mismo contenido que una carga anterior. No se insertó nada, que es lo correcto.
        </p>
      )}

      {job.status === "done" && job.batch_id && <BatchResult batchId={job.batch_id} />}
    </div>
  );
}

function BatchResult({ batchId }: { batchId: string }) {
  const { data } = useApi<BatchDetail>(`/ingest/batches/${batchId}`);
  if (!data) return null;

  const { batch, report } = data;
  const unmapped = report.unmapped_columns ?? [];
  const sanity = report.sanity_issues ?? [];

  return (
    <div className="mt-2 space-y-1.5">
      {report.profile?.label && (
        <p className="text-[11.5px] text-accent">
          Detectado: {report.profile.label}
        </p>
      )}
      <p className="text-[11.5px] text-ink-2">
        {batch.rows_total} filas · <b className="text-positive">{batch.rows_inserted} nuevas</b> ·{" "}
        {batch.rows_updated} actualizadas · {batch.rows_skipped} sin cambios ·{" "}
        <b className={batch.rows_failed > 0 ? "text-negative" : ""}>
          {batch.rows_failed} con error
        </b>
      </p>

      {batch.discrepancy_count > 0 && (
        <p className="text-[11px] text-warning">
          {batch.discrepancy_count} valores de dinero cambiaron respecto a una carga previa.
          Se guardó el rastro.
        </p>
      )}

      {unmapped.length > 0 && (
        <details className="text-[11px] text-ink-dim">
          <summary className="cursor-pointer hover:text-ink-muted">
            {unmapped.length} columnas del archivo no se usaron
          </summary>
          <p className="mt-1 leading-relaxed">{unmapped.join(" · ")}</p>
        </details>
      )}

      {sanity.slice(0, 3).map((issue, index) => (
        <p key={index} className="text-[11px] text-warning">
          Fila {issue.row}: {issue.message}
        </p>
      ))}
      {sanity.length > 3 && (
        <p className="text-[11px] text-ink-dim">y {sanity.length - 3} avisos más</p>
      )}
    </div>
  );
}

function BatchHistory() {
  const { data, loading, reload } = useApi<{ items: BatchSummary[]; total: number }>(
    "/ingest/batches?page=1&page_size=20",
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

      {!loading && (data?.items ?? []).length === 0 && (
        <EmptyState
          title="Todavía no hay cargas"
          instruction="Sube tu primer reporte arriba y aparecerá aquí con su resultado."
        />
      )}

      {!loading && (data?.items ?? []).length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-[12px]">
            <thead>
              <tr className="text-[10.5px] uppercase tracking-wide text-ink-dim">
                <th className="pb-2 text-left font-semibold">Archivo</th>
                <th className="pb-2 text-left font-semibold">Conexión</th>
                <th className="pb-2 text-right font-semibold">Filas</th>
                <th className="pb-2 text-right font-semibold">Nuevas</th>
                <th className="pb-2 text-right font-semibold">Actualizadas</th>
                <th className="pb-2 text-right font-semibold">Errores</th>
                <th className="pb-2 text-right font-semibold">Cuándo</th>
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
                  <td className="py-2.5 text-right text-positive">{batch.rows_inserted}</td>
                  <td className="py-2.5 text-right text-ink-2">{batch.rows_updated}</td>
                  <td
                    className={cx(
                      "py-2.5 text-right",
                      batch.rows_failed > 0 ? "text-negative" : "text-ink-dim",
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

      <p className="mt-4 rounded-[8px] border border-line bg-sunken px-3.5 py-2.5 text-[11.5px] leading-relaxed text-ink-dim">
        <b className="text-ink-2">Recibir por correo:</b> próximamente podrás reenviar los
        reportes a un buzón de tu cuenta y se cargarán solos. Por ahora, la subida manual
        y las conexiones automáticas cubren el mismo camino.
      </p>
    </Card>
  );
}
