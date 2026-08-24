import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformPicker, platformOptions, shortName } from "@/components/PlatformPicker";
import { DateRangeProvider } from "@/lib/date-range";
import type { Connection } from "@/lib/types";

/**
 * The picker offers exactly the platforms that have a guide-carrying
 * connection, keeps the choice in the URL, and never hides an active filter.
 */
const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => nav.search,
  usePathname: () => "/ec",
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
}));

const api = vi.hoisted(() => ({ connections: [] as unknown[] }));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: { get: vi.fn(async () => api.connections) },
}));

function connection(overrides: Partial<Connection>): Connection {
  return {
    connection_id: crypto.randomUUID(),
    connection_name: "conn",
    country_code: "EC",
    platform_code: "effi",
    platform_name: "Effi (fulfillment COD)",
    tier: 2,
    status: "active",
    health: "ok",
    consent_granted_at: null,
    last_sync_at: null,
    last_error: null,
    hours_since_sync: null,
    batches_7d: null,
    failed_batches_7d: null,
    scope: "country",
    category: "fulfillment",
    has_webhook: false,
    ...overrides,
  } as Connection;
}

const EFFI = connection({ platform_code: "effi", platform_name: "Effi (fulfillment COD)" });
const DROPI = connection({ platform_code: "dropi", platform_name: "Dropi" });
const META = connection({ platform_code: "meta_ads", platform_name: "Meta Ads", category: "pauta" });
const MANUAL = connection({
  platform_code: "manual_xlsx",
  platform_name: "Carga manual Excel/CSV",
  category: "archivos",
  scope: "global",
  country_code: null,
});
const PERU_DROPI = connection({ platform_code: "dropi", platform_name: "Dropi", country_code: "PE" });

beforeEach(() => {
  nav.replace.mockClear();
  nav.search = new URLSearchParams();
  api.connections = [];
});

afterEach(cleanup);

describe("platformOptions", () => {
  it("lists each guide-carrying platform once, ads excluded", () => {
    expect(platformOptions([EFFI, EFFI, DROPI, META])).toEqual([
      { code: "effi", name: "Effi (fulfillment COD)" },
      { code: "dropi", name: "Dropi" },
    ]);
  });

  it("keeps a global connection for every country and drops other countries'", () => {
    expect(platformOptions([EFFI, MANUAL, PERU_DROPI], "EC").map((o) => o.code)).toEqual([
      "effi",
      "manual_xlsx",
    ]);
  });
});

describe("shortName", () => {
  it("drops the catalogue's parenthetical", () => {
    expect(shortName("Effi (fulfillment COD)")).toBe("Effi");
    expect(shortName("Dropi")).toBe("Dropi");
  });
});

describe("PlatformPicker", () => {
  it("renders nothing with a single platform and no filter: there is nothing to choose", async () => {
    api.connections = [EFFI];
    const { container } = render(
      <DateRangeProvider>
        <PlatformPicker countryCode="EC" />
      </DateRangeProvider>,
    );
    await waitFor(() => expect(container.querySelector("[role=group]")).toBeNull());
  });

  it("writes the chosen platform to the URL and clears it with Todas", async () => {
    api.connections = [EFFI, DROPI];
    render(
      <DateRangeProvider>
        <PlatformPicker countryCode="EC" />
      </DateRangeProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Dropi" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/ec?platform=dropi", { scroll: false });

    fireEvent.click(screen.getByRole("button", { name: "Todas" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/ec", { scroll: false });
  });

  it("keeps the range the reader already set", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-14");
    api.connections = [EFFI, DROPI];
    render(
      <DateRangeProvider>
        <PlatformPicker countryCode="EC" />
      </DateRangeProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Effi" }));
    expect(nav.replace).toHaveBeenLastCalledWith(
      "/ec?from=2026-08-01&to=2026-08-14&platform=effi",
      { scroll: false },
    );
  });

  it("shows a platform that came from the link even when no connection matches it", async () => {
    nav.search = new URLSearchParams("platform=dropi");
    api.connections = [EFFI];
    render(
      <DateRangeProvider>
        <PlatformPicker countryCode="EC" />
      </DateRangeProvider>,
    );

    const stray = await screen.findByRole("button", { name: /dropi ×/ });
    expect(stray).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(stray);
    expect(nav.replace).toHaveBeenLastCalledWith("/ec", { scroll: false });
  });
});
