import { describe, expect, it } from "vitest";

import {
  BUCKET_ORDER,
  GRADE_ORDER,
  MATURITY_FLOOR_PCT,
  bucketMeta,
  contactLabel,
  contactNotice,
  daysOpenTone,
  daysToDeliver,
  gradeMeta,
  maturityNotice,
  pageWindow,
  pickSplit,
} from "@/lib/orders";
import type { ContributionSplit, CustomerGrade } from "@/lib/types";

// ---------------------------------------------------------------------------
// Customer grade
// ---------------------------------------------------------------------------

describe("gradeMeta", () => {
  it("gives every grade the SQL can produce a label and a tone", () => {
    for (const grade of GRADE_ORDER) {
      const meta = gradeMeta(grade);
      expect(meta.label).not.toBe("");
      expect(meta.explanation).not.toBe("");
    }
  });

  /**
   * The one colour that must not drift. "Riesgo" is the row an operator has to
   * spot while scrolling; painting it anything but the alert colour turns the
   * whole screen into a list they have to read carefully instead of scan.
   */
  it("paints riesgo with the alert colour and nothing else with it", () => {
    expect(gradeMeta("riesgo").tone).toBe("negative");

    const others = GRADE_ORDER.filter((grade) => grade !== "riesgo");
    for (const grade of others) {
      expect(gradeMeta(grade).tone).not.toBe("negative");
    }
  });

  it("keeps nuevo neutral: it is not praise and not a complaint", () => {
    expect(gradeMeta("nuevo").tone).toBe("neutral");
  });

  /**
   * `customer_grade` comes from a CASE in SQL. If someone adds a branch there
   * before this UI ships, the row must still render - and it must not borrow
   * the colour of a grade it is not.
   */
  it("falls back to a neutral 'sin clasificar' for a grade it does not know", () => {
    const unknown = gradeMeta("platino" as CustomerGrade);
    expect(unknown.label).toBe("Sin clasificar");
    expect(unknown.tone).toBe("neutral");
  });

  it("does not crash on a null or empty grade", () => {
    expect(gradeMeta(null).label).toBe("Sin clasificar");
    expect(gradeMeta(undefined).label).toBe("Sin clasificar");
    expect(gradeMeta("").label).toBe("Sin clasificar");
  });
});

// ---------------------------------------------------------------------------
// Shipment status
// ---------------------------------------------------------------------------

describe("bucketMeta", () => {
  it("labels the four ends a guide can reach", () => {
    for (const bucket of BUCKET_ORDER) {
      expect(bucketMeta(bucket).label).not.toBe("");
    }
  });

  /** A parcel in transit is the normal state of a healthy day, not a warning. */
  it("does not colour a travelling guide as a problem", () => {
    expect(bucketMeta("pipeline").tone).toBe("accent");
    expect(bucketMeta("delivered").tone).toBe("positive");
    expect(bucketMeta("returned").tone).toBe("negative");
  });

  it("echoes an unknown bucket instead of hiding it", () => {
    expect(bucketMeta("en_aduana").label).toBe("en_aduana");
    expect(bucketMeta("en_aduana").tone).toBe("neutral");
  });
});

describe("daysOpenTone", () => {
  it("stays neutral while the guide is young", () => {
    expect(daysOpenTone(0, false)).toBe("neutral");
    expect(daysOpenTone(12, false)).toBe("neutral");
  });

  it("warns from 13 days and alerts from 21", () => {
    expect(daysOpenTone(13, false)).toBe("warning");
    expect(daysOpenTone(20, false)).toBe("warning");
    expect(daysOpenTone(21, false)).toBe("negative");
    expect(daysOpenTone(90, false)).toBe("negative");
  });

  /** A closed guide's age is history, not exposure. */
  it("never colours a guide that already finished", () => {
    expect(daysOpenTone(90, true)).toBe("neutral");
  });

  it("stays neutral when the API did not send the age", () => {
    expect(daysOpenTone(null, false)).toBe("neutral");
    expect(daysOpenTone(undefined, false)).toBe("neutral");
  });
});

// ---------------------------------------------------------------------------
// Contact data
// ---------------------------------------------------------------------------

