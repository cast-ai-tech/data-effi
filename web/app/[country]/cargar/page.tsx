"use client";

/**
 * Upload for ONE country, asking which platform the file is from.
 *
 * The operator's words: "cada país debe tener su propia sección de cargar
 * datos, y cuando se cargue se debe decir con un check de qué plataforma es".
 * This is that screen. Step one is the platform (Effi, Dropi, carga manual...);
 * step two is the file. The platforms offered are the ones the catalogue says
 * operate in this country - a different list per country, because Ecuador
 * may run Effi and Dropi while Colombia runs Dropi alone.
 *
 * THE CHECK. When a file is dropped it is first shown to `/ingest/detect`,
 * which recognises the report shapes Data Effi knows by name. If the file is
 * Effi's export and Dropi is selected, the upload is blocked here with both
 * names on screen - and the API refuses it too (`platform_mismatch`), so the
 * rule holds even for a caller that skips this page.
 *
 * NO CONNECTION TO CREATE. The API finds or creates the file-mode connection
 * for (país, plataforma) on first upload (migration 042). The connections
 * screen still lists it, and still exists for sessions, sheets and webhooks.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { BatchHistory } from "@/components/ingest/BatchHistory";
import { ACCEPTED_EXTENSIONS, JobRow, KINDS } from "@/components/ingest/UploadJobs";
import { Card, Chip, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { judgeFile, platformsForKind, shortPlatformName } from "@/lib/upload-platform";
import type { Country, CountryPlatform, DetectResult, UploadJob } from "@/lib/types";

export default function CountryUploadPage() {
  const params = useParams<{ country: string }>();
  const countryCode = (params.country ?? "").toUpperCase();

  const { data: countries } = useApi<Country[]>("/config/countries");
  const country = useMemo(
    () => (countries ?? []).find((item) => item.code === countryCode) ?? null,
    [countries, countryCode],
  );

  const platformsState = useApi<CountryPlatform[]>(
    countryCode ? `/config/platforms?country=${countryCode}` : null,
    [countryCode],
  );
  const [kind, setKind] = useState<string>("shipments");
  // The platforms offered follow the report type: guides come from Effi, Dropi
  // or a manual file; ad spend from the manual ad sheet; CS from its sheet.
  const platforms = useMemo(
    () => platformsForKind(platformsState.data, kind),
    [platformsState.data, kind],
  );

  const [platform, setPlatform] = useState<string | null>(null);
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [lastFiles, setLastFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  /** Ask the API what the first file is, before deciding anything. */
  const detect = useCallback(async (file: File): Promise<DetectResult | null> => {
    const form = new FormData();
    form.append("file", file);
    try {
      return await api.upload<DetectResult>("/ingest/detect", form);
    } catch {
      // A preview that fails must not block the upload: the job will explain
      // an unreadable file in its own words.
      return null;
    }
  }, []);

  const upload = useCallback(
    async (files: FileList | File[], reprocess = false) => {
      setError(null);
      setNotice(null);
      const picked = Array.from(files);
      if (picked.length === 0) return;
      setLastFiles(picked);

      // The check, on guide and money files: what does the file say it is?
      let chosen = platform;
      if (kind === "shipments" || kind === "movements") {
        setChecking(true);
        const detected = await detect(picked[0]);
        setChecking(false);
        const verdict = judgeFile(chosen, detected);

        if (verdict?.kind === "mismatch") {
          setError(
            `"${picked[0].name}" es un reporte de ${verdict.label}, y elegiste ` +
              `${shortPlatformName(nameOf(platforms, verdict.chosen))}. Elige ` +
              `${shortPlatformName(nameOf(platforms, verdict.detected))} arriba o sube el archivo correcto.`,
          );
          return;
        }
        if (verdict?.kind === "suggest") {
          chosen = verdict.platform;
          setPlatform(chosen);
          setNotice(`Reconocido como ${verdict.label}: se carga como ${shortPlatformName(nameOf(platforms, chosen))}.`);
        }
      }

      if (!chosen) {
        setError("Elige primero de qué plataforma es este archivo.");
        return;
      }

      const form = new FormData();
      form.append("platform_code", chosen);
      form.append("country_code", countryCode);
      form.append("kind", kind);
      if (reprocess) form.append("reprocess", "true");
      for (const file of picked) form.append("files", file);

      try {
        const response = await api.upload<{ jobs: UploadJob[] }>("/ingest/upload", form);
        setJobs((previous) => [...response.jobs, ...previous]);
        platformsState.reload();
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "No se pudo subir. Revisa tu conexión.",
        );
      }
    },
    [platform, kind, detect, platforms, countryCode, platformsState],
  );

  return (
    <AppShell>
      <header className="mb-5">
        <p className="text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
          Cargar datos
        </p>
        <h1 className="mt-1 flex items-center gap-2.5 text-2xl font-bold tracking-tight">
          <span className="text-2xl leading-none">{countryFlag(countryCode)}</span>
          {country?.name ?? countryCode}
        </h1>
        <p className="mt-1 text-sm text-ink-dim">
          Primero di de qué plataforma es el archivo; luego súbelo. Subir el mismo archivo
          dos veces no duplica nada.
        </p>
      </header>

      <Card className="mb-4">
        {platformsState.loading ? (
          <SkeletonRows rows={2} />
        ) : platformsState.error ? (
          <ErrorState message={platformsState.error.message} onRetry={platformsState.reload} />
        ) : platforms.length === 0 ? (
          <EmptyState
            title={`Ninguna plataforma opera en ${country?.name ?? countryCode}`}
            instruction="Revisa el catálogo en Configuración → Conexiones."
            action={
              <Link
                href="/connections"
                className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
              >
                Ir a Conexiones
              </Link>
            }
          />
        ) : (
          <>
            <label className="mb-4 block sm:w-1/2">
              <span className="mb-1 block text-sm font-medium text-ink-muted">
                1. Tipo de reporte
              </span>
              <select
                value={kind}
                onChange={(event) => {
                  setKind(event.target.value);
                  setPlatform(null);
                  setError(null);
                  setNotice(null);
                }}
                className="w-full rounded-control border border-line-input bg-surface px-3 py-2 text-base text-ink focus:border-accent focus:outline-none"
              >
                {KINDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="mb-4">
              <legend className="mb-1.5 block text-sm font-medium text-ink-muted">
                2. ¿De qué plataforma es el archivo?
              </legend>
              <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Plataforma del archivo">
                {platforms.map((item) => {
                  const active = platform === item.platform_code;
                  return (
                    <button
                      key={item.platform_code}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => {
                        setPlatform(item.platform_code);
                        setError(null);
                        setNotice(null);
                      }}
                      title={item.setup_hint ?? undefined}
                      className={cx(
                        "flex items-center gap-2 rounded-control border px-3.5 py-2 text-base transition-colors",
                        active
                          ? "border-accent bg-accent/[0.12] font-semibold text-ink"
                          : "border-line-strong bg-surface text-ink-2 hover:border-accent/60",
                      )}
                    >
                      <span
                        aria-hidden
                        className={cx(
                          "size-3.5 rounded-full border-2",
                          active ? "border-accent bg-accent" : "border-line-strong",
                        )}
                      />
                      {shortPlatformName(item.platform_name)}
                      {item.active_connection_count > 0 && (
                        <Chip tone="positive">con datos</Chip>
                      )}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-xs text-ink-dim">
                Las guías quedan bajo la plataforma que elijas: así el tablero separa Effi de
                Dropi. Si el archivo es un reporte conocido y no coincide, no se carga.
              </p>
            </fieldset>

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
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed px-6 py-12 text-center transition-colors focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40",
                dragging
                  ? "border-accent bg-accent/[0.06]"
                  : "border-line-input bg-sunken hover:border-line-strong",
              )}
            >
              <p className="text-md font-semibold text-ink-2">
                3. Arrastra el reporte aquí
              </p>
              <p className="text-sm text-ink-dim">
                o haz clic para elegirlo · Excel (.xlsx, .xls), CSV o TXT · hasta 25 MB cada uno
              </p>
              {checking && <p className="text-sm text-accent-ink">Revisando de qué plataforma es…</p>}
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

            {notice && (
              <p className="mt-3 rounded-control border border-accent/30 bg-accent/[0.08] px-3 py-2 text-sm text-ink-2">
                {notice}
              </p>
            )}
            {error && (
              <p
                role="alert"
                className="mt-3 rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink"
              >
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

      <BatchHistory countryCode={countryCode} />
    </AppShell>
  );
}

function nameOf(platforms: readonly CountryPlatform[], code: string): string {
  return platforms.find((item) => item.platform_code === code)?.platform_name ?? code;
}
