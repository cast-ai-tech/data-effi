import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { apiOrigin, expiresWithin } from "@/lib/upload-transport";

/**
 * File uploads bypass the `/api/backend` proxy (Netlify caps a function's
 * request body at 6 MB) and go straight to the API with a bearer the proxy
 * hands out. These tests pin that contract from the page's side.
 */

function jwtWithExp(exp: number): string {
  const payload = Buffer.from(JSON.stringify({ sub: "u", exp })).toString("base64url");
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.sig`;
}

describe("apiOrigin", () => {
  it("reduces the public API URL to its origin for the CSP", () => {
    expect(apiOrigin("https://master-data-api.onrender.com/")).toBe("https://master-data-api.onrender.com");
    expect(apiOrigin("http://localhost:8000")).toBe("http://localhost:8000");
  });

  it("opens nothing when the variable is missing or garbage", () => {
    expect(apiOrigin(undefined)).toBeNull();
    expect(apiOrigin("")).toBeNull();
    expect(apiOrigin("not a url")).toBeNull();
  });
});

describe("expiresWithin", () => {
  const now = 1_800_000_000_000; // ms

  it("says no for a token with minutes left", () => {
    expect(expiresWithin(jwtWithExp(now / 1000 + 600), 120, now)).toBe(false);
  });

  it("says yes for a token inside the margin, or already dead", () => {
    expect(expiresWithin(jwtWithExp(now / 1000 + 60), 120, now)).toBe(true);
    expect(expiresWithin(jwtWithExp(now / 1000 - 1), 120, now)).toBe(true);
  });

  it("treats anything it cannot read as expiring", () => {
    expect(expiresWithin("garbage", 120, now)).toBe(true);
    expect(expiresWithin("a.###.c", 120, now)).toBe(true);
    const noExp = Buffer.from(JSON.stringify({ sub: "u" })).toString("base64url");
    expect(expiresWithin(`h.${noExp}.s`, 120, now)).toBe(true);
  });
});

describe("api.upload", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function json(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  it("asks the proxy for a credential, then posts the file straight to the API", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(200, { access_token: "tok-1" }))
      .mockResolvedValueOnce(json(202, { jobs: [{ id: "j1" }] }));
    vi.stubGlobal("fetch", fetchMock);

    const form = new FormData();
    form.append("files", new Blob(["a;b\n1;2"]), "x.csv");
    const result = await api.upload<{ jobs: { id: string }[] }>("/ingest/upload", form);

    expect(result.jobs[0].id).toBe("j1");
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [credentialUrl, credentialInit] = fetchMock.mock.calls[0];
    expect(credentialUrl).toBe("/api/backend/auth/upload-credential");
    expect(credentialInit?.method).toBe("POST");
    expect(credentialInit?.credentials).toBe("same-origin");

    // The file itself never touches the proxy.
    const [uploadUrl, uploadInit] = fetchMock.mock.calls[1];
    expect(uploadUrl).toBe("http://localhost:8000/ingest/upload");
    expect(uploadInit?.method).toBe("POST");
    expect(uploadInit?.body).toBe(form);
    expect(uploadInit?.credentials).toBe("omit");
    const headers = uploadInit?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-1");
    // No Content-Type of our own: the browser must set the multipart boundary.
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("retries exactly once with a fresh credential when the API says 401", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(200, { access_token: "stale" }))
      .mockResolvedValueOnce(json(401, { error: { code: "unauthorized", message: "x", detail: {} } }))
      .mockResolvedValueOnce(json(200, { access_token: "fresh" }))
      .mockResolvedValueOnce(json(202, { jobs: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.upload("/ingest/upload", new FormData());

    expect(fetchMock).toHaveBeenCalledTimes(4);
    const retryInit = fetchMock.mock.calls[3][1];
    expect((retryInit?.headers as Record<string, string>).Authorization).toBe("Bearer fresh");
  });

  it("surfaces the API's own error, 413 included, as an ApiError", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json(200, { access_token: "tok" }))
      .mockResolvedValueOnce(
        json(413, { error: { code: "payload_too_large", message: "El envío pesa 30.0 MB", detail: {} } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const failure = await api.upload("/ingest/upload", new FormData()).catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(413);
    expect((failure as ApiError).code).toBe("payload_too_large");
    expect((failure as ApiError).message).toContain("30.0 MB");
  });
});
