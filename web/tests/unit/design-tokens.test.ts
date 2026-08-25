/**
 * Guard for the light-first design system.
 *
 * Every size and colour comes from the tokens in app/globals.css. A stray
 * `text-[11px]` or `bg-white/[0.04]` is not a style choice, it is a regression
 * to the dark-first era that this test exists to catch before a reviewer has
 * to. The printable report keeps its own palette on purpose and is allowed.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..", "..");
const SCAN = ["app", "components", "lib"].map((dir) => join(ROOT, dir));

/** Files that legitimately break the rules, with the reason. */
const ALLOWLIST: Record<string, RegExp[]> = {
  // Printable daily report: its own print palette, reviewed by hand.
  "app/[country]/informe/page.tsx": [/.*/],
};

const FORBIDDEN: Array<{ label: string; pattern: RegExp }> = [
  { label: "arbitrary font size (use text-xs … text-4xl)", pattern: /text-\[\d+(\.\d+)?px\]/ },
  { label: "white overlay (use bg-hover / bg-hover-strong)", pattern: /bg-white\// },
  { label: "black scrim (use bg-scrim)", pattern: /bg-black\// },
  { label: "text-white (use text-on-solid / text-on-accent)", pattern: /\btext-white\b/ },
  { label: "old brand name", pattern: /Data Effi|dataeffi(?!_)/i },
];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(tsx?|css)$/.test(name) ? [full] : [];
  });
}

describe("design tokens", () => {
  const files = SCAN.flatMap(walk);

  it("scans the app", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  for (const { label, pattern } of FORBIDDEN) {
    it(`no ${label}`, () => {
      const offenders: string[] = [];
      for (const file of files) {
        const rel = relative(ROOT, file).replace(/\\/g, "/");
        const allowed = ALLOWLIST[rel];
        if (allowed?.some((rule) => rule.test(pattern.source))) continue;
        const lines = readFileSync(file, "utf-8").split("\n");
        lines.forEach((line, index) => {
          // Legacy cookie names are kept on purpose for one release.
          if (/LEGACY_/.test(line)) return;
          if (pattern.test(line)) offenders.push(`${rel}:${index + 1}: ${line.trim()}`);
        });
      }
      expect(offenders, offenders.join("\n")).toEqual([]);
    });
  }
});
