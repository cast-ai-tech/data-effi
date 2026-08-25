/**
 * The vocabulary of the users screen: which side of the business a person is
 * on, and how a country list is toggled by clicking flags.
 *
 * Mirrors `core.membership.business_model` (migration 046). Kept out of the
 * page so the rules have tests: a chip with the wrong word, or a toggle that
 * drops the last country and silently widens the access to every country,
 * are both failures a screenshot would not catch.
 */

export const BUSINESS_MODELS = ["ecommerce", "proveeduria"] as const;
export type BusinessModel = (typeof BUSINESS_MODELS)[number];

export interface BusinessModelMeta {
  label: string;
  detail: string;
}

export const BUSINESS_MODEL_META: Record<BusinessModel, BusinessModelMeta> = {
  ecommerce: { label: "Ecommerce", detail: "Vende: tiendas, pauta y despachos" },
  proveeduria: { label: "Proveeduría", detail: "Surte: producto y bodega" },
};

export function isBusinessModel(value: unknown): value is BusinessModel {
  return typeof value === "string" && (BUSINESS_MODELS as readonly string[]).includes(value);
}

/** Label for a chip. Null reads as "sin modelo": the admin has not said yet. */
export function businessModelLabel(value: string | null | undefined): string {
  return isBusinessModel(value) ? BUSINESS_MODEL_META[value].label : "Sin modelo";
}

/**
 * Click a flag: add the country when it is not selected, remove it when it is.
 * Order follows the company's own country order, not the click order, so two
 * admins looking at the same person see the same list.
 */
export function toggleCountry(
  selected: readonly string[],
  code: string,
  available: readonly string[],
): string[] {
  const next = new Set(selected.map((c) => c.toUpperCase()));
  const upper = code.toUpperCase();
  if (next.has(upper)) next.delete(upper);
  else next.add(upper);
  return available.map((c) => c.toUpperCase()).filter((c) => next.has(c));
}

/**
 * What to send to the API for a country selection. An empty selection means
 * "every country of the company" - which the API expresses as clearing the
 * scope, never as an empty list (the schema refuses one).
 */
export function scopePayload(
  selected: readonly string[],
  available: readonly string[],
): { country_scope: string[] } | { clear_country_scope: true } {
  const all = available.map((c) => c.toUpperCase());
  const chosen = selected.map((c) => c.toUpperCase());
  if (chosen.length === 0 || all.every((c) => chosen.includes(c))) {
    return { clear_country_scope: true };
  }
  return { country_scope: chosen };
}

/** The countries a member may read, as the flags will show them. */
export function visibleCountries(
  scope: readonly string[] | null | undefined,
  available: readonly string[],
): string[] {
  if (!scope || scope.length === 0) return [...available];
  const upper = scope.map((c) => c.toUpperCase());
  return available.filter((c) => upper.includes(c.toUpperCase()));
}
