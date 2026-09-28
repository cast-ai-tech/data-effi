import { beforeEach, describe, expect, it } from "vitest";

import {
  RECENT_FAILURE_MS,
  initialUploadChoice,
  readUploadChoice,
  resumableJobs,
  uploadChoiceKey,
  writeUploadChoice,
} from "@/lib/upload-memory";
import type { UploadJob } from "@/lib/types";

/**
 * The daily upload is one drag: the screen starts on yesterday's type and
 * platform, and files still processing come back after looking elsewhere.
 */
describe("upload choice memory", () => {
  beforeEach(() => window.localStorage.clear());

  it("starts on guides with no platform the first time", () => {
    expect(initialUploadChoice(null, null)).toEqual({ kind: "shipments", platform: null });
  });

  it("starts on the type and platform of the last upload in that country", () => {
    writeUploadChoice("co", { kind: "shipments", platform: "effi" });
    expect(readUploadChoice("CO")).toEqual({ kind: "shipments", platform: "effi" });
    expect(initialUploadChoice(null, readUploadChoice("CO"))).toEqual({ kind: "shipments", platform: "effi" });
    // Another country remembers its own.
    expect(readUploadChoice("MX")).toBeNull();
  });

  it("lets a link ask for a type, keeping the platform only if it was for that type", () => {
    const remembered = { kind: "shipments" as const, platform: "effi" };
    expect(initialUploadChoice("movements", remembered)).toEqual({ kind: "movements", platform: null });
    expect(initialUploadChoice("shipments", remembered)).toEqual({ kind: "shipments", platform: "effi" });
    expect(initialUploadChoice("nada", remembered)).toEqual({ kind: "shipments", platform: "effi" });
  });

  it("ignores a corrupt or tampered entry", () => {
    window.localStorage.setItem(uploadChoiceKey("CO"), "{oops");
    expect(readUploadChoice("CO")).toBeNull();
    window.localStorage.setItem(uploadChoiceKey("CO"), JSON.stringify({ kind: "shipments", platform: "Effi; x" }));
    expect(readUploadChoice("CO")).toEqual({ kind: "shipments", platform: null });
  });
});

describe("resumableJobs", () => {
  const now = Date.parse("2026-09-28T15:00:00Z");
  const job = (status: UploadJob["status"], finishedMinutesAgo: number | null): UploadJob =>
    ({
      id: `${status}-${finishedMinutesAgo}`,
      filename: "guias.csv",
      kind: "shipments",
      size_bytes: 10,
      status,
      batch_id: null,
      error: null,
      queued_at: "2026-09-28T14:00:00Z",
      finished_at:
        finishedMinutesAgo === null ? null : new Date(now - finishedMinutesAgo * 60_000).toISOString(),
    }) as UploadJob;

  it("brings back what is still moving and what failed a moment ago", () => {
    const picked = resumableJobs(
      [
        job("queued", null),
        job("processing", null),
        job("failed", 5),
        job("failed", RECENT_FAILURE_MS / 60_000 + 1),
        job("done", 1),
        job("duplicate", 1),
      ],
      now,
    ).map((item) => item.id);
    expect(picked).toEqual(["queued-null", "processing-null", "failed-5"]);
  });
});
