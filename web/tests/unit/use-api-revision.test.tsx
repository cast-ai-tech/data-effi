import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApi } from "@/lib/hooks";
import { RevisionContext } from "@/lib/notifications";

/**
 * `useApi` refetches when the revision moves, and does it silently: the
 * numbers already on screen stay until the new ones land. Eighteen skeletons
 * flashing at once every time a file is processed would read as the
 * dashboard breaking.
 */
const net = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: net.get } };
});

function Probe({ path }: { path: string }) {
  const { data, loading, error } = useApi<{ value: number }>(path);
  return (
    <div>
      <span data-testid="value">{data ? data.value : "none"}</span>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="error">{error ? error.message : "none"}</span>
    </div>
  );
}

let bump: () => void = () => {};

function Harness({ path = "/kpis/x" }: { path?: string }) {
  const [revision, setRevision] = useState(0);
  bump = () => setRevision((n) => n + 1);
  return (
    <RevisionContext.Provider value={revision}>
      <Probe path={path} />
    </RevisionContext.Provider>
  );
}

beforeEach(() => {
  net.get.mockReset();
});

afterEach(cleanup);

describe("useApi - revision", () => {
  it("fetches once with no provider, as before", async () => {
    net.get.mockResolvedValue({ value: 1 });
    render(<Probe path="/kpis/x" />);

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));
    expect(net.get).toHaveBeenCalledTimes(1);
  });

  it("refetches when the revision moves", async () => {
    net.get.mockResolvedValueOnce({ value: 1 }).mockResolvedValueOnce({ value: 2 });
    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));

    await act(async () => {
      bump();
    });

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("2"));
    expect(net.get).toHaveBeenCalledTimes(2);
    expect(net.get).toHaveBeenNthCalledWith(2, "/kpis/x");
  });

  it("keeps the old data on screen while the silent refetch is in flight", async () => {
    let release: (value: unknown) => void = () => {};
    net.get
      .mockResolvedValueOnce({ value: 1 })
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));

    await act(async () => {
      bump();
    });

    // Not loading, not blank: the reader still sees 1.
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    expect(screen.getByTestId("value")).toHaveTextContent("1");

    await act(async () => {
      release({ value: 2 });
    });
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("2"));
  });

  it("keeps the last good answer if the silent refetch fails", async () => {
    net.get.mockResolvedValueOnce({ value: 1 }).mockRejectedValueOnce(new Error("boom"));
    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("1"));

    await act(async () => {
      bump();
    });
    await waitFor(() => expect(net.get).toHaveBeenCalledTimes(2));

    expect(screen.getByTestId("value")).toHaveTextContent("1");
    expect(screen.getByTestId("error")).toHaveTextContent("none");
  });
});
