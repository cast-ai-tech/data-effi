import { describe, expect, it } from "vitest";

import { briefSummary } from "@/lib/glossary";

/**
 * The daily summary never shows the server's internal reason ("Falta
 * GEMINI_API_KEY o AI_ENABLED") to a merchant: it says what it means for them.
 */
describe("briefSummary", () => {
  const base = { summary: "Hoy entregaste 40 guías.", degraded: false, degraded_reason: null };

  it("shows a real summary as written", () => {
    expect(briefSummary(base)).toBe("Hoy entregaste 40 guías.");
  });

  it("replaces a configuration error with a plain sentence", () => {
    const text = briefSummary({
      summary: "El copiloto no está configurado en este despliegue. Falta GEMINI_API_KEY o AI_ENABLED.",
      degraded: true,
      degraded_reason: "not_configured",
    });
    expect(text).not.toMatch(/GEMINI|AI_ENABLED|despliegue/);
    expect(text).toMatch(/tableros y cifras funcionan igual/);
  });

  it("covers every other failure without technical words", () => {
    for (const reason of ["budget_exhausted", "upstream_error", "empty_response", "execution_failed", null]) {
      const text = briefSummary({ summary: "Gemini 503", degraded: true, degraded_reason: reason });
      expect(text).not.toMatch(/Gemini|503/);
      expect(text).toMatch(/tableros funcionan igual/);
    }
  });
});
