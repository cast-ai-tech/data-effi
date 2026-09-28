// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { loginUrlFor, safeNextPath } from "@/lib/safe-next";
import { middleware } from "@/middleware";

/**
 * A session that ends mid-read must bring the reader back to the SAME screen:
 * same country, same tab, same range. Losing the query string on the way
 * through /login silently reset the filters to "todo el histórico".
 */
describe("loginUrlFor", () => {
  it("carries the path and its query string as `next`", () => {
    const url = loginUrlFor("/co", "?from=2026-09-01&to=2026-09-07&tab=logistica");
    const next = new URL(url, "http://x").searchParams.get("next");
    expect(next).toBe("/co?from=2026-09-01&to=2026-09-07&tab=logistica");
    expect(safeNextPath(next)).toBe(next);
  });

  it("does not add a dangling ? when there are no filters", () => {
    expect(new URL(loginUrlFor("/co/orders", ""), "http://x").searchParams.get("next")).toBe("/co/orders");
    expect(new URL(loginUrlFor("/co/orders", "?"), "http://x").searchParams.get("next")).toBe("/co/orders");
  });
});

describe("middleware - the way through /login keeps your place", () => {
  function hit(path: string, cookies: Record<string, string> = {}) {
    const request = new NextRequest(new URL(path, "http://localhost:3000"));
    for (const [name, value] of Object.entries(cookies)) request.cookies.set(name, value);
    return middleware(request);
  }

  it("sends a signed-out reader to /login remembering the filters, not leaking them onto /login", () => {
    const response = hit("/co?from=2026-09-01&statuses=novedad");
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect([...location.searchParams.keys()]).toEqual(["next"]);
    expect(location.searchParams.get("next")).toBe("/co?from=2026-09-01&statuses=novedad");
  });

  it("sends an already signed-in reader straight to `next` instead of the default screen", () => {
    const response = hit("/login?next=%2Fco%2Forders%3Fpage%3D3", { masterdata_refresh: "r" });
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname + location.search).toBe("/co/orders?page=3");
  });

  it("still refuses to bounce a signed-in reader off-site", () => {
    const response = hit("/login?next=https%3A%2F%2Fevil.example", { masterdata_refresh: "r" });
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.origin).toBe("http://localhost:3000");
    expect(location.pathname).toBe("/global");
  });
});
