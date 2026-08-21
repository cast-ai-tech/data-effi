"use client";

/**
 * What each card has to disclose about the filter, ON the card.
 *
 * This cannot live next to the picker as one sentence. A COD guide has several
 * dates and the endpoints do not agree on which one they use: the confirmation
 * widget filters by when customer service made contact, CPA/ROAS by when the ad
 * money was spent, everything else by when the guide was created. A header that
 * said "por fecha de creación" would be a lie on two of the tabs.
 *
 * Four states, and the last two are the ones people get wrong:
 *   - filtered  -> a quiet caption under the card naming WHICH date
 *   - `null`    -> a band above the card: this number ignores the filter
 *   - unknown   -> a band saying we do not know, never a claim that it filtered
 *   - no range  -> nothing at all
 */

import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { cx } from "@/components/ui";
import {
  BASIS_CAVEATS,
  BASIS_LABELS,
  DATE_FIELDS,
  DateBasisScope,
  useDateRange,
  type DateBasis,
} from "@/lib/date-range";

/** A band sits ABOVE the card; a caption sits under it. */
export interface BasisNote {
  kind: "band" | "caption";
  short: string;
  detail: string;
  /** Longer reasoning, for the tooltip. Not every basis has one. */
  caveat?: string;
}

/**
 * What, if anything, this card must disclose.
 *
 * Returns `null` while the range is "Máximo": with no filter applied there is no
 * discrepancy between cards to explain, and a banner on all eighteen of them
 * would be noise that trains the reader to ignore banners.
 */
export function useDateBasisNote(basis: DateBasis | undefined): BasisNote | null {
  const { range, field } = useDateRange();
  const filtering = Boolean(range.from || range.to);

  return useMemo(() => {
    if (!filtering) return null;

    if (basis === undefined) {
      return {
        kind: "band",
        short: "Sin confirmar",
        detail:
          "El servidor no informó si esta cifra respeta el rango seleccionado. No la compare con las tarjetas que sí lo respetan.",
      };
    }

    if (basis === null) {
      return {
        kind: "band",
        short: "Histórico completo",
        detail:
          "Este widget no filtra por fecha: su cifra es de todo el histórico y no cambia al mover el rango.",
      };
    }

    // The reader ASKED for one of the guide's three dates and this endpoint
    // answered with a different one. That is a band, not a footnote: they made
    // an explicit choice and this card did not obey it.
    if (basis !== field && (DATE_FIELDS as readonly string[]).includes(basis)) {
      return {
        kind: "band",
        short: "Otra fecha",
        detail:
          `Este widget filtró sobre ${BASIS_LABELS[basis]}, no sobre ` +
          `${BASIS_LABELS[field]} que elegiste arriba.`,
        caveat: BASIS_CAVEATS[basis],
      };
    }

    return {
      kind: "caption",
      short: "Rango",
      detail: `sobre ${BASIS_LABELS[basis]}`,
      caveat: BASIS_CAVEATS[basis],
    };
  }, [basis, field, filtering]);
}

/**
 * Deliberately NOT the warning colour.
 *
 * Amber in this product means "degradado, mira esto", and it already sits above
 * widgets missing a connector. Painting disclosure bands the same shade would
 * drown those, and this is not an alarm - it is a footnote about what the number
 * covers.
 */
export function BasisBand({
  note,
  rounded = true,
  /** A band that sits on its own rather than capping a card below it. */
  standalone = false,
}: {
  note: BasisNote;
  rounded?: boolean;
  standalone?: boolean;
}) {
  return (
    <div
      role="status"
      className={cx(
        "flex items-start gap-2 border border-line-strong bg-sunken px-4 py-2",
        standalone
          ? "rounded-[12px]"
          : cx("border-b-0", rounded && "rounded-t-[12px]"),
      )}
    >
      <ClockIcon className="mt-[1px] size-3.5 shrink-0 text-ink-dim" />
      <p className="text-[11.5px] leading-snug text-ink-muted">
        <span className="font-semibold text-ink-2">{note.short}</span> · {note.detail}
      </p>
    </div>
  );
}

/** One dim line under the card, naming the date the range bit on. */
export function BasisCaption({ note }: { note: BasisNote }) {
  return (
    <p
      className="mt-1.5 flex items-center gap-1.5 px-1 text-[10.5px] leading-snug text-ink-dim"
      title={note.caveat}
    >
      <ClockIcon className="size-3 shrink-0" />
      <span>
        {note.short} {note.detail}
        {note.caveat && (
          <span aria-hidden className="ml-1 text-ink-faint">
            ⓘ
          </span>
        )}
      </span>
    </p>
  );
}

/**
 * Renders whichever disclosure the note calls for, around the card.
 *
 * Kept in one place so `WidgetRenderer` and any screen that mounts a widget
 * directly cannot drift into disclosing differently.
 */
export function BasisDisclosure({
  note,
  rounded = true,
  children,
}: {
  note: BasisNote | null;
  rounded?: boolean;
  children: ReactNode;
}) {
  const band = note?.kind === "band" ? note : null;

  return (
    <>
      {band && <BasisBand note={band} rounded={rounded} />}
      <div className={cx(band && "[&>section]:rounded-t-none")}>{children}</div>
      {note?.kind === "caption" && <BasisCaption note={note} />}
    </>
  );
}

/**
 * Collects whatever the widget inside reports.
 *
 * `onBasis` is memoised because `useRangedApi` fires it from an effect; an
 * unstable callback would re-run that effect on every render.
 */
export function useBasisReport(): {
  note: BasisNote | null;
  onBasis: (basis: DateBasis | undefined) => void;
} {
  const [basis, setBasis] = useState<DateBasis | undefined>(undefined);
  const onBasis = useCallback((next: DateBasis | undefined) => setBasis(next), []);
  return { note: useDateBasisNote(basis), onBasis };
}

/** Standalone frame, for widgets rendered outside `WidgetRenderer`. */
export function DateBasisFrame({ children }: { children: ReactNode }) {
  const { note, onBasis } = useBasisReport();

  return (
    <div className="relative">
      <BasisDisclosure note={note}>
        <DateBasisScope onBasis={onBasis}>{children}</DateBasisScope>
      </BasisDisclosure>
    </div>
  );
}

function ClockIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <circle cx="8" cy="8" r="5.8" stroke="currentColor" strokeWidth="1.3" />
      <path
        d="M8 4.8V8l2.2 1.4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
