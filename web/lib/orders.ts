/**
 * The vocabulary the orders and customers screens share.
 *
 * It lives outside the pages for two reasons: the widgets need the same
 * maturity sentence the widgets' cards show, and this is the logic that
 * deserves tests - a grade rendered with the wrong colour, or a hidden phone
 * rendered as an empty cell, are both failures a screenshot would not catch.
 */

import { formatPercent, parseIsoDate } from "@/lib/format";
import type { ContributionSplit, CustomerGrade } from "@/lib/types";

/** Same five tones the shared `Chip` and `StatusDot` understand. */
export type Tone = "neutral" | "accent" | "positive" | "warning" | "negative";

// ---------------------------------------------------------------------------
// Customer grade
// ---------------------------------------------------------------------------

export interface GradeMeta {
  label: string;
  tone: Tone;
  /** Why the customer landed in this grade, in the operator's words. */
  explanation: string;
}

const GRADE_META: Record<CustomerGrade, GradeMeta> = {
  excelente: {
    label: "Excelente",
    tone: "positive",
    explanation:
      "Tres pedidos o más, todos recibidos y ninguno devuelto. A este cliente le puedes despachar sin pensarlo.",
  },
  bueno: {
    label: "Bueno",
    tone: "positive",
    explanation: "Recibe 8 de cada 10 pedidos o más. Es un cliente confiable.",
  },
  regular: {
    label: "Regular",
    tone: "warning",
    explanation:
      "Recibe entre la mitad y el 80% de lo que le mandas. Vale la pena confirmarle por teléfono antes de despachar.",
  },
  riesgo: {
    label: "Riesgo",
    tone: "negative",
    explanation:
      "Devuelve más de la mitad de lo que le despachas. Cada envío a este cliente arranca perdiendo el flete de ida y el de vuelta.",
  },
  nuevo: {
    label: "Nuevo",
    tone: "neutral",
    explanation:
      "Todavía no tiene dos pedidos cerrados. No es bueno ni malo: aún no hay con qué juzgarlo.",
  },
};

/** Worst first: the operator opens this screen to find who is bleeding them. */
export const GRADE_ORDER: CustomerGrade[] = [
  "riesgo",
  "regular",
  "bueno",
  "excelente",
  "nuevo",
];

const UNKNOWN_GRADE: GradeMeta = {
  label: "Sin clasificar",
  tone: "neutral",
  explanation:
    "El servidor devolvió una calidad que esta versión de la interfaz no conoce todavía.",
};

/**
 * The chip for a customer grade.
 *
 * Takes a plain string rather than the union: `customer_grade` is computed by a
 * CASE in SQL, and a value added there before a release lands here must render
 * as "sin clasificar" instead of crashing the row or, worse, painting itself
 * with whatever colour happens to be first.
 */
export function gradeMeta(grade: string | null | undefined): GradeMeta {
  if (!grade) return UNKNOWN_GRADE;
  return GRADE_META[grade as CustomerGrade] ?? UNKNOWN_GRADE;
}

// ---------------------------------------------------------------------------
// Shipment status
// ---------------------------------------------------------------------------
// The status chip and the "Estado" picker use the five words of
// `@/lib/status` (statusGroupMeta / STATUS_GROUPS) since migration 045; the
// four coarse buckets no longer reach the screen.

/**
 * How worried to be about a guide that has been open this long.
 *
 * Same thresholds as the aging buckets: past 13 days the money is at risk,
 * past 21 it is usually gone. A closed guide gets no colour at all - its days
 * are history, not exposure.
 */
export function daysOpenTone(
  days: number | null | undefined,
  isTerminal: boolean,
): Tone {
  if (isTerminal || days === null || days === undefined) return "neutral";
  if (days >= 21) return "negative";
  if (days >= 13) return "warning";
  return "neutral";
}

// ---------------------------------------------------------------------------
// Contact data, and why it might not be there
// ---------------------------------------------------------------------------

export interface ContactLabel {
  /** Always something readable. Never an empty string. */
  primary: string;
  /** The phone, when there is one to show under the name. */
  secondary: string | null;
  /** True when the server refused to decrypt, false when there was nothing. */
  hidden: boolean;
}

/**
 * What to write in the customer cell.
 *
 * The two reasons a name is missing are NOT the same thing and must not look
 * the same. If the server hid it, the reference code is the identity and the
 * banner above the table explains the policy. If the guide simply never
 * carried a name, that is a gap in the uploaded file and the operator can fix
 * it at the source. Rendering both as a blank cell teaches the operator that
 * the system lost their data, which is the one conclusion that is never true.
 */
export function contactLabel(
  row: {
    customer_name: string | null;
    customer_phone: string | null;
    customer_ref: string | null;
  },
  piiVisible: boolean,
): ContactLabel {
  const reference = row.customer_ref?.trim() ?? "";
  const fallback = reference === "" ? "Sin identificar" : reference;

  if (!piiVisible) {
    return { primary: fallback, secondary: null, hidden: true };
  }

  const name = row.customer_name?.trim() ?? "";
  const phone = row.customer_phone?.trim() ?? "";

  if (name !== "") return { primary: name, secondary: phone || null, hidden: false };
  if (phone !== "") return { primary: phone, secondary: null, hidden: false };

  return { primary: fallback, secondary: null, hidden: false };
}

/**
 * The banner shown once above a table whose contact data is hidden.
 *
 * It covers both causes because the API answers with a single boolean, and
 * naming only one of them would be a guess presented as a fact.
 */
