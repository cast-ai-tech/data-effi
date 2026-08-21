import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DateFieldPicker, ExcludedByFieldBand } from "@/components/DateFieldPicker";
import { DateRangeProvider, useDateRange } from "@/lib/date-range";
import type { FormatCountry } from "@/lib/format";

/**
 * The rule this file guards: choosing "entrega" must never look like choosing a
 * view of the whole operation.
 *
 * `delivered_at` is null on 989 of Ecuador's 1.649 guides, so that filter drops
 * 60% of them - exactly the ones somebody still has to chase. The filter is
 * offered because "¿cuánto entregué en enero?" is a real question; what must not
 * happen is an operator asking it and reading the answer as "todo lo que tengo".
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

const ECUADOR: FormatCountry = {
  currency_symbol: "$",
  currency_code: "USD",
  decimal_places: 2,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-EC",
};

/** Stands in for a widget answering with an `excluded_no_date`. */
function Reporter({ count }: { count: number | null }) {
  const { reportExcluded } = useDateRange();
  useEffect(() => {
    reportExcluded(count);
  }, [reportExcluded, count]);
  return null;
}

function renderPicker(reported: number | null = null) {
  return render(
    <DateRangeProvider>
      <DateFieldPicker />
      <Reporter count={reported} />
      <ExcludedByFieldBand country={ECUADOR} />
    </DateRangeProvider>,
  );
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31");
});

afterEach(cleanup);

describe("DateFieldPicker", () => {
  it("starts on Creación, the only date every guide has", () => {
    renderPicker();
    expect(screen.getByRole("button", { name: "Creación" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("offers only the three dates a guide has", () => {
    renderPicker();
    const group = screen.getByRole("group", { name: /Fecha sobre la que/ });
    expect(group.querySelectorAll("button")).toHaveLength(3);
    // `interaccion` and `pauta` belong to other sources and are never a choice.
    expect(screen.queryByRole("button", { name: /pauta/i })).not.toBeInTheDocument();
  });

  it("writes the chosen date into the query string, keeping the range", async () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: "Entrega" }));

    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith(
        "/ec?from=2026-08-01&to=2026-08-31&field=entrega",
        { scroll: false },
      ),
    );
  });

  it("drops the parameter when the default is chosen back", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: "Creación" }));

    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith("/ec?from=2026-08-01&to=2026-08-31", {
        scroll: false,
      }),
    );
  });

  it("ignores a field the API would reject", () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=cumpleaños");
    renderPicker();
    expect(screen.getByRole("button", { name: "Creación" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

describe("ExcludedByFieldBand", () => {
  it("says how many guides left the screen, and offers the way back", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker(989);

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(screen.getByText(/989 guías fuera del tablero/)).toBeInTheDocument();
    expect(screen.getByText(/en tránsito, en novedad o devueltas/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ver por creación" })).toBeInTheDocument();
  });

  it("names the right reason for despacho", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=despacho");
    renderPicker(140);

    await waitFor(() =>
      expect(screen.getByText(/no han salido en una relación de despacho/)).toBeInTheDocument(),
    );
  });

  it("stays silent on the default date, which excludes nobody", async () => {
    renderPicker(989);
    // Even if something reported a count, `creacion` leaves no guide out - a
    // banner here would be a warning about nothing.
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("stays silent while nothing has been reported yet", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker(null);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("stays silent when the count is zero", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker(0);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("uses the country's own thousands separator", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker(1649);
    await waitFor(() =>
      expect(screen.getByText(/1\.649 guías fuera del tablero/)).toBeInTheDocument(),
    );
  });

  it("says 'guía' in the singular", async () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderPicker(1);
    await waitFor(() =>
      expect(screen.getByText(/^1 guía fuera del tablero\.$/)).toBeInTheDocument(),
    );
  });
});
