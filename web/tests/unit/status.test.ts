import { describe, expect, it } from "vitest";

import {
  STATUS_GROUPS,
  STATUS_GROUP_HINTS,
  STATUS_GROUP_LABELS,
  STATUS_GROUP_TEXT,
  STATUS_GROUP_TONES,
  isStatusGroup,
  platformSwatch,
  statusGroupMeta,
  statusGroupOf,
} from "@/lib/status";

/**
 * The five screen groups mirror `core.status_canon.status_group` (migration
 * 045) and `pipeline/mapping.py::STATUS_GROUPS`. Three copies, one rule; this
 * file keeps the browser's copy honest.
 */
describe("statusGroupOf", () => {
  it("puts Effi's and Dropi's delivered guides in the same column", () => {
    expect(statusGroupOf("delivered")).toBe("entregada");
  });

  it("counts a parcel waiting in the office as novedad, not en tránsito", () => {
    expect(statusGroupOf("in_office")).toBe("novedad");
    expect(statusGroupOf("delivery_issue")).toBe("novedad");
  });

  it("counts both return states and a cancellation as devolución", () => {
    expect(statusGroupOf("returning")).toBe("devolucion");
    expect(statusGroupOf("returned")).toBe("devolucion");
    expect(statusGroupOf("cancelled")).toBe("devolucion");
  });

  it("puts a lost parcel and its payout in the indemnización column", () => {
    expect(statusGroupOf("lost")).toBe("indemnizacion");
    expect(statusGroupOf("compensated")).toBe("indemnizacion");
  });

  it("reads everything still moving as en tránsito, unknown codes included", () => {
    for (const code of ["created", "confirmed", "picked_up", "in_transit", "out_for_delivery"]) {
      expect(statusGroupOf(code)).toBe("en_transito");
    }
    expect(statusGroupOf("algo-raro")).toBe("en_transito");
    expect(statusGroupOf(null)).toBe("en_transito");
  });
});

describe("the five groups", () => {
  it("are the operator's five words, in reading order", () => {
    expect([...STATUS_GROUPS]).toEqual([
      "entregada",
      "en_transito",
      "novedad",
      "devolucion",
      "indemnizacion",
    ]);
    expect(STATUS_GROUP_LABELS.entregada).toBe("Entregado");
    expect(STATUS_GROUP_LABELS.en_transito).toBe("En tránsito");
    expect(STATUS_GROUP_LABELS.indemnizacion).toBe("Indemnización");
  });

  it("each has a label, a hint, a tone and a text colour", () => {
    for (const group of STATUS_GROUPS) {
      expect(STATUS_GROUP_LABELS[group]).toBeTruthy();
      expect(STATUS_GROUP_HINTS[group]).toBeTruthy();
      expect(STATUS_GROUP_TONES[group]).toBeTruthy();
      expect(STATUS_GROUP_TEXT[group]).toBeTruthy();
    }
  });

  it("never says 'en calle' or 'muerta' anywhere on screen", () => {
    const words = [
      ...Object.values(STATUS_GROUP_LABELS),
      ...Object.values(STATUS_GROUP_HINTS),
    ].join(" ");
    expect(words.toLowerCase()).not.toMatch(/en calle|muerta|en camino/);
  });
});

describe("statusGroupMeta", () => {
  it("does not colour a travelling guide as a problem", () => {
    expect(statusGroupMeta("en_transito").tone).toBe("accent");
    expect(statusGroupMeta("entregada").tone).toBe("positive");
    expect(statusGroupMeta("devolucion").tone).toBe("negative");
    expect(statusGroupMeta("novedad").tone).toBe("warning");
  });

  it("echoes an unknown group instead of hiding it", () => {
    expect(statusGroupMeta("en_aduana").label).toBe("en_aduana");
    expect(statusGroupMeta("en_aduana").tone).toBe("neutral");
    expect(statusGroupMeta(null).label).toBe("Sin estado");
    expect(isStatusGroup("en_aduana")).toBe(false);
    expect(isStatusGroup("novedad")).toBe(true);
  });
});

describe("platformSwatch", () => {
  it("never paints an unknown platform with another platform's colour", () => {
    expect(platformSwatch("effi")).not.toBe(platformSwatch("dropi"));
    expect(platformSwatch("plataforma-nueva")).toBe("bg-track");
  });
});
