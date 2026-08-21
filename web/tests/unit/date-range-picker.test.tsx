import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DateRangePicker } from "@/components/DateRangePicker";
import { DateRangeProvider, resolvePreset } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";

/**
 * The provider's only state is the query string, so the router is the whole
 * surface worth stubbing: what the picker "does" is rewrite the URL, and that
 * is what these assertions read.
 */
const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => nav.search,
  usePathname: () => "/co",
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
}));

/** Chile writes dates dd-MM-yyyy, which is how we prove nothing is hardcoded. */
const CHILE: FormatCountry = {
  currency_symbol: "$",
  currency_code: "CLP",
  decimal_places: 0,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd-MM-yyyy",
  locale: "es-CL",
};

function renderPicker() {
  return render(
    <DateRangeProvider>
      <DateRangePicker country={CHILE} />
    </DateRangeProvider>,
  );
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.search = new URLSearchParams();
});

afterEach(cleanup);

describe("DateRangePicker", () => {
  it("defaults to Máximo, which means no parameters at all", () => {
    renderPicker();
    expect(screen.getByRole("button", { name: /Máximo/ })).toBeInTheDocument();
  });

  it("shows a range from the URL in the country's own date format", () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31");
    renderPicker();
    expect(screen.getByText("01-08-2026 – 31-08-2026")).toBeInTheDocument();
  });

  it("ignores a from/to that is not a plain ISO date", () => {
    // A hand-edited or truncated link must not forward junk to the API.
    nav.search = new URLSearchParams("from=ayer&to=hoy");
    renderPicker();
    expect(screen.getByRole("button", { name: /Máximo/ })).toBeInTheDocument();
  });

  it("does not touch the URL until Aplicar is pressed", () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /Máximo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Últimos 7 días" }));

    // Twenty widgets must not reload against a half-chosen range.
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("writes the chosen shortcut into the query string", async () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /Máximo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Últimos 7 días" }));
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));

    const expected = resolvePreset("7d", new Date());
    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith(
        `/co?from=${expected.from}&to=${expected.to}`,
        { scroll: false },
      ),
    );
  });

  it("keeps the other query parameters when it rewrites the range", async () => {
    nav.search = new URLSearchParams("tab=logistica");
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /Máximo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Ayer" }));
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));

    const expected = resolvePreset("ayer", new Date());
    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith(
        `/co?tab=logistica&from=${expected.from}&to=${expected.to}`,
        { scroll: false },
      ),
    );
  });

  it("drops the parameters entirely when Máximo is chosen back", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31");
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /01-08-2026/ }));
    fireEvent.click(screen.getByRole("button", { name: "Máximo" }));
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));

    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith("/co", { scroll: false }),
    );
  });


  it("closes on Escape without changing anything", () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /Máximo/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("refuses to offer days that have not happened yet", async () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /Máximo/ }));

    const today = new Date();
    const tomorrow = new Date(
      today.getFullYear(),
      today.getMonth(),
      today.getDate() + 1,
    );
    // The right-hand pane is the current month, so tomorrow is on screen
    // whenever it has not rolled into the next one.
    if (tomorrow.getMonth() === today.getMonth()) {
      const cells = screen.getAllByRole("button", { name: String(tomorrow.getDate()) });
      await waitFor(() => expect(cells.some((cell) => cell.hasAttribute("disabled"))).toBe(true));
    }
  });
});
