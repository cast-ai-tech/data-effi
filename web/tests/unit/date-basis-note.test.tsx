import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BasisDisclosure, useDateBasisNote } from "@/components/DateBasisNote";
import { DateRangeProvider, type DateBasis } from "@/lib/date-range";

/**
 * The rule this file guards: a card that does not respect the filter has to say
 * so, and a card that does must stay quiet.
 *
 * Get it backwards in either direction and the dashboard is worse than one with
 * no filter at all - the operator compares a July number against an all-time
 * number and concludes July was a disaster.
 */
const nav = vi.hoisted(() => ({ search: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => nav.search,
  usePathname: () => "/co",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

function Probe({ basis }: { basis: DateBasis | undefined }) {
  const note = useDateBasisNote(basis);
  return (
    <BasisDisclosure note={note}>
      {note === null && <span>sin nota</span>}
    </BasisDisclosure>
  );
}

function renderProbe(basis: DateBasis | undefined) {
  return render(
    <DateRangeProvider>
      <Probe basis={basis} />
    </DateRangeProvider>,
  );
}

beforeEach(() => {
  nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31");
});

afterEach(cleanup);

describe("useDateBasisNote", () => {
  it("marks a widget that ignores the range, above the card", () => {
    renderProbe(null);
    expect(screen.getByText("Histórico completo")).toBeInTheDocument();
    expect(screen.getByText(/no cambia al mover el rango/)).toBeInTheDocument();
  });

  it("does not claim the widget filtered when the server said nothing", () => {
    renderProbe(undefined);
    expect(screen.getByText("Sin confirmar")).toBeInTheDocument();
  });

  it("names WHICH date each basis filtered on, and they differ", () => {
    // The whole reason this is per-card: one global sentence in the header
    // would be wrong on the servicio and efectividad tabs.
    const cases: Array<[Exclude<DateBasis, null>, RegExp]> = [
      ["creacion", /creación de la guía/],
      ["movimiento", /movimiento de dinero/],
      ["interaccion", /gestión de servicio al cliente/],
      ["pauta", /gasto en pauta/],
    ];

    for (const [basis, expected] of cases) {
      const { unmount } = renderProbe(basis);
      expect(screen.getByText(expected)).toBeInTheDocument();
      unmount();
    }
  });

  it("does not shout about a filter that worked", () => {
    // A card whose number matches the filter needs a footnote, not a banner.
    renderProbe("creacion");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shouts when the card used a different date than the one chosen", () => {
    // `/kpis/daily-contribution` always answers `creacion`, whatever was asked.
    // The reader made an explicit choice and this card did not obey it, so a
    // footnote is not enough.
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderProbe("creacion");

    expect(screen.getByText("Otra fecha")).toBeInTheDocument();
    expect(screen.getByText(/no sobre la fecha de entrega que elegiste/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("stays a footnote when the card used exactly the date chosen", () => {
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderProbe("entrega");

    expect(screen.getByText(/sobre la fecha de entrega/)).toBeInTheDocument();
    expect(screen.queryByText("Otra fecha")).not.toBeInTheDocument();
  });

  it("does not call a fixed-basis widget disobedient", () => {
    // CS has no other date to offer: `interaccion` is not a choice the reader
    // could have made, so it is a footnote, not a broken promise.
    nav.search = new URLSearchParams("from=2026-08-01&to=2026-08-31&field=entrega");
    renderProbe("interaccion");

    expect(screen.queryByText("Otra fecha")).not.toBeInTheDocument();
    expect(screen.getByText(/gestión de servicio al cliente/)).toBeInTheDocument();
  });

  it("says nothing at all while the range is Máximo", () => {
    // No filter applied means no discrepancy between cards to explain, and a
    // banner on every card is a banner the reader stops seeing.
    nav.search = new URLSearchParams();
    renderProbe(null);
    expect(screen.getByText("sin nota")).toBeInTheDocument();
  });
});
