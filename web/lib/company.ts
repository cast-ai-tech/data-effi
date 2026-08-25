/**
 * What kind of company this is - the answer to "¿Tienda o Proveedor?" asked
 * when the company is created (migration 050).
 *
 * The four values are the ones the operator listed: a dropshipping store, a
 * store with its own stock, a mixed one, or a supplier. The label is what the
 * person sees; the hint is the one-line explanation under it.
 */

export const COMPANY_TYPES = ["dropshipping", "own_stock", "mixed", "supplier"] as const;

export type CompanyType = (typeof COMPANY_TYPES)[number];

export interface CompanyTypeMeta {
  label: string;
  hint: string;
  /** "tienda" or "proveedor": the top-level check the operator asked for. */
  side: "tienda" | "proveedor";
}

export const COMPANY_TYPE_META: Record<CompanyType, CompanyTypeMeta> = {
  dropshipping: {
    label: "Tienda de dropshipping",
    hint: "Vendes productos de proveedores y ellos despachan.",
    side: "tienda",
  },
  own_stock: {
    label: "Tienda con mercancía propia",
    hint: "Compras tu inventario y despachas tú.",
    side: "tienda",
  },
  mixed: {
    label: "Tienda mixta",
    hint: "Parte dropshipping, parte mercancía propia.",
    side: "tienda",
  },
  supplier: {
    label: "Proveedor",
    hint: "Surtes y despachas los pedidos de otras tiendas.",
    side: "proveedor",
  },
};

export function isCompanyType(value: unknown): value is CompanyType {
  return typeof value === "string" && (COMPANY_TYPES as readonly string[]).includes(value);
}

export function companyTypeLabel(value: unknown): string {
  return isCompanyType(value) ? COMPANY_TYPE_META[value].label : "Tipo sin definir";
}
