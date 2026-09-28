import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  FILTER_MEMORY_KEY,
  captureFilters,
  filterQuery,
  hasFilterParams,
  isDashboardPath,
  readRememberedFilters,
  useFilterMemory,
  writeRememberedFilters,
} from "@/lib/filter-memory";

/**
 * Osvaldo narrows Colombia to the last 7 days, opens Órdenes to look at a
 * guide and clicks "Tablero": he must land on the last 7 days, on the tab he
 * left - not on the whole history. Same the next morning after signing in.
 */

const nav = vi.hoisted(() => ({
  pathname: "/co",
  search: new URLSearchParams(),
  replace: vi.fn(),
  // Next's router is stable across renders; a fresh object each time would
  // re-run every effect that depends on it.
  router: null as unknown,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.search,
  useRouter: () => (nav.router ??= { replace: nav.replace, push: vi.fn() }),
}));

const MONDAY = new Date(2026, 8, 28); // 2026-09-28

describe("filter memory - pure rules", () => {
  it("knows which screens are dashboards", () => {
    expect(isDashboardPath("/global")).toBe(true);
    expect(isDashboardPath("/co")).toBe(true);
    expect(isDashboardPath("/co/orders")).toBe(false);
    expect(isDashboardPath("/connections")).toBe(false);
  });

  it("stores a shortcut as the shortcut, so tomorrow it still means 'the last 7 days'", () => {
    const search = new URLSearchParams("from=2026-09-21&to=2026-09-27&tab=logistica");
    const remembered = captureFilters("/co", search, MONDAY);
    expect(remembered.preset).toBe("7d");
    expect(remembered.tab).toBe("logistica");

    const tuesday = new Date(2026, 8, 29);
    expect(filterQuery(remembered, tuesday, true)).toBe(
      "?from=2026-09-22&to=2026-09-28&tab=logistica",
    );
  });

  it("stores a hand-picked range as its dates", () => {
    const search = new URLSearchParams("from=2026-08-03&to=2026-08-19&platform=dropi&statuses=novedad");
    const remembered = captureFilters("/co", search, MONDAY);
    expect(remembered.preset).toBeNull();
    expect(filterQuery(remembered, new Date(2027, 0, 1))).toBe(
      "?from=2026-08-03&to=2026-08-19&platform=dropi&statuses=novedad",
    );
  });

  it("keeps the country tab when the filters are captured on /global", () => {
    const previous = captureFilters("/co", new URLSearchParams("tab=servicio"), MONDAY);
    const onGlobal = captureFilters("/global", new URLSearchParams("from=2026-09-28&to=2026-09-28"), MONDAY, previous);
    expect(onGlobal.preset).toBe("hoy");
    expect(onGlobal.tab).toBe("servicio");
  });

  it("carries nothing for 'Máximo' and only adds the tab on request", () => {
    const remembered = captureFilters("/co", new URLSearchParams("tab=finanzas"), MONDAY);
    expect(filterQuery(remembered, MONDAY)).toBe("");
    expect(filterQuery(remembered, MONDAY, true)).toBe("?tab=finanzas");
  });

  it("drops junk from a tampered or corrupt entry instead of forwarding it", () => {
    window.localStorage.setItem(
      FILTER_MEMORY_KEY,
      JSON.stringify({ preset: "siempre", from: "2026-02-31", platform: "x; drop", statuses: "hackeo", tab: "<b>" }),
    );
    expect(readRememberedFilters()).toEqual({
      preset: "maximo",
      from: null,
      to: null,
      platform: null,
      statuses: null,
      tab: null,
    });
    window.localStorage.setItem(FILTER_MEMORY_KEY, "{not json");
    expect(readRememberedFilters()).toBeNull();
  });

  it("only counts the filter parameters, not the tab", () => {
    expect(hasFilterParams(new URLSearchParams("tab=logistica"))).toBe(false);
    expect(hasFilterParams(new URLSearchParams("platform=effi"))).toBe(true);
  });
});

function Probe({ onMemory }: { onMemory: (value: unknown) => void }) {
  onMemory(useFilterMemory());
  return null;
}

describe("useFilterMemory - on screen", () => {
  beforeEach(() => {
    window.localStorage.clear();
    nav.replace.mockReset();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 28, 10));
  });

  it("restores the remembered filters and tab when a dashboard is opened with none", async () => {
    writeRememberedFilters(
      captureFilters("/co", new URLSearchParams("from=2026-09-21&to=2026-09-27&tab=logistica"), MONDAY),
    );
    nav.pathname = "/co";
    nav.search = new URLSearchParams();
    render(<Probe onMemory={() => {}} />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledTimes(1));
    expect(nav.replace).toHaveBeenCalledWith(
      "/co?from=2026-09-21&to=2026-09-27&tab=logistica",
      { scroll: false },
    );
  });

  it("does not override filters that the link already carries, and remembers them", async () => {
    nav.pathname = "/global";
    nav.search = new URLSearchParams("from=2026-09-01&to=2026-09-28");
    render(<Probe onMemory={() => {}} />);
    await waitFor(() => expect(readRememberedFilters()?.preset).toBe("este_mes"));
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("only reads on a screen that is not a dashboard, handing the memory to the menu", async () => {
    writeRememberedFilters(captureFilters("/co", new URLSearchParams("platform=effi"), MONDAY));
    nav.pathname = "/co/orders";
    nav.search = new URLSearchParams("page=2");
    const seen: unknown[] = [];
    render(<Probe onMemory={(value) => seen.push(value)} />);
    await waitFor(() => expect(seen.at(-1)).toMatchObject({ platform: "effi" }));
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
