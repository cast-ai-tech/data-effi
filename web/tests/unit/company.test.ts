import { describe, expect, it } from "vitest";

import { COMPANY_TYPES, COMPANY_TYPE_META, companyTypeLabel, isCompanyType } from "@/lib/company";

describe("company type (050)", () => {
  it("has the four kinds the operator listed: three stores and a supplier", () => {
    expect(COMPANY_TYPES).toEqual(["dropshipping", "own_stock", "mixed", "supplier"]);
    expect(COMPANY_TYPES.filter((t) => COMPANY_TYPE_META[t].side === "tienda")).toHaveLength(3);
    expect(COMPANY_TYPE_META.supplier.side).toBe("proveedor");
  });

  it("labels in business language and never blanks an undefined one", () => {
    expect(companyTypeLabel("dropshipping")).toBe("Tienda de dropshipping");
    expect(companyTypeLabel("supplier")).toBe("Proveedor");
    expect(companyTypeLabel(null)).toBe("Tipo sin definir");
    expect(isCompanyType("ecommerce")).toBe(false);
  });
});
