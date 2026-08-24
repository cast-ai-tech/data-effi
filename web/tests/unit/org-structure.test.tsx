import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgStructure } from "@/components/OrgStructure";
import type { SupportedCountry, TenantRow, User } from "@/lib/types";

/**
 * The org chart has three levels and two audiences. What these tests pin:
 *   - organización → empresas → países is rendered as such, with names not codes
 *   - only an org admin gets "Nueva empresa" and "Editar"
 *   - saving the checklist sends the WHOLE country list, so unticking deactivates
 */

const state = vi.hoisted(() => ({
  tenants: [] as unknown[],
  countries: [] as unknown[],
}));

vi.mock("@/lib/hooks", () => ({
  useApi: (path: string | null) => ({
    data: path === "/org/tenants" ? state.tenants : path === "/org/countries" ? state.countries : null,
    error: null,
    loading: false,
    reload: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    get: vi.fn(async () => null),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
  },
}));

import { api } from "@/lib/api";

const catalogue: SupportedCountry[] = [
  { code: "CO", name: "Colombia", currency_code: "COP" },
  { code: "EC", name: "Ecuador", currency_code: "USD" },
  { code: "GT", name: "Guatemala", currency_code: "GTQ" },
];

const tenants: TenantRow[] = [
  {
    tenant_id: "t-1",
    name: "Distrilatam",
    slug: "distrilatam",
    countries: ["EC", "CO"],
    member_count: 3,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    tenant_id: "t-2",
    name: "Sociedad Guatemala",
    slug: "sociedad-guatemala",
    countries: ["GT"],
    member_count: 1,
    notes: "Socio al 40%",
    created_at: "2026-01-02T00:00:00Z",
  },
];

function user(overrides: Partial<User>): User {
  return {
    id: "u-1",
    email: "jefe@dataeffi.co",
    full_name: "Jefe",
    role: "owner",
    tenant_id: "t-1",
    tenant_name: "Distrilatam",
    created_at: "2026-01-01T00:00:00Z",
    org_id: "o-1",
    org_name: "Grupo Distrilatam",
    is_org_admin: false,
    org_role: null,
    countries: null,
    capabilities: [],
    workspaces: [],
    ...overrides,
  };
}

describe("OrgStructure", () => {
  beforeEach(() => {
    state.tenants = tenants;
    state.countries = catalogue;
    vi.mocked(api.patch).mockClear();
    vi.mocked(api.post).mockClear();
  });
  afterEach(cleanup);

  it("renders organización → empresas → países with country names", () => {
    render(<OrgStructure user={user({})} orgName="Grupo Distrilatam" />);

    expect(screen.getByTestId("org-root")).toHaveTextContent("Grupo Distrilatam");
    expect(screen.getByTestId("org-root")).toHaveTextContent("2 empresas");
    expect(screen.getByText("Distrilatam")).toBeInTheDocument();
    expect(screen.getByText("Sociedad Guatemala")).toBeInTheDocument();
    expect(screen.getByText("Ecuador")).toBeInTheDocument();
    expect(screen.getByText("Colombia")).toBeInTheDocument();
    expect(screen.getByText("Guatemala")).toBeInTheDocument();
    expect(screen.getByText("Socio al 40%")).toBeInTheDocument();
  });

  it("hides the editing controls from anyone who is not an org admin", () => {
    render(<OrgStructure user={user({ org_role: "viewer" })} orgName="Grupo" />);

    expect(screen.queryByText("Nueva empresa")).not.toBeInTheDocument();
    expect(screen.queryByText("Editar")).not.toBeInTheDocument();
  });

  it("lets an org admin change the countries and saves the whole list", async () => {
    render(<OrgStructure user={user({ org_role: "admin" })} orgName="Grupo" />);

    expect(screen.getByText("Nueva empresa")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Editar")[0]);

    const form = screen.getByRole("form", { name: "Editar Distrilatam" });
    expect(form).toBeInTheDocument();

    // Untick Colombia, tick Guatemala: the request must carry the resulting list.
    fireEvent.click(screen.getByLabelText(/Colombia/));
    fireEvent.click(screen.getByLabelText(/Guatemala/));
    fireEvent.click(screen.getByText("Guardar cambios"));

    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(api.patch).toHaveBeenCalledWith("/org/tenants/t-1", {
      name: "Distrilatam",
      notes: null,
      countries: ["EC", "GT"],
    });
  });

  it("creates a company from the same form", async () => {
    render(<OrgStructure user={user({ is_org_admin: true })} orgName="Grupo" />);

    fireEvent.click(screen.getByText("Nueva empresa"));
    fireEvent.change(screen.getByPlaceholderText("Ej. Distrilatam Ecuador"), {
      target: { value: "Sociedad Honduras" },
    });
    fireEvent.click(screen.getByLabelText(/Ecuador/));
    fireEvent.click(screen.getByText("Crear empresa"));

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post).toHaveBeenCalledWith("/org/tenants", {
      name: "Sociedad Honduras",
      notes: null,
      countries: ["EC"],
    });
  });
});
