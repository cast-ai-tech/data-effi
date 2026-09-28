import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EffiExtensionPairingPanel,
  formatCountdown,
} from "@/components/EffiExtensionPairingPanel";
import type { Connection } from "@/lib/types";

/**
 * "Conectar con la extensión": consent first, a code with a countdown, and a
 * poll that stops as soon as the extension redeemed the code.
 */
const calls = vi.hoisted(() => ({
  post: [] as Array<{ path: string; body: unknown }>,
  get: [] as string[],
  statusQueue: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    post: vi.fn(async (path: string, body: unknown) => {
      calls.post.push({ path, body });
      return {
        pairing_id: "p-1",
        connection_id: "c-1",
        code: "ABCD-EFGH-JKMN",
        expires_at: new Date(Date.now() + 600_000).toISOString(),
        ttl_seconds: 600,
        api_url: "https://api.test",
        message: "",
      };
    }),
    get: vi.fn(async (path: string) => {
      calls.get.push(path);
      return (
        calls.statusQueue.shift() ?? {
          pairing_id: "p-1",
          connection_id: "c-1",
          state: "pending",
          credential_status: "none",
          summary: null,
          expires_at: new Date(Date.now() + 600_000).toISOString(),
          redeemed_at: null,
        }
      );
    }),
  },
}));

const connection = {
  connection_id: "c-1",
  connection_name: "Effi Colombia",
  country_code: "CO",
  platform_code: "effi",
  platform_name: "Effi",
} as unknown as Connection;

beforeEach(() => {
  calls.post = [];
  calls.get = [];
  calls.statusQueue = [];
  window.sessionStorage.clear();
});

afterEach(() => cleanup());

describe("EffiExtensionPairingPanel", () => {
  it("does not generate a code without consent", () => {
    render(<EffiExtensionPairingPanel connection={connection} pollMs={10} />);
    const button = screen.getByRole("button", { name: "Generar código" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
  });

  it("shows the code with a countdown and polls until connected", async () => {
    const onConnected = vi.fn();
    render(
      <EffiExtensionPairingPanel
        connection={connection}
        onConnected={onConnected}
        pollMs={10}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Generar código" }));

    expect(await screen.findByText("ABCD-EFGH-JKMN", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByTestId("pairing-countdown").textContent).toMatch(/^(10:00|9:5\d)$/);
    expect(calls.post[0]).toEqual({
      path: "/config/effi/connections/c-1/pairing",
      body: { consent_granted: true },
    });

    calls.statusQueue.push({
      pairing_id: "p-1",
      connection_id: "c-1",
      state: "connected",
      credential_status: "ok",
      summary: "La conexión funciona y tiene todos los permisos.",
      expires_at: new Date(Date.now() + 500_000).toISOString(),
      redeemed_at: new Date().toISOString(),
    });

    expect(
      await screen.findByText("La conexión funciona y tiene todos los permisos.", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1), { timeout: 5000 });
    expect(calls.get[0]).toBe("/config/effi/connections/c-1/pairing/p-1");

    // Polling stops once the code is spent.
    const polls = calls.get.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(calls.get.length).toBe(polls);
    expect(screen.queryByText("ABCD-EFGH-JKMN")).not.toBeInTheDocument();
  });

  it("keeps the pending code on screen after leaving and coming back", async () => {
    const first = render(<EffiExtensionPairingPanel connection={connection} pollMs={10_000} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Generar código" }));
    await screen.findByText("ABCD-EFGH-JKMN", {}, { timeout: 5000 });
    first.unmount();

    render(<EffiExtensionPairingPanel connection={connection} pollMs={10_000} />);
    // Same code, no second POST: a new one would revoke the one already typed
    // into the extension.
    expect(await screen.findByText("ABCD-EFGH-JKMN", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(calls.post).toHaveLength(1);
    expect(screen.getByTestId("pairing-countdown").textContent).toMatch(/^(10:00|9:5\d)$/);
  });

  it("does not bring back a code that already expired", () => {
    window.sessionStorage.setItem(
      "masterdata.effi.pairing.c-1",
      JSON.stringify({
        pairing: { pairing_id: "p-0", connection_id: "c-1", code: "VIEJ-OCOD-IGOO", ttl_seconds: 600 },
        deadline: Date.now() - 1000,
      }),
    );
    render(<EffiExtensionPairingPanel connection={connection} pollMs={10_000} />);
    expect(screen.queryByText("VIEJ-OCOD-IGOO")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("masterdata.effi.pairing.c-1")).toBeNull();
  });

  it("says plainly when Effi rejected the session", async () => {
    render(<EffiExtensionPairingPanel connection={connection} pollMs={10} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Generar código" }));
    await screen.findByText("ABCD-EFGH-JKMN", {}, { timeout: 5000 });

    calls.statusQueue.push({
      pairing_id: "p-1",
      connection_id: "c-1",
      state: "session_rejected",
      credential_status: "session_expired",
      summary: null,
      expires_at: new Date(Date.now() + 500_000).toISOString(),
      redeemed_at: new Date().toISOString(),
    });

    expect(await screen.findByRole("status", {}, { timeout: 5000 })).toHaveTextContent(
      /Effi no aceptó la sesión/,
    );
    expect(screen.getByRole("button", { name: "Generar otro código" })).toBeInTheDocument();
  });
});

describe("formatCountdown", () => {
  it("renders minutes and zero-padded seconds, never negative", () => {
    expect(formatCountdown(600_000)).toBe("10:00");
    expect(formatCountdown(61_000)).toBe("1:01");
    expect(formatCountdown(-5)).toBe("0:00");
  });
});
