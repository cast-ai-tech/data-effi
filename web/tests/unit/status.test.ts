import { describe, expect, it } from "vitest";

import {
  STATUS_GROUPS,
  STATUS_GROUP_LABELS,
  STATUS_GROUP_TONES,
  platformSwatch,
  statusGroupOf,
} from "@/lib/status";

/**
 * The five screen groups mirror `core.status_canon.display_group` (migration
 * 040) and `pipeline/mapping.py::DISPLAY_GROUPS`. Three copies, one rule; this
 * file keeps the browser's copy honest.
 */
describe("statusGroupOf", () => {
  it("puts Effi's and Dropi's delivered guides in the same column", () => {
    expect(statusGroupOf("delivered")).toBe("entregada");
  });

  it("counts a parcel waiting in the office as novedad, not en camino", () => {
    expect(statusGroupOf("in_office")).toBe("novedad");
    expect(statusGroupOf("delivery_issue")).toBe("novedad");
  });

  it("counts both return states as devolución", () => {
    expect(statusGroupOf("returning")).toBe("devolucion");
    expect(statusGroupOf("returned")).toBe("devolucion");
  });

  it("keeps cancelled and lost out of every other column", () => {
    expect(statusGroupOf("cancelled")).toBe("muerta");
    expect(statusGroupOf("lost")).toBe("muerta");
  });

  it("reads everything still moving as en camino, unknown codes included", () => {
    for (const code of ["created", "confirmed", "picked_up", "in_transit", "out_for_delivery"]) {
      expect(statusGroupOf(code)).toBe("en_camino");
    }
    expect(statusGroupOf("algo-raro")).toBe("en_camino");
    expect(statusGroupOf(null)).toBe("en_camino");
  });
});

describe("the five groups", () => {
  it("each has a label and a tone", () => {
    for (const group of STATUS_GROUPS) {
      expect(STATUS_GROUP_LABELS[group]).toBeTruthy();
      expect(STATUS_GROUP_TONES[group]).toBeTruthy();
    }
  });
});

describe("platformSwatch", () => {
  it("never paints an unknown platform with another platform's colour", () => {
    expect(platformSwatch("effi")).not.toBe(platformSwatch("dropi"));
    expect(platformSwatch("plataforma-nueva")).toBe("bg-track");
  });
});
