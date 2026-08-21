"use client";

/**
 * Which of a guide's three dates the range is measured against.
 *
 * The operator asked for this in as many words: "todas las fechas son
 * importantes". They are, and they answer different questions - "¿cuánto vendí
 * esta semana?" is not "¿cuánto despaché?" and neither is "¿cuánto entregué?".
 *
 * THE TRAP THIS COMPONENT EXISTS TO DEFUSE
 * Only `creacion` is present on every guide. A guide still in transit has no
 * delivery date, so `entrega` silently removes it - in the real Ecuador data
 * that is 989 of 1.649 guides, and they are precisely the ones somebody still
 * has to chase. That is the honest meaning of "entregadas entre el 1 y el 31",
 * so the filter is offered; what must never happen is an operator using it and
 * believing they are looking at their whole operation.
 */

import { Button, cx } from "@/components/ui";
import {
  DATE_FIELDS,
  DEFAULT_FIELD,
  FIELD_LABELS,
  FIELD_QUESTIONS,
  useDateRange,
} from "@/lib/date-range";
import { FALLBACK_COUNTRY, formatNumber, type FormatCountry } from "@/lib/format";

export function DateFieldPicker() {
  const { field, setField } = useDateRange();

  return (
    <div
      className="hidden items-center gap-0.5 rounded-[8px] border border-line-strong bg-surface p-0.5 md:flex"
      role="group"
      aria-label="Fecha sobre la que se aplica el rango"
    >
      {DATE_FIELDS.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => setField(option)}
          aria-pressed={field === option}
          title={FIELD_QUESTIONS[option]}
          className={cx(
            "rounded-[6px] px-2.5 py-1 text-[11.5px] font-medium transition-colors",
            field === option
              ? "bg-range-active text-ink"
              : "text-ink-muted hover:text-ink-2",
          )}
        >
          {FIELD_LABELS[option]}
        </button>
      ))}
    </div>
  );
}

/**
 * The count of guides the chosen date leaves out, stated once and prominently.
 *
 * Not per card: `excluded_no_date` is a property of (país, fecha elegida) and is
 * the same number on all eighteen widgets. Repeating it eighteen times is how a
 * warning becomes wallpaper.
 *
 * Amber is right here, unlike the per-card basis footnotes: this IS the "watch
 * this" case. Sixty percent of the operation just left the screen.
 */
export function ExcludedByFieldBand({ country }: { country?: FormatCountry }) {
  const { field, excludedNoDate, setField } = useDateRange();
  const fmt = country ?? FALLBACK_COUNTRY;

  if (field === DEFAULT_FIELD || excludedNoDate === null || excludedNoDate === 0) {
    return null;
  }

  const counted = `${formatNumber(excludedNoDate, fmt, 0)} ${
    excludedNoDate === 1 ? "guía" : "guías"
  }`;

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-warning/25 bg-warning/[0.08] px-5 py-2"
    >
      <p className="min-w-0 flex-1 text-[11.5px] leading-snug text-warning">
        <span className="font-semibold">{counted} fuera del tablero.</span>{" "}
        {field === "entrega"
          ? "Todavía no tienen fecha de entrega: van en tránsito, en novedad o devueltas. Ninguna cifra de esta pantalla las cuenta."
          : "Todavía no tienen fecha de despacho: no han salido en una relación de despacho. Ninguna cifra de esta pantalla las cuenta."}
      </p>
      <Button variant="ghost" size="sm" onClick={() => setField(DEFAULT_FIELD)}>
        Ver por creación
      </Button>
    </div>
  );
}
