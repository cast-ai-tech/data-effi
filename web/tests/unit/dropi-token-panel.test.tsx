import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DropiTokenPanel } from "@/components/DropiTokenPanel";
import type { Connection } from "@/lib/types";

/**
 * Disconnecting Dropi throws away a token that cannot be read back: it is
 * asked first, and nothing is deleted until the owner confirms.
 */
const net = vi.hoisted(() => ({ delete: vi.fn(), get: vi.fn() }));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    get: net.get,
    delete: net.delete,
    put: vi.fn(),
    post: vi.fn(),
  },
}));

const connection = {
  connection_id: "c-9",
  connection_name: "Dropi Colombia",
  country_code: "CO",
  platform_code: "dropi",
  platform_name: "Dropi",
} as unknown as Connection;

afterEach(() => {
  cleanup();
  net.delete.mockReset();
  net.get.mockReset();
});

describe("DropiTokenPanel - desconectar", () => {
  it("asks before deleting the token, and cancelling keeps it", async () => {
    net.get.mockResolvedValue({ has_token: true, credential_status: "ok" });
    render(<DropiTokenPanel connection={connection} isOwner />);

    fireEvent.click(await screen.findByRole("button", { name: "Desconectar API" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Dropi Colombia");
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(net.delete).not.toHaveBeenCalled();
  });

  it("deletes it once confirmed", async () => {
    net.get.mockResolvedValue({ has_token: true, credential_status: "ok" });
    net.delete.mockResolvedValue(undefined);
    render(<DropiTokenPanel connection={connection} isOwner />);

    fireEvent.click(await screen.findByRole("button", { name: "Desconectar API" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, desconectar" }));
    await waitFor(() => expect(net.delete).toHaveBeenCalledWith("/config/dropi/connections/c-9/token"));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });
});