describe("contactLabel", () => {
  const withContact = {
    customer_name: "Marta Cedeño",
    customer_phone: "0991234567",
    customer_ref: "#A47F3C",
  };

  it("shows the name with the phone underneath when PII is visible", () => {
    const label = contactLabel(withContact, true);
    expect(label.primary).toBe("Marta Cedeño");
    expect(label.secondary).toBe("0991234567");
    expect(label.hidden).toBe(false);
  });

  /**
   * THE test for this screen.
   *
   * With `pii_visible: false` the API sends nulls for name and phone. The cell
   * must NOT go blank: an operator who sees an empty column concludes the
   * system lost their data, which is the one thing that is never true. The
   * reference code is the identity, and `hidden` is what tells the page to
   * explain the policy instead of blaming the upload.
   */
  it("falls back to the reference code, marked as hidden, when PII is off", () => {
    const label = contactLabel(
      { customer_name: null, customer_phone: null, customer_ref: "#A47F3C" },
      false,
    );
    expect(label.primary).toBe("#A47F3C");
    expect(label.secondary).toBeNull();
    expect(label.hidden).toBe(true);
  });

  /** A name that somehow arrived anyway must not leak past the policy flag. */
  it("ignores contact data entirely when PII is off", () => {
    const label = contactLabel(withContact, false);
    expect(label.primary).toBe("#A47F3C");
    expect(label.secondary).toBeNull();
    expect(label.hidden).toBe(true);
  });

  /**
   * Missing because the file never carried it is NOT the same as missing
   * because the server refused, and `hidden` is what keeps the two apart.
   */
  it("does not claim data was hidden when the guide simply had none", () => {
    const label = contactLabel(
      { customer_name: null, customer_phone: null, customer_ref: "#B12E90" },
      true,
    );
    expect(label.primary).toBe("#B12E90");
    expect(label.hidden).toBe(false);
  });

  it("promotes the phone when there is no name", () => {
    const label = contactLabel(
      { customer_name: null, customer_phone: "0991234567", customer_ref: "#A47F3C" },
      true,
    );
    expect(label.primary).toBe("0991234567");
    expect(label.secondary).toBeNull();
  });

  it("treats whitespace as absent rather than printing a blank cell", () => {
    const label = contactLabel(
      { customer_name: "   ", customer_phone: "  ", customer_ref: "#A47F3C" },
      true,
    );
    expect(label.primary).toBe("#A47F3C");
  });

  it("always writes something, even with no reference at all", () => {
    const label = contactLabel(
      { customer_name: null, customer_phone: null, customer_ref: null },
      false,
    );
    expect(label.primary).toBe("Sin identificar");
  });
});

describe("contactNotice", () => {
  const withContact = { customer_name: "Marta Cedeño", customer_phone: null };
  const withoutContact = { customer_name: null, customer_phone: null };

  it("blames the policy when the server refused to decrypt", () => {
    expect(contactNotice([withContact], false)).toBe("hidden");
    expect(contactNotice([withoutContact], false)).toBe("hidden");
  });

  /**
   * The state the product is actually in today: `pii_visible` is true, and
   * every one of the 1.649 guides was uploaded before contact data was kept,
   * so the whole column is empty with full permission to see it. Telling the
   * operator they lack permission would send them to argue with an owner who
   * cannot help; the fix is a re-upload, and the notice has to say so.
   */
  it("blames the upload when permission exists and no row carries contact data", () => {
    expect(contactNotice([withoutContact, withoutContact], true)).toBe("missing");
  });

  it("says nothing when at least one row proves the data flows", () => {
    expect(contactNotice([withoutContact, withContact], true)).toBeNull();
  });

  /** An empty table is empty for its own reasons; do not blame the upload. */
  it("says nothing about an empty page", () => {
    expect(contactNotice([], true)).toBeNull();
  });

  it("treats whitespace as no contact data at all", () => {
    expect(
      contactNotice([{ customer_name: "  ", customer_phone: "" }], true),
    ).toBe("missing");
  });
});

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

