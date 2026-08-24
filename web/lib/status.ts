/**
 * The five words a status can be on screen.
 *
 * Twelve canonical statuses are the right grain for merging files and the
 * wrong one for a daily table with four columns. Effi writes "Entregada a
 * destino", Dropi writes "Entregado", and both belong in the same column; "en
 * oficina" has to count somewhere the reader can see. This is the mirror of
 * `core.status_canon.display_group` (migration 040) and of
 * `pipeline/mapping.py::DISPLAY_GROUPS`. The canonical code is never replaced
 * - a guide's own row still says "En oficina" - it is only GROUPED here.
 */

import type { Tone } from "@/components/ui";

export const STATUS_GROUPS = [
  "entregada",
  "devolucion",
  "en_camino",
  "novedad",
  "muerta",
] as const;

export type StatusGroup = (typeof STATUS_GROUPS)[number];

/** Canonical status code -> screen group. Unknown codes read as en camino. */
const GROUP_OF: Record<string, StatusGroup> = {
  created: "en_camino",
  confirmed: "en_camino",
  picked_up: "en_camino",
  in_transit: "en_camino",
  out_for_delivery: "en_camino",
  in_office: "novedad",
  delivery_issue: "novedad",
  delivered: "entregada",
  returning: "devolucion",
  returned: "devolucion",
  cancelled: "muerta",
  lost: "muerta",
};

export function statusGroupOf(statusCode: string | null | undefined): StatusGroup {
  return (statusCode && GROUP_OF[statusCode]) || "en_camino";
}

export const STATUS_GROUP_LABELS: Record<StatusGroup, string> = {
  entregada: "Entregada",
  devolucion: "Devolución",
  en_camino: "En camino",
  novedad: "Novedad",
  muerta: "Muerta",
};

/**
 * What each column counts, for the header tooltip. Written for someone who
 * has never seen the twelve canonical names.
 */
export const STATUS_GROUP_HINTS: Record<StatusGroup, string> = {
  entregada: "El cliente ya recibió el paquete.",
  devolucion: "Va de regreso o ya volvió a la bodega. La venta se perdió.",
  en_camino: "Generada, recogida, en tránsito o en reparto. Todavía puede entregarse.",
  novedad:
    "Se detuvo: el cliente no contestó, no estaba, o el paquete espera en oficina. " +
    "Con una llamada todavía se rescata.",
  muerta: "Cancelada o extraviada. No se entregó ni se devolvió.",
};

/**
 * The same colours the operator's hand-made report uses, so the screen reads
 * like the sheet they already know: green delivered, orange returns, blue in
 * transit, purple issues. `muerta` is grey - it is the absence of an outcome.
 */
export const STATUS_GROUP_TONES: Record<StatusGroup, Tone> = {
  entregada: "positive",
  devolucion: "negative",
  en_camino: "accent",
  novedad: "warning",
  muerta: "neutral",
};

/** Text colour per group, for numbers in a table cell. */
export const STATUS_GROUP_TEXT: Record<StatusGroup, string> = {
  entregada: "text-positive",
  devolucion: "text-negative",
  en_camino: "text-accent",
  novedad: "text-warning",
  muerta: "text-ink-dim",
};

/**
 * Platform brand colours for the split bar. Anything not listed gets the
 * neutral swatch: a new platform must never render as if it were Effi.
 */
export const PLATFORM_SWATCH: Record<string, string> = {
  effi: "bg-accent",
  dropi: "bg-positive",
  manual_xlsx: "bg-ink-dim",
};

export function platformSwatch(code: string): string {
  return PLATFORM_SWATCH[code] ?? "bg-track";
}
