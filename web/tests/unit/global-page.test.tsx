import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import GlobalPage from "@/app/global/page";
import type { Country } from "@/lib/types";

/**
 * The product rule: a figure that could not be read is said to be missing,
 * never printed as a zero. The global screen used to sum an empty list when
 * `/kpis/global` failed and show "0 guías" as if it were real.
 */
const net = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: net.get } };
});

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/widgets/global_summary", () => ({
  default: () => <div data-testid="global-summary" />,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/global",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const colombia = {
  code: "CO",
  name: "Colombia",
  is_active: true,
  currency_symbol: "$",
  currency_code: "COP",
  decimal_places: 0,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-CO",
} as unknown as Country;

function answer(routes: Record<string, unknown>) {
  net.get.mockImplementation(async (path: string) => {
    const key = Object.keys(routes).find((prefix) => path.startsWith(prefix));
    if (!key) throw new Error(`unexpected ${path}`);
    const value = routes[key];
    if (value instanceof Error) throw value;
    return value;
  });
}

beforeEach(() => {
  net.get.mockReset();
});

afterEach(cleanup);

describe("GlobalPage", () => {
  it("says the totals could not be read instead of printing zeros", async () => {
    answer({
      "/kpis/global": new Error("La API no respondió"),
      "/config/countries": [colombia],
      "/config/connections": [],
      "/auth/me": { countries: null },
      "/ai/brief": { summary: "ok", degraded: false },
    });
    render(<GlobalPage />);

    await waitFor(() => expect(screen.getByText("La API no respondió")).toBeInTheDocument());
    expect(screen.queryByText("Guías despachadas")).not.toBeInTheDocument();
  });

  it("does not invite to create a company when the countries failed to load", async () => {
    answer({
      "/kpis/global": [],
      "/config/countries": new Error("Sin conexión"),
      "/config/connections": [],
      "/auth/me": { countries: null },
    });
    render(<GlobalPage />);

    await waitFor(() => expect(screen.getByText("Sin conexión")).toBeInTheDocument());
    expect(screen.queryByText("Crea tu primera empresa")).not.toBeInTheDocument();
  });

  it("does not call a failed connections read 'Sin conexiones'", async () => {
    answer({
      "/kpis/global": [],
      "/config/countries": [colombia],
      "/config/connections": new Error("Fallo conexiones"),
      "/auth/me": { countries: null },
      "/ai/brief": { summary: "ok", degraded: false },
    });
    render(<GlobalPage />);

    await waitFor(() => expect(screen.getByText("Fallo conexiones")).toBeInTheDocument());
    expect(screen.queryByText("Sin conexiones")).not.toBeInTheDocument();
  });
});