describe("daysToDeliver", () => {
  it("counts the days between dispatch and delivery", () => {
    expect(daysToDeliver("2026-08-01", "2026-08-06")).toBe(5);
  });

  /** Same-day delivery is real, and 0 is the honest answer for it. */
  it("returns zero for a guide delivered the day it shipped", () => {
    expect(daysToDeliver("2026-08-01", "2026-08-01")).toBe(0);
  });

  it("accepts a timestamp for the delivery, not just a date", () => {
    expect(daysToDeliver("2026-08-01", "2026-08-04T15:30:00Z")).toBe(3);
  });

  it("returns null when the guide never arrived", () => {
    expect(daysToDeliver("2026-08-01", null)).toBeNull();
    expect(daysToDeliver(null, "2026-08-06")).toBeNull();
  });

  /** A delivery before its own dispatch is bad data, not a negative duration. */
  it("refuses to invent a negative duration", () => {
    expect(daysToDeliver("2026-08-06", "2026-08-01")).toBeNull();
  });

  it("returns null rather than NaN on an unparseable date", () => {
    expect(daysToDeliver("ayer", "2026-08-06")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Maturity
// ---------------------------------------------------------------------------

describe("maturityNotice", () => {
  /** With real data today this is 43,1%, so this branch is the one users see. */
  it("explains what the closed figure covers when few guides have finished", () => {
    const notice = maturityNotice(43.1);
    expect(notice).not.toBeNull();
    expect(notice).toContain("43,1%");
    // It informs, it does not alarm: no talk of errors, losses or problems.
    expect(notice?.toLowerCase()).not.toMatch(/error|problema|pérdida|cuidado/);
  });

  it("says nothing once most of the operation has closed", () => {
    expect(maturityNotice(MATURITY_FLOOR_PCT)).toBeNull();
    expect(maturityNotice(85)).toBeNull();
    expect(maturityNotice(100)).toBeNull();
  });

  it("says nothing when maturity is unknown", () => {
    expect(maturityNotice(null)).toBeNull();
    expect(maturityNotice(undefined)).toBeNull();
    expect(maturityNotice(Number.NaN)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Contribution split
// ---------------------------------------------------------------------------

function makeSplit(overrides: Partial<ContributionSplit> = {}): ContributionSplit {
  return {
    country_code: "EC",
    currency_code: "USD",
    shipments: 1649,
    closed_shipments: 711,
    open_shipments: 938,
    realised_revenue: 48000,
    realised_cost: 39358,
    realised_contribution: 8642,
    realised_margin_pct: 18.0,
    capital_in_street: 12018,
    committed_revenue: 63000,
    net_contribution: -3376,
    maturity_pct: 43.1,
    ...overrides,
  };
}

describe("pickSplit", () => {
  it("accepts the single object the endpoint documents", () => {
    const split = makeSplit();
    expect(pickSplit(split, "EC")).toBe(split);
  });

  /** Every other /kpis route answers with a list; this must survive that too. */
  it("accepts a list and picks the country asked for, not the first row", () => {
    const ec = makeSplit({ country_code: "EC" });
    const co = makeSplit({ country_code: "CO", realised_contribution: 1 });
    expect(pickSplit([co, ec], "EC")).toBe(ec);
  });

  it("returns null when there is nothing to show", () => {
    expect(pickSplit(null, "EC")).toBeNull();
    expect(pickSplit(undefined, "EC")).toBeNull();
    expect(pickSplit([], "EC")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

describe("pageWindow", () => {
  it("describes the first page of a long list", () => {
    const span = pageWindow(1, 50, 1649);
    expect(span.first).toBe(1);
    expect(span.last).toBe(50);
    expect(span.pageCount).toBe(33);
    expect(span.hasPrevious).toBe(false);
    expect(span.hasNext).toBe(true);
  });

  it("stops the last page at the real total, not at a round number", () => {
    const span = pageWindow(33, 50, 1649);
    expect(span.first).toBe(1601);
    expect(span.last).toBe(1649);
    expect(span.hasNext).toBe(false);
  });

  it("does not offer a page 1 of 0 when the list is empty", () => {
    const span = pageWindow(1, 50, 0);
    expect(span.first).toBe(0);
    expect(span.last).toBe(0);
    expect(span.pageCount).toBe(1);
    expect(span.hasNext).toBe(false);
  });
});
