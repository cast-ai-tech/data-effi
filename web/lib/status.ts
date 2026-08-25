/**
 * The five words a status can be on screen.
 *
 * Thirteen canonical statuses are the right grain for merging files and the
 * wrong one for a daily table. Effi writes "Entregada a destino", Dropi writes
 * "Entregado", and both belong in the same column; "en oficina" has to count
 * somewhere the reader can see. This is the mirror of
 * `core.status_canon.status_group` (migration 045) and of
 * `pipeline/mapping.py::STATUS_GROUPS`. The canonical code is never replaced
 * - a guide's own row still says "En oficina" - it is only GROUPED here.
 */

import type { Tone } from "@/components/ui";

/** In the order the operator reads them. */
export const STATUS_GROUPS = [
  "entregada",
  "en_transito",
  "novedad",
  "devolucion",
  "indemnizacion",
] as const;

export type StatusGroup = (typeof STATUS_GROUPS)[number];

/** Canonical status code -> screen group. Unknown codes read as en tránsito. */
const GROUP_OF: Record<string, StatusGroup> = {
  created: "en_transito",
  confirmed: "en_transito",
  picked_up: "en_transito",
  in_transit: "en_transito",
  out_for_delivery: "en_transito",
  in_office: "novedad",
  delivery_issue: "novedad",
  delivered: "entregada",
  returning: "devolucion",
  returned: "devolucion",
  // The operator's decision (045): the sale is lost and the product is back.
  cancelled: "devolucion",
  // A siniestro is an indemnity still owed; compensated is the same parcel paid.
  lost: "indemnizacion",
  compensated: "indemnizacion",
};

export function statusGroupOf(statusCode: string | null | undefined): StatusGroup {
  return (statusCode && GROUP_OF[statusCode]) || "en_transito";
}

export function isStatusGroup(value: unknown): value is StatusGroup {
  return typeof value === "string" && (STATUS_GROUPS as readonly string[]).includes(value);
}

export const STATUS_GROUP_LABELS: Record<StatusGroup, string> = {
  entregada: "Entregado",
  en_transito: "En tránsito",
  novedad: "Novedad",
  devolucion: "Devolución",
  indemnizacion: "Indemnización",
};

/**
 * What each column counts, for the header tooltip. Written for someone who
 * has never seen the thirteen canonical names.
 */
export const STATUS_GROUP_HINTS: Record<StatusGroup, string> = {
  entregada: "El cliente ya recibió el paquete.",
  en_transito: "Generada, recogida, en tránsito o en reparto. Todavía puede entregarse.",
  novedad:
    "Se detuvo: el cliente no contestó, no estaba, o el paquete espera en oficina. " +
    "Con una llamada todavía se rescata.",
  devolucion:
    "Va de regreso, ya volvió a la bodega o la cancelaron. La venta se perdió.",
  indemnizacion:
    "La transportadora perdió el paquete. Te debe el valor, o ya te lo pagó.",
};

/**
 * The same colours the operator's hand-made report uses, so the screen reads
 * like the sheet they already know: green delivered, blue in transit, amber
 * issues, red returns. `indemnizacion` is grey: the parcel is gone, the money
 * is a separate conversation.
 */
export const STATUS_GROUP_TONES: Record<StatusGroup, Tone> = {
  entregada: "positive",
  en_transito: "accent",
  novedad: "warning",
  devolucion: "negative",
  indemnizacion: "neutral",
};

/** Text colour per group, for numbers in a table cell. */
export const STATUS_GROUP_TEXT: Record<StatusGroup, string> = {
  entregada: "text-positive-ink",
  en_transito: "text-accent-ink",
  novedad: "text-warning-ink",
  devolucion: "text-negative-ink",
  indemnizacion: "text-ink-dim",
};

export interface StatusGroupMeta {
  label: string;
  tone: Tone;
}

/**
 * Label and tone for a chip. An unknown value is echoed rather than hidden:
 * a guide whose group the screen does not know must still say something.
 */
export function statusGroupMeta(group: string | null | undefined): StatusGroupMeta {
  if (!group) return { label: "Sin estado", tone: "neutral" };
  if (isStatusGroup(group)) {
    return { label: STATUS_GROUP_LABELS[group], tone: STATUS_GROUP_TONES[group] };
  }
  return { label: group, tone: "neutral" };
}

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