export const PII_HIDDEN_NOTICE =
  "No estás viendo nombres ni teléfonos: o tu rol es de solo lectura, o este " +
  "servidor no tiene la llave para descifrarlos. Los datos están completos y " +
  "guardados; solo no se muestran acá. Cada cliente sigue identificado por su " +
  "código (por ejemplo #A47F3C), que es el mismo en todos sus pedidos.";

/**
 * Contact data is missing because nobody ever uploaded it.
 *
 * This is NOT the same sentence as the one above and must never be swapped for
 * it. "No tienes permiso" tells the operator to ask an owner; this one tells
 * them to re-upload a file. Showing the wrong one sends them to argue with the
 * wrong person.
 */
export const PII_MISSING_NOTICE =
  "Estas guías no traen nombre ni teléfono. O el archivo que subiste no traía " +
  "esas columnas, o se cargó antes de que el sistema empezara a guardar datos " +
  "de contacto, y en ese caso ya no se pueden recuperar de lo que está " +
  "guardado. Vuelve a subir los reportes para verlos. Mientras tanto cada " +
  "cliente sigue identificado por su código (por ejemplo #A47F3C), que es el " +
  "mismo en todos sus pedidos.";

/** Which of the two explanations a table owes its reader, if either. */
export type ContactNotice = "hidden" | "missing" | null;

/**
 * Decide which sentence goes above the table.
 *
 * `missing` is only claimed when NOT ONE visible row carries contact data:
 * that is a systemic gap worth a banner. A column that is merely patchy needs
 * no announcement - each of those cells still shows the customer's code rather
 * than going blank, which is what the rule against empty cells is actually
 * about.
 */
export function contactNotice(
  rows: Array<{ customer_name: string | null; customer_phone: string | null }>,
  piiVisible: boolean,
): ContactNotice {
  if (!piiVisible) return "hidden";
  if (rows.length === 0) return null;

  const anyContact = rows.some(
    (row) =>
      (row.customer_name?.trim() ?? "") !== "" ||
      (row.customer_phone?.trim() ?? "") !== "",
  );
  return anyContact ? null : "missing";
}

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

/**
 * How long a finished guide took to arrive.
 *
 * `days_open` is null once a guide closes - it measures exposure, and a closed
 * guide has none. But "days" is still the useful number in that column, so it
 * is recomputed from the two dates rather than left as a dash. Returns null
 * when the guide never arrived, or when the dates are impossible: a delivery
 * before its own dispatch is bad data, and inventing a negative from it would
 * only launder it into the UI.
 */
export function daysToDeliver(
  createdDate: string | null | undefined,
  deliveredAt: string | null | undefined,
): number | null {
  if (!createdDate || !deliveredAt) return null;

  const from = parseIsoDate(createdDate);
  const to = parseIsoDate(deliveredAt);
  if (!from || !to) return null;

  const days = Math.floor((to.getTime() - from.getTime()) / 86_400_000);
  return days < 0 ? null : days;
}

/** How many guides `GET /customers/{hash}` will ever return. */
export const CUSTOMER_ORDERS_CAP = 200;

// ---------------------------------------------------------------------------
// Maturity
// ---------------------------------------------------------------------------

/** Below this share of closed guides, the realised figure is a small sample. */
export const MATURITY_FLOOR_PCT = 60;

/**
 * The sentence under a realised-contribution figure, when it needs one.
 *
 * Deliberately not a warning: nothing is wrong when half the guides are still
 * travelling - that is what a young operation looks like. What would be wrong
 * is reading the closed figure as if it were the whole month, so the sentence
 * says what the number covers and what happens next, and returns null the rest
 * of the time rather than nagging.
 */
export function maturityNotice(maturityPct: number | null | undefined): string | null {
  if (maturityPct === null || maturityPct === undefined) return null;
  if (!Number.isFinite(maturityPct)) return null;
  if (maturityPct >= MATURITY_FLOOR_PCT) return null;

  return (
    `Hasta ahora ha cerrado el ${formatPercent(maturityPct)} de las guías. ` +
    "La contribución cerrada se calcula solo sobre esa parte; el resto sigue " +
    "en la calle y se irá sumando a medida que las guías terminen."
  );
}

// ---------------------------------------------------------------------------
// Pagination arithmetic
// ---------------------------------------------------------------------------

export interface PageWindow {
  /** 1-based index of the first row on this page. 0 when there are none. */
  first: number;
  last: number;
  total: number;
  pageCount: number;
  hasPrevious: boolean;
  hasNext: boolean;
}

export function pageWindow(page: number, pageSize: number, total: number): PageWindow {
  const pageCount = pageSize > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = total === 0 ? 0 : Math.min(page * pageSize, total);
  return {
    first,
    last,
    total,
    pageCount,
    hasPrevious: page > 1,
    hasNext: page < pageCount,
  };
}

// ---------------------------------------------------------------------------
// Contribution split
// ---------------------------------------------------------------------------

/**
 * The one row of `mart.v_contribution_split` this country is about.
 *
 * The endpoint answers with a single object, but every other `/kpis/*` route in
 * this API answers with a list, so both shapes are accepted here. Picking the
 * matching country rather than blindly taking the first element is what keeps
 * it correct if the country filter is ever dropped: these are sums in different
 * currencies and adding them together would be nonsense.
 */
export function pickSplit(
  data: ContributionSplit | ContributionSplit[] | null | undefined,
  countryCode: string,
): ContributionSplit | null {
  if (!data) return null;
  if (!Array.isArray(data)) return data;
  if (data.length === 0) return null;
  return data.find((row) => row.country_code === countryCode) ?? data[0];
}
