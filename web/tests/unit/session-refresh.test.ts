import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { SESSION_EXPIRED_HEADER, classifyUnauthorized } from "@/lib/session";

/**
 * The API kills a refresh token the moment it is used. When the access token
 * expires, a dashboard fires ~18 requests at once; if each refreshed on its
 * own, one would win and the rest would log the reader out. These tests pin
 * the contract: the proxy LABELS the 401, the tab refreshes ONCE.
 */

function jwtWithExp(exp: number): string {
  const payload = Buffer.from(JSON.stringify({ sub: "u", exp })).toString("base64url");
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("classifyUnauthorized", () => {
  const now = 1_800_000_000_000;
  const live = jwtWithExp(now / 1000 + 600);
  const dying = jwtWithExp(now / 1000 + 5);

  it("calls a missing or dying access token an expiry when there is a refresh token", () => {
    expect(classifyUnauthorized("kpis/global", null, "r", now)).toBe("expired");
    expect(classifyUnauthorized("kpis/global", dying, "r", now)).toBe("expired");
  });

  it("calls a live token that was rejected, or nothing to refresh with, a dead session", () => {
    expect(classifyUnauthorized("kpis/global", live, "r", now)).toBe("dead");
    expect(classifyUnauthorized("kpis/global", null, null, now)).toBe("dead");
  });

  it("treats a wrong current password as an answer, not a session verdict", () => {
    expect(classifyUnauthorized("auth/me/password", live, "r", now)).toBe("answer");
    // ...unless the token itself had run out: then renew and replay.
    expect(classifyUnauthorized("auth/me/password", null, "r", now)).toBe("expired");
  });
});

describe("api client - refresh", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    });
  }

  const expired = () =>
    json(401, { error: { code: "unauthorized", message: "x", detail: {} } }, { [SESSION_EXPIRED_HEADER]: "1" });

  it("refreshes ONCE for many requests that expired together, then replays each", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const replayed: string[] = [];
    let refreshes = 0;
    const firstCall = new Set<string>();

    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/backend/auth/refresh") {
        refreshes += 1;
        await gate;
        return json(200, { expires_in: 900 });
      }
      if (!firstCall.has(url)) {
        firstCall.add(url);
        return expired();
      }
      replayed.push(url);
      return json(200, { url });
    });
    vi.stubGlobal("fetch", fetchMock);

    const calls = ["/a", "/b", "/c"].map((path) => api.get<{ url: string }>(path));
    // Let all three see their 401 and queue on the refresh before it answers.
    await new Promise((resolve) => setTimeout(resolve, 0));
    release();
    const results = await Promise.all(calls);

    expect(refreshes).toBe(1);
    expect(results.map((r) => r.url)).toEqual(["/api/backend/a", "/api/backend/b", "/api/backend/c"]);
    expect(replayed.sort()).toEqual(["/api/backend/a", "/api/backend/b", "/api/backend/c"]);
  });

  it("does not replay when the refresh fails, and surfaces the 401", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(expired())
      .mockResolvedValueOnce(json(401, { error: { code: "unauthorized", message: "Sesión expirada", detail: {} } }));
    vi.stubGlobal("fetch", fetchMock);

    const failure = await api.get("/a", { auth: false }).catch((err: unknown) => err);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(401);
  });

  it("does not refresh on a 401 the proxy did not label (wrong password)", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        json(401, { error: { code: "unauthorized", message: "La contraseña actual no es correcta", detail: {} } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const failure = await api
      .post("/auth/me/password", { current_password: "x", new_password: "y" }, { auth: false })
      .catch((err: unknown) => err);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((failure as ApiError).message).toBe("La contraseña actual no es correcta");
  });
});
