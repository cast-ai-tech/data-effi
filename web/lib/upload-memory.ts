/**
 * What "Cargar datos" remembers so a daily upload is one drag, not three steps.
 *
 * Osvaldo uploads the same Effi guides report every day. The screen used to
 * forget the report type and the platform each time, and forgot the files
 * still processing the moment he looked at another screen - coming back, the
 * "Procesando" card was gone and nothing said whether the file had landed.
 */

import type { UploadJob } from "@/lib/types";

/** The report types the API accepts (pipeline.models.BatchKind). */
export const UPLOAD_KINDS = ["shipments", "movements", "ads", "cs"] as const;
export type UploadKind = (typeof UPLOAD_KINDS)[number];

export interface UploadChoice {
  kind: UploadKind;
  platform: string | null;
}

export function uploadChoiceKey(countryCode: string): string {
  return `masterdata.cargar.${countryCode.toUpperCase()}`;
}

export function isUploadKind(value: string | null | undefined): value is UploadKind {
  return typeof value === "string" && (UPLOAD_KINDS as readonly string[]).includes(value);
}

/**
 * The type and platform to start from.
 *
 * A `?tipo=` in the link (a dashboard card that says "faltan movimientos")
 * wins over the memory; the remembered platform only applies when it was
 * chosen for that same type - an Effi guides choice says nothing about ads.
 */
export function initialUploadChoice(
  tipoParam: string | null,
  remembered: UploadChoice | null,
): UploadChoice {
  const kind = isUploadKind(tipoParam) ? tipoParam : (remembered?.kind ?? "shipments");
  const platform = remembered && remembered.kind === kind ? remembered.platform : null;
  return { kind, platform };
}

export function readUploadChoice(countryCode: string): UploadChoice | null {
  try {
    const raw = window.localStorage.getItem(uploadChoiceKey(countryCode));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<UploadChoice> | null;
    if (!value || !isUploadKind(value.kind)) return null;
    const platform =
      typeof value.platform === "string" && /^[a-z0-9_]{1,40}$/.test(value.platform)
        ? value.platform
        : null;
    return { kind: value.kind, platform };
  } catch {
    return null;
  }
}

export function writeUploadChoice(countryCode: string, choice: UploadChoice): void {
  try {
    window.localStorage.setItem(uploadChoiceKey(countryCode), JSON.stringify(choice));
  } catch {
    // Not remembered; the screen still works.
  }
}

/** How long a failed job stays on screen after coming back to it. */
export const RECENT_FAILURE_MS = 30 * 60 * 1000;

/**
 * Which of the server's recent jobs belong on the "Procesando" card when the
 * screen opens: the ones still moving, and the ones that failed a moment ago
 * (a failure has no row in the history, so this is the only place it shows).
 * Finished ones are already in the history below.
 */
export function resumableJobs(jobs: readonly UploadJob[], now: number): UploadJob[] {
  return jobs.filter((job) => {
    if (job.status === "queued" || job.status === "processing") return true;
    if (job.status !== "failed" || !job.finished_at) return false;
    const finished = Date.parse(job.finished_at);
    return Number.isFinite(finished) && now - finished <= RECENT_FAILURE_MS;
  });
}
