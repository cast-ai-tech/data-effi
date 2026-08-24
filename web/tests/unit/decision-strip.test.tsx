import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DecisionStrip, VERDICT_META } from "@/components/DecisionStrip";
import type { Decision, DecisionsResponse } from "@/lib/types";

/**
 * The strip's two promises: a verdict reads as a verb, and no verdict is ever
 * painted in warning. Warning belongs to degraded connectors; a product to
 * watch is not a fault in the plumbing.
 */
const api = vi.hoisted(() => ({
  payload: null as unknown,
  error: null as Error | null,
  loading: false,
}));

vi.mock("@/lib/hooks", () => ({
  useApi: vi.fn(() => ({
    data: api.payload,
    loading: api.loading,
    error: api.error,
    reload: vi.fn(),
  })),
  usePersistentState: vi.fn(() => [null, vi.fn()]),
}));

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(async () => ({})) },
}));

function decision(overrides: Partial<Decision> = {}): Decision {
  return {
    key: "p1",
    label: "Crema facial",
    verdict: "cut",
    headline: "Entrega 48% con punto de equilibrio en 61%: pierde en cada despacho.",
    numbers: { product_id: "p1" },
    impact_amount: -1250000,
    impact_currency: "COP",
    deep_link: "/co/products",
    ...overrides,
  };
}

function response(items: Decision[], extra: Partial<DecisionsResponse> = {}): DecisionsResponse {
  return {
    scope: "products",
    country_code: "CO",
    items,
    thresholds: {},
    narrative: null,
    degraded: false,
    degraded_reason: null,
    ...extra,
  };
}

beforeEach(() => {
  api.payload = null;
  api.error = null;
  api.loading = false;
});

afterEach(cleanup);

describe("DecisionStrip", () => {
  it("is a labelled region with one verb per decision", () => {
    api.payload = response([
      decision(),
      decision({ key: "p2", label: "Serum", verdict: "keep", impact_amount: 900000 }),
      decision({ key: "p3", label: "Tónico", verdict: "watch", impact_amount: null }),
    ]);
    render(<DecisionStrip countryCode="CO" scope="products" />);

    const region = screen.getByRole("region", { name: "Decisiones · Productos" });
    expect(region).toBeVisible();
    expect(screen.getByText("Cortar")).toBeVisible();
    expect(screen.getByText("Seguir")).toBeVisible();
    expect(screen.getByText("Vigilar")).toBeVisible();
    expect(screen.getByText("Crema facial")).toBeVisible();
  });

  it("never paints a verdict in warning", () => {
    api.payload = response(
      (Object.keys(VERDICT_META) as Decision["verdict"][]).map((verdict, index) =>
        decision({ key: `k${index}`, verdict }),
      ),
    );
    const { container } = render(<DecisionStrip countryCode="CO" scope="products" max={10} />);

    expect(container.querySelectorAll('[class*="warning"]')).toHaveLength(0);
    // And the tones it does use are the three the design allows.
    for (const meta of Object.values(VERDICT_META)) {
      expect(["accent", "positive", "neutral"]).toContain(meta.tone);
    }
  });

  it("puts the actionable verdicts first and honours `max`", () => {
    api.payload = response([
      decision({ key: "a", label: "A", verdict: "ok" }),
      decision({ key: "b", label: "B", verdict: "watch" }),
      decision({ key: "c", label: "C", verdict: "cut" }),
      decision({ key: "d", label: "D", verdict: "keep" }),
    ]);
    render(<DecisionStrip countryCode="CO" scope="products" max={2} />);

    const labels = screen.getAllByRole("listitem").map((item) => item.textContent ?? "");
    expect(labels).toHaveLength(2);
    expect(labels[0]).toContain("Cortar");
    expect(labels[1]).toContain("Vigilar");
    expect(screen.queryByText("D")).toBeNull();
  });

  it("renders nothing at all when the endpoint fails or has nothing to say", () => {
    api.error = new Error("404");
    const failed = render(<DecisionStrip countryCode="CO" scope="cash" />);
    expect(failed.container).toBeEmptyDOMElement();
    failed.unmount();

    api.error = null;
    api.payload = response([]);
    const empty = render(<DecisionStrip countryCode="CO" scope="cash" />);
    expect(empty.container).toBeEmptyDOMElement();
  });

  it("offers Explicar and shows the degraded reason quietly", () => {
    api.payload = response([decision()], {
      degraded: true,
      degraded_reason: "Sin presupuesto de IA hoy.",
    });
    render(<DecisionStrip countryCode="CO" scope="products" />);

    expect(screen.getByRole("button", { name: "Explicar" })).toBeVisible();
    expect(screen.getByText("Sin presupuesto de IA hoy.")).toBeVisible();
  });
});
