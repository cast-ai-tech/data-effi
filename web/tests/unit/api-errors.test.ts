import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NETWORK_ERROR_STATUS, api, friendlyErrorMessage } from "@/lib/api";

/**
 * Every screen prints `error.message`. A failure the API did not explain -
 * a sleeping server, no internet - must still read as plain Spanish that says
 * what to do next, never "Error 502" or "Failed to fetch".
 */
describe("api client - errors a merchant can read", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the API's own message when it sent one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "conflict", message: "Ese correo ya tiene cuenta.", detail: {} } }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    await expect(api.get("/x")).rejects.toMatchObject({ status: 409, message: "Ese correo ya tiene cuenta." });
  });

  it("explains a bare 5xx as a server waking up, with the next step", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })));
    const error = await api.get("/x").catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).not.toMatch(/Error 502/);
    expect((error as ApiError).message).toMatch(/Reintentar/);
  });

  it("turns a request that never left the browser into a readable ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const error = await api.get("/x").catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(NETWORK_ERROR_STATUS);
    expect((error as ApiError).message).toMatch(/conexión/);
    expect((error as ApiError).message).not.toMatch(/Failed to fetch/);
  });

  it("does not swallow a deliberate abort", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    await expect(api.get("/x")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("has a specific, actionable sentence for the common statuses", () => {
    expect(friendlyErrorMessage(403)).toMatch(/permiso/);
    expect(friendlyErrorMessage(404)).toMatch(/No encontramos/);
    expect(friendlyErrorMessage(413)).toMatch(/demasiado grande/);
    expect(friendlyErrorMessage(429)).toMatch(/Espera un minuto/);
    for (const status of [0, 400, 403, 404, 408, 413, 429, 500, 503]) {
      expect(friendlyErrorMessage(status)).not.toMatch(/Error \d/);
    }
  });
});
