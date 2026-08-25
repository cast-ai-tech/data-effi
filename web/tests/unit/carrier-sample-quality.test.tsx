import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CarrierTable from "@/components/widgets/carrier_table";
import { DateRangeProvider } from "@/lib/date-range";
import type { CarrierRow, Country } from "@/lib/types";

/**
 * The rule this file guards: a percentage built on two guides must not be
 * printed the way a percentage built on four hundred is.
 *
 * From migration 021, with real numbers: inside a six-day window SERVIENTREGA
 * shows 48 guides and "100,00% de entrega". It reads like a perfect carrier and
 * it is nothing of the sort - almost none of those guides has closed yet. An
 * operator comparing carriers moves volume toward one that was never measured.
 *
 * The API is mocked at `useApi` so the whole real path runs: the envelope is
 * unwrapped by `useRangedApi` exactly as it is in the browser.
 */
const api = vi.hoisted(() => ({ payload: null as unknown }));

vi.mock("@/lib/hooks", () => ({
  useApi: vi.fn(() => ({
    data: api.payload,
    loading: false,
    error: null,
    reload: vi.fn(),
  })),
  usePersistentState: vi.fn(() => [null, vi.fn()]),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("from=2026-08-10&to=2026-08-15"),
  usePathname: () => "/ec",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const COUNTRY: Country = {
  code: "EC",
  name: "Ecuador",
  currency_code: "USD",
  currency_symbol: "$",
  decimal_places: 2,
  thousands_sep: ".",
  decimal_sep: ",",
  date_format: "dd/MM/yyyy",
  locale: "es-EC",
  timezone: "America/Guayaquil",
  geo_level1_label: "Provincia",
  is_active: true,
  maturation_days: 21,
  maturation_days_suggested: 21,
};

function carrier(overrides: Partial<CarrierRow> = {}): CarrierRow {
  return {
    country_code: "EC",
    carrier_id: null,
    carrier_name: "SERVIENTREGA",
    shipments: 48,
    delivered: 2,
    returned: 0,
    in_transit: 46,
    delivery_rate_pct: 100,
    return_rate_pct: 0,
    avg_days_to_deliver: 2.1,
    p90_days_to_deliver: 4,
    freight_total: 120,
    avg_freight_per_shipment: 2.5,
    revenue: 900,
    contribution: 300,
    currency_code: "USD",
    ...overrides,
  };
}

function renderTable(rows: CarrierRow[]) {
  api.payload = { rows, date_basis: "creacion", excluded_no_date: 0 };
  return render(
    <DateRangeProvider>
      <CarrierTable countryCode="EC" country={COUNTRY} state="available" message={null} />
    </DateRangeProvider>,
  );
}

beforeEach(() => {
  api.payload = null;
});

afterEach(cleanup);

describe("carrier table - sample_quality", () => {
  it("marks a rate built on fewer than ten terminal guides", () => {
    renderTable([carrier({ sample_quality: "muestra_corta" })]);

    // Both percentages carry the mark, because both are the estimate.
    const marks = screen.getAllByTitle(/Menos de 10 guías cerradas/);
    expect(marks).toHaveLength(2);
    expect(marks[0]).toHaveTextContent("~");
  });

  it("still shows the percentage rather than blanking the row", () => {
    // A blank cell explains nothing; a marked number explains itself.
    renderTable([carrier({ sample_quality: "muestra_corta" })]);
    expect(screen.getByText("100,0%")).toBeInTheDocument();
  });

  it("explains the mark under the table, and counts who it affects", () => {
    renderTable([
      carrier({ sample_quality: "muestra_corta" }),
      carrier({ carrier_name: "LAAR", sample_quality: "muestra_corta" }),
      carrier({ carrier_name: "URBANO", sample_quality: "suficiente" }),
    ]);

    expect(screen.getByText(/Afecta a 2 transportadoras/)).toBeInTheDocument();
  });

  it("says it in the singular for one carrier", () => {
    renderTable([
      carrier({ sample_quality: "muestra_corta" }),
      carrier({ carrier_name: "LAAR", sample_quality: "suficiente" }),
    ]);

    expect(screen.getByText(/Afecta a 1 transportadora\./)).toBeInTheDocument();
  });

  it("leaves a well-measured carrier completely unmarked", () => {
    renderTable([carrier({ carrier_name: "URBANO", sample_quality: "suficiente" })]);

    expect(screen.queryByTitle(/Menos de 10 guías cerradas/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Afecta a/)).not.toBeInTheDocument();
  });

  it("marks nothing when the API does not send the field at all", () => {
    // Today `api/schemas.py::CarrierRow` does not declare `sample_quality`, so
    // it never reaches the wire. Marking at random would be worse than not
    // marking: the mark has to mean something.
    renderTable([carrier()]);

    expect(screen.queryByTitle(/Menos de 10 guías cerradas/)).not.toBeInTheDocument();
    expect(screen.getByText("100,0%")).toBeInTheDocument();
  });
});
