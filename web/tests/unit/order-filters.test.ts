import { describe, expect, it } from "vitest";

import { hasOrderFilters, readOrderFilters, writeOrderFilters } from "@/lib/orders";

/**
 * Órdenes keeps its filters, page and open guide in the URL, so Back, a reload
 * or a trip to the dashboard and back return to exactly the same list.
 */
describe("order filters in the URL", () => {
  it("reads a shared link back into the same list", () => {
    const filters = readOrderFilters(
      new URLSearchParams("buscar=UX-5&estado=novedad&desde=2026-09-01&hasta=2026-09-27&abiertas=1&pagina=4&guia=abc-123"),
    );
    expect(filters).toEqual({
      search: "UX-5",
      group: "novedad",
      from: "2026-09-01",
      to: "2026-09-27",
      onlyOpen: true,
      page: 4,
      order: "abc-123",
    });
    expect(hasOrderFilters(filters)).toBe(true);
  });

  it("ignores values a hand-edited link could smuggle in", () => {
    const filters = readOrderFilters(
      new URLSearchParams("estado=perdida&desde=ayer&pagina=-3&guia=<script>&abiertas=si"),
    );
    expect(filters).toMatchObject({ group: "", from: "", page: 1, order: null, onlyOpen: false });
    expect(hasOrderFilters(filters)).toBe(false);
  });

  it("goes back to page 1 when what is asked for changes", () => {
    const now = new URLSearchParams("estado=novedad&pagina=7");
    expect(writeOrderFilters(now, { group: "devolucion" }).toString()).toBe("estado=devolucion");
    expect(writeOrderFilters(now, { search: "UX-1" }).get("pagina")).toBeNull();
  });

  it("keeps the page when a guide is opened or closed", () => {
    const now = new URLSearchParams("estado=novedad&pagina=3");
    const opened = writeOrderFilters(now, { order: "abc" });
    expect(opened.get("pagina")).toBe("3");
    expect(opened.get("guia")).toBe("abc");
    expect(writeOrderFilters(opened, { order: null }).toString()).toBe("estado=novedad&pagina=3");
  });

  it("keeps parameters it does not own and never writes empty ones", () => {
    const now = new URLSearchParams("from=2026-09-01&estado=novedad");
    const cleared = writeOrderFilters(now, { group: "", search: "  " });
    expect(cleared.toString()).toBe("from=2026-09-01");
  });
});
