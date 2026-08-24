/**
 * "Which carrier delivers best HERE."
 *
 * The country league table averages a carrier over every city it touches, and
 * that average hides the whole decision: a carrier can be the best in Bogotá
 * and the worst in the coast. These helpers pick the best carrier per zone from
 * `mart.v_carrier_by_zone`, with one rule shared by every screen that shows
 * the note - so the semáforo, the league table and the zone widget never
 * disagree about who is best in Antioquia.
 */

import type { CarrierZoneRow } from "@/lib/types";

/** Fewer terminal guides than this and a rate is luck, not a measurement. */
export const MIN_TERMINAL_FOR_BEST = 30;

export interface BestCarrier {
  carrier_name: string;
  delivery_rate_pct: number;
  shipments: number;
  terminal: number;
}

/** The zone key a row belongs to, at the requested level. */
export function zoneKey(row: CarrierZoneRow, level: "level1" | "city"): string {
  return level === "level1" ? row.level1_name : `${row.level1_name}||${row.city_name}`;
}

/**
 * Best carrier per zone: highest delivery rate among carriers with enough
 * terminal guides; ties go to the one with more shipments.
 *
 * At `level1` the cities of a department are folded into one figure per
 * carrier first, weighted by terminal guides, because a carrier with one
 * perfect city and nine bad ones is not the best in that department.
 */
export function bestCarrierByZone(
  rows: CarrierZoneRow[],
  level: "level1" | "city" = "level1",
  minTerminal = MIN_TERMINAL_FOR_BEST,
): Map<string, BestCarrier> {
  const folded = new Map<string, Map<string, { shipments: number; terminal: number; delivered: number }>>();

  // A widget that asks for this over a hook someone mocked, or an endpoint
  // that answers with an envelope, must degrade to "no note", never throw.
  if (!Array.isArray(rows)) return new Map();

  for (const row of rows) {
    if (row.delivery_rate_pct === null) continue;
    const zone = zoneKey(row, level);
    let carriers = folded.get(zone);
    if (!carriers) {
      carriers = new Map();
      folded.set(zone, carriers);
    }
    const bucket = carriers.get(row.carrier_name) ?? { shipments: 0, terminal: 0, delivered: 0 };
    bucket.shipments += row.shipments;
    bucket.terminal += row.terminal;
    bucket.delivered += (row.delivery_rate_pct / 100) * row.terminal;
    carriers.set(row.carrier_name, bucket);
  }

  const best = new Map<string, BestCarrier>();
  for (const [zone, carriers] of folded) {
    let winner: BestCarrier | null = null;
    for (const [carrier_name, bucket] of carriers) {
      if (bucket.terminal < minTerminal) continue;
      const rate = (bucket.delivered / bucket.terminal) * 100;
      if (
        !winner ||
        rate > winner.delivery_rate_pct ||
        (rate === winner.delivery_rate_pct && bucket.shipments > winner.shipments)
      ) {
        winner = {
          carrier_name,
          delivery_rate_pct: rate,
          shipments: bucket.shipments,
          terminal: bucket.terminal,
        };
      }
    }
    if (winner) best.set(zone, winner);
  }
  return best;
}
