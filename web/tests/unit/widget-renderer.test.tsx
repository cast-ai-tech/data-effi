import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WidgetRenderer, describeDomains } from "@/components/WidgetRenderer";
import { useApi } from "@/lib/hooks";
import type { Country, LayoutWidget, WidgetState } from "@/lib/types";

/**
 * The widgets themselves are out of scope here: this file is about the three
 * states WidgetRenderer arbitrates. Stubbing the data hooks keeps every widget
 * permanently in its loading branch, so nothing below the renderer can fail a
 * test for reasons that have nothing to do with blocked/degraded/available.
 *
 * `useApi` doubling as a spy is also how we prove a blocked widget never
 * mounted its component: if it had, the component would have called this.
 */
vi.mock("@/lib/hooks", () => ({
  useApi: vi.fn(() => ({ data: null, loading: true, error: null, reload: vi.fn() })),
  usePolling: vi.fn(() => ({ data: null, loading: true, error: null, reload: vi.fn() })),
  usePersistentState: vi.fn(() => [null, vi.fn()]),
}));

const COUNTRY: Country = {
  code: "CO",
  name: "Colombia",
  currency_code: "COP",
  currency_symbol: "$",
  decimal_places: 0,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-CO",
  timezone: "America/Bogota",
  geo_level1_label: "Departamento",
  is_active: true,
  maturation_days: 14,
  maturation_days_suggested: 14,
};

function makeWidget(overrides: Partial<LayoutWidget> = {}): LayoutWidget {
  return {
    widget_code: "kpi_contribution",
    tab: "finanzas",
    title: "Contribución",
    description: "Lo que quedó después de todo",
    sort_order: 1,
    state: "available" as WidgetState,
    state_message: null,
    required_domains: ["shipments"],
    optional_domains: ["ads"],
    missing_required: [],
    missing_optional: [],
    awaiting_data: [],
    ...overrides,
  };
}

const fetchSpy = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("WidgetRenderer - blocked", () => {
  const blocked = makeWidget({
    widget_code: "kpi_contribution",
    title: "Contribución",
    state: "blocked",
    state_message: "Falta conectar guías para ver este widget.",
    missing_required: ["shipments"],
  });

  /**
   * THE test. A dashboard that silently omits what it cannot compute teaches
   * the operator that they are seeing everything. The blocked card must be on
   * screen, named, explained, and actionable.
   */
  it("renders the blocked widget visibly instead of hiding it", () => {
    render(<WidgetRenderer widget={blocked} country={COUNTRY} />);

    expect(screen.getByText("Contribución")).toBeVisible();
    expect(
      screen.getByText("Falta conectar guías para ver este widget."),
    ).toBeVisible();
  });

  it("labels the card as blocked for assistive technology", () => {
    render(<WidgetRenderer widget={blocked} country={COUNTRY} />);

    const card = screen.getByLabelText("Contribución (bloqueado)");
    expect(card).toBeVisible();
    expect(card).toHaveAttribute("data-widget-state", "blocked");
  });

  it("offers a way out: a link to /settings", () => {
    render(<WidgetRenderer widget={blocked} country={COUNTRY} />);

    const card = screen.getByLabelText("Contribución (bloqueado)");
    // Queried by href rather than by role: the overlay currently sits inside an
    // aria-hidden wrapper, so role queries would not see it. What matters for
    // this test is that the escape hatch is rendered and points at /settings.
    const link = card.querySelector<HTMLAnchorElement>('a[href="/settings"]');
    expect(link).not.toBeNull();
    expect(link).toBeVisible();
    expect(link).toHaveTextContent("Conectar");
  });

  it("falls back to naming the missing domains when the API sent no message", () => {
    render(
      <WidgetRenderer
        widget={{ ...blocked, state_message: null, missing_required: ["movements", "ads"] }}
        country={COUNTRY}
      />,
    );

    expect(
      screen.getByText("Falta conectar movimientos de dinero, pauta para ver este widget."),
    ).toBeVisible();
  });

  it("does NOT mount the registry component, so nothing is fetched", () => {
    render(<WidgetRenderer widget={blocked} country={COUNTRY} />);

    // The widget's own data hook was never reached...
    expect(useApi).not.toHaveBeenCalled();
    // ...and therefore no request left the browser.
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("WidgetRenderer - degraded", () => {
  const degraded = makeWidget({
    state: "degraded",
    state_message: "Sin pauta conectada: el CPA no incluye inversión publicitaria.",
    missing_optional: ["ads"],
  });

  it("shows the warning band with the API's message", () => {
    render(<WidgetRenderer widget={degraded} country={COUNTRY} />);

    const message = screen.getByText(
      "Sin pauta conectada: el CPA no incluye inversión publicitaria.",
    );
    expect(message).toBeVisible();

    // The message lives in a live region so a screen reader announces it.
    // (`getByRole("status")` alone is ambiguous: loading skeletons use it too.)
    const band = message.closest('[role="status"]');
    expect(band).not.toBeNull();
    expect(band).toBeVisible();
  });

  it("still mounts the component - degraded is not blocked", () => {
    render(<WidgetRenderer widget={degraded} country={COUNTRY} />);

    expect(useApi).toHaveBeenCalled();
  });
});

describe("WidgetRenderer - available", () => {
  it("mounts the component and never shows a warning band", () => {
    // A stray state_message on an available widget must stay invisible: the
    // band belongs to the degraded state alone.
    render(
      <WidgetRenderer
        widget={makeWidget({ state: "available", state_message: "No debería verse" })}
        country={COUNTRY}
      />,
    );

    expect(useApi).toHaveBeenCalled();
    expect(screen.queryByText("No debería verse")).toBeNull();
  });
});

describe("WidgetRenderer - unknown widget_code", () => {
  it("says so on screen rather than rendering nothing", () => {
    render(
      <WidgetRenderer
        widget={makeWidget({
          widget_code: "widget_del_futuro",
          title: "Widget del futuro",
          state: "available",
        })}
        country={COUNTRY}
      />,
    );

    expect(screen.getByText("Widget del futuro")).toBeVisible();
    expect(
      screen.getByText(/existe en el servidor pero no en esta versión de la interfaz/),
    ).toBeVisible();
    expect(screen.getByText("widget_del_futuro")).toBeVisible();
  });

  it("does not fetch anything for a widget it cannot render", () => {
    render(
      <WidgetRenderer
        widget={makeWidget({ widget_code: "widget_del_futuro" })}
        country={COUNTRY}
      />,
    );

    expect(useApi).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("describeDomains", () => {
  it("translates known domain codes to Spanish", () => {
    expect(describeDomains(["shipments", "movements"])).toBe(
      "guías, movimientos de dinero",
    );
  });

  it("passes an unknown domain through untouched", () => {
    expect(describeDomains(["shipments", "dominio_nuevo"])).toBe(
      "guías, dominio_nuevo",
    );
  });
});
