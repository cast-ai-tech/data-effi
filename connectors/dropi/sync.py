"""Which dates one Dropi sync reads. Pure functions: no HTTP, no database.

WHY A ROLLING WINDOW AND NOT "ONLY WHAT CHANGED"
------------------------------------------------
A cash-on-delivery order changes status for weeks after it is created: guide
generated, in transit, novedad, delivered or returned. Dropi's list can filter
by "FECHA DE CAMBIO DE ESTATUS", but the document describes it as orders that
changed TO a status selected in the same request - not "anything that changed"
- so it cannot be trusted to return every movement (verificar con cuenta real).
Filtering by creation date over a window that reaches back further than an order
stays open is the version that cannot miss a status change.

  first run           the last BACKFILL_DAYS
  every run after     the last LOOKBACK_DAYS, AND never starting after the
                      cursor - a worker that was down for two months catches up
                      instead of leaving a hole (capped at MAX_CATCHUP_DAYS)

Overlap is free: the content hash makes an unchanged slice a no-op, and the
merge rules make a changed one an update.

The window is cut into slices of SLICE_DAYS so one request never has to page
through a whole quarter, and each slice is its own batch in raw.load_batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

BACKFILL_DAYS = 90
LOOKBACK_DAYS = 45
MAX_CATCHUP_DAYS = 180
SLICE_DAYS = 15


@dataclass(frozen=True, slots=True)
class SyncWindow:
    date_from: date
    date_to: date

    def slices(self, days: int = SLICE_DAYS) -> list[tuple[date, date]]:
        out: list[tuple[date, date]] = []
        start = self.date_from
        while start <= self.date_to:
            end = min(start + timedelta(days=days - 1), self.date_to)
            out.append((start, end))
            start = end + timedelta(days=1)
        return out


def plan_window(*, today: date, synced_through: date | None) -> SyncWindow:
    """The dates the next sync reads, given the cursor."""
    if synced_through is None:
        return SyncWindow(today - timedelta(days=BACKFILL_DAYS - 1), today)

    date_from = min(today - timedelta(days=LOOKBACK_DAYS - 1), synced_through)
    earliest = today - timedelta(days=MAX_CATCHUP_DAYS - 1)
    return SyncWindow(max(date_from, earliest), today)
