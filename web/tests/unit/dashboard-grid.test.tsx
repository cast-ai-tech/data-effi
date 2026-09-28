import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardGrid } from "@/components/DashboardGrid";
import type { Country, LayoutWidget } from "@/lib/types";

/**
 * Acomodar el tablero guarda en el servidor. Dos cambios rápidos lanzaban dos
 * PUT en paralelo: podían llegar al revés y perder el último sin aviso, y la
 * reversión de uno fallido pisaba el siguiente. Ahora van en fila.
 */
const net = vi.hoisted(() => ({
  put: vi.fn<(path: string, body: unknown) => Promise<unknown>>(),
}));

vi.mock("@/lib/api", () => ({ api: { put: net.put } }));

vi.mock("@/components/WidgetRenderer", () => ({
  WidgetRenderer: ({ widget }: { widget: LayoutWidget }) => <div>{widget.title}</div>,
}));

function widget(code: string): LayoutWidget {
  return {
    widget_code: code,
    tab: "finanzas",
    title: code,
    description: "",
    sort_order: 1,
    width: 1,
    state: "available",
    state_message: null,
    required_domains: [],
    optional_domains: [],
    missing_required: [],
    missing_optional: [],
    awaiting_data: [],
  };
}

const country = { code: "CO" } as Country;

beforeEach(() => {
  net.put.mockReset();
});

afterEach(cleanup);

describe("DashboardGrid - guardado", () => {
  it("manda los PUT uno detrás de otro y recarga solo tras el último", async () => {
    const releases: Array<() => void> = [];
    net.put.mockImplementation(
      () => new Promise((resolve) => releases.push(() => resolve(undefined))),
    );
    const onSaved = vi.fn();
    const widgets = [widget("a"), widget("b")];
    render(
      <DashboardGrid
        widgets={widgets}
        country={country}
        defaultFullWidth={new Set()}
        customised={false}
        onSaved={onSaved}
      />,
    );

    fireEvent.click(screen.getByLabelText("a: ocupar dos columnas"));
    fireEvent.click(screen.getByLabelText("b: ocupar dos columnas"));
    await act(async () => {});

    // The second waits for the first.
    expect(net.put).toHaveBeenCalledTimes(1);

    await act(async () => {
      releases[0]();
    });
    expect(net.put).toHaveBeenCalledTimes(2);
    expect(onSaved).not.toHaveBeenCalled();

    // The second carries BOTH changes.
    const body = net.put.mock.calls[1][1] as { placements: { widget_code: string; width: number }[] };
    expect(body.placements.map((p) => p.width)).toEqual([2, 2]);

    await act(async () => {
      releases[1]();
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("si falla uno que ya tiene otro detrás, no revierte encima del más nuevo", async () => {
    let call = 0;
    net.put.mockImplementation(() => {
      call += 1;
      return call === 1 ? Promise.reject(new Error("x")) : Promise.resolve(undefined);
    });
    render(
      <DashboardGrid
        widgets={[widget("a"), widget("b")]}
        country={country}
        defaultFullWidth={new Set()}
        customised={false}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByLabelText("a: ocupar dos columnas"));
      fireEvent.click(screen.getByLabelText("b: ocupar dos columnas"));
    });

    // b's change (the newest, saved fine) is still on screen, and so is a's,
    // which travelled inside it.
    expect(screen.getByLabelText("a: ocupar una columna")).toBeInTheDocument();
    expect(screen.getByLabelText("b: ocupar una columna")).toBeInTheDocument();
  });
});
