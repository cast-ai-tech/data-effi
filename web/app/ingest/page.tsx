"use client";

/**
 * Data upload, the classic way: pick a connection, drop the files.
 *
 * Since migration 042 each country has its own upload screen that asks WHICH
 * PLATFORM the file is from (Effi, Dropi...) and refuses a recognised report
 * loaded into the wrong one. That is the screen the operator should use for
 * guides; this one stays for the rest (ad spend, CS sheets, webhooks' manual
 * companions) and for anyone who prefers to name the connection directly.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { BatchHistory } from "@/components/ingest/BatchHistory";
import { ACCEPTED_EXTENSIONS, JobRow, KINDS } from "@/components/ingest/UploadJobs";
import { Card, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Connection, Country, UploadJob } from "@/lib/types";

export default function IngestPage() {
  const { data: connections, loading: loadingConnections, error: connectionsError, reload: reloadConnections } = useApi<Connection[]>(
    "/config/connections",
  );
  const { data: countries } = useApi<Country[]>("/config/countries");
  const [connectionId, setConnectionId] = useState<string>("");
  const [kind, setKind] = useState<string>("shipments");
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  // The File handles the operator picked, kept so "volver a procesar"
  // can resend the same bytes: a browser cannot re-read a file it
  // already uploaded.
  const [lastFiles, setLastFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const activeCountries = useMemo(
    () => (countries ?? []).filter((country) => country.is_active),
    [countries],
  );

  useEffect(() => {
    if (!connectionId && connections && connections.length > 0) {
      setConnectionId(connections[0].connection_id);
    }
  }, [connections, connectionId]);

  const upload = useCallback(
    async (files: FileList | File[], reprocess = false) => {
      if (!connectionId) {
        setError("Elige primero a qué conexión pertenecen estos archivos.");
        return;
      }
      setError(null);

      const picked = Array.from(files);
      setLastFiles(picked);

      const form = new FormData();
      form.append("connection_id", connectionId);
      form.append("kind", kind);
      if (reprocess) form.append("reprocess", "true");
      for (const file of picked) form.append("files", file);

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

      {activeCountries.length > 0 && (
        <div className="mb-4 rounded-[12px] border border-accent/25 bg-accent/[0.06] px-4 py-3 text-[12px] text-ink-2">
          <b className="text-ink">¿Guías de Effi o de Dropi?</b> Cárgalas desde la sección del
          país, donde eliges de qué plataforma es el archivo y el tablero las separa:{" "}
          {activeCountries.map((country, index) => (
            <span key={country.code}>
              {index > 0 && " · "}
              <Link
                href={`/${country.code.toLowerCase()}/cargar`}
                className="font-medium text-accent underline underline-offset-2"
              >
                {countryFlag(country.code)} {country.name}
              </Link>
            </span>
          ))}
        </div>
      )}

      <Card className="mb-4">
        {loadingConnections ? (
          <SkeletonRows rows={2} />
        ) : connectionsError ? (
          <ErrorState message={connectionsError.message} onRetry={reloadConnections} />
        ) : (connections ?? []).length === 0 ? (
          <EmptyState
            title="Todavía no tienes conexiones"
            instruction="Crea una conexión de carga manual y vuelve aquí, o carga desde la sección de tu país."
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
              role="button"
              tabIndex={0}
              aria-label="Elegir archivos para cargar"
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  inputRef.current?.click();
                }
              }}
              className={cx(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[12px] border-2 border-dashed px-6 py-12 text-center transition-colors focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40",
                dragging
                  ? "border-accent bg-accent/[0.06]"
                  : "border-line-input bg-sunken hover:border-line-strong",
              )}
            >
              <p className="text-[14px] font-semibold text-ink-2">
                Arrastra tus reportes aquí
              </p>
              <p className="text-[12px] text-ink-dim">
                o haz clic para elegirlos · Excel (.xlsx, .xls), CSV o TXT · hasta 25 MB cada uno
              </p>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPTED_EXTENSIONS}
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
              <JobRow
                key={job.id}
                job={job}
                onReprocess={
                  lastFiles.length > 0 ? () => void upload(lastFiles, true) : undefined
                }
              />
            ))}
          </div>
        </Card>
      )}

      <BatchHistory />
    </AppShell>
  );
}
