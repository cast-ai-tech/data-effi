import { describe, expect, it } from "vitest";

import { safeNextPath } from "@/lib/safe-next";

describe("safeNextPath", () => {
  it("falls back when there is nothing to go back to", () => {
    expect(safeNextPath(null)).toBe("/global");
    expect(safeNextPath(undefined)).toBe("/global");
    expect(safeNextPath("")).toBe("/global");
  });

  it("keeps a path on this origin, query string included", () => {
    expect(safeNextPath("/co/orders?page=2")).toBe("/co/orders?page=2");
    expect(safeNextPath("/settings")).toBe("/settings");
  });

  it("refuses an absolute URL to another site", () => {
    expect(safeNextPath("https://evil.example/login")).toBe("/global");
    expect(safeNextPath("http://evil.example")).toBe("/global");
  });

  it("refuses protocol-relative and backslash tricks", () => {
    expect(safeNextPath("//evil.example")).toBe("/global");
    expect(safeNextPath("/\\evil.example")).toBe("/global");
    expect(safeNextPath("/co://evil")).toBe("/global");
  });

  it("refuses header-splitting characters", () => {
    expect(safeNextPath("/co\r\nLocation: x")).toBe("/global");
  });

  it("honours a different fallback", () => {
    expect(safeNextPath("javascript:alert(1)", "/login")).toBe("/login");
  });
});
