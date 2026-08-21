"use client";

/**
 * The global date filter, in the shape an ads-manager operator already knows:
 * a button showing the active range, and a panel with shortcuts on the left and
 * a calendar on the right.
 *
 * Nothing is applied until "Aplicar". A filter that fires on every click of the
 * calendar reloads twenty widgets against a half-built range - the reader sees
 * a flash of "1 de julio a 1 de julio" before they have chosen the second date.
 *
 * The dates on screen always go through `formatDate` with the active country's
 * `date_format`. Chile writes 03-08-2026 and Colombia writes 03/08/2026, and
 * neither of them wants to see the other one's.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, cx } from "@/components/ui";
import {
  FIELD_QUESTIONS,
  INVERTED_RANGE_MESSAGE,
  MAX_RANGE,
  PRESET_KEYS,
  PRESET_LABELS,
  addDays,
  endOfMonth,
  formatRangeLabel,
  fromIso,
  isValidRange,
  resolvePreset,
  startOfMonth,
  toIso,
  useDateRange,
  type DateRange,
  type PresetKey,
} from "@/lib/date-range";
import { FALLBACK_COUNTRY, formatDate, type FormatCountry } from "@/lib/format";

/** Month names are a label, not a date format, so they are not `date_format`'s job. */
const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

/** Monday first: nobody in LATAM reads a week that starts on Sunday. */
const WEEKDAYS = ["lu", "ma", "mi", "ju", "vi", "sá", "do"];

export function DateRangePicker({ country }: { country?: FormatCountry }) {
  const { range, mode, field, today, setRange } = useDateRange();
  const fmt = country ?? FALLBACK_COUNTRY;

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange>(range);
  const [error, setError] = useState<string | null>(null);
  const [viewMonth, setViewMonth] = useState<Date>(() => startOfMonth(new Date()));

  const rootRef = useRef<HTMLDivElement>(null);

  const todayIso = today ? toIso(today) : null;

  const openPanel = useCallback(() => {
    setDraft(range);
    setError(null);
    // Land on the month the range starts in, and show the month before the
    // current one as the left pane so the common case - "the last few weeks" -
    // is visible without a single click on the arrows.
    const anchor = fromIso(range.from) ?? today ?? new Date();
    const base = startOfMonth(anchor);
    const isCurrent =
      today !== null &&
      base.getFullYear() === today.getFullYear() &&
      base.getMonth() === today.getMonth();
    setViewMonth(isCurrent ? new Date(base.getFullYear(), base.getMonth() - 1, 1) : base);
    setOpen(true);
  }, [range, today]);

  // Close on outside click and on Escape: a filter panel that traps you is
  // worse than one that closes when you did not mean it to.
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const pickDay = useCallback((iso: string) => {
    setError(null);
    setDraft((current) => {
      // A complete range, or none at all, starts a new one. A click before the
      // start is a correction of the start, not an inverted range.
      if (!current.from || current.to || iso < current.from) {
        return { from: iso, to: null };
      }
      return { from: current.from, to: iso };
    });
  }, []);

  const applyDraft = useCallback(() => {
    // One click on the calendar means one day, not "from here to forever".
    const next: DateRange =
      draft.from && !draft.to ? { from: draft.from, to: draft.from } : draft;

    if (!isValidRange(next)) {
      setError(INVERTED_RANGE_MESSAGE);
      return;
    }
    setRange(next);
    setOpen(false);
  }, [draft, setRange]);

  const draftLabel = useMemo(() => formatRangeLabel(draft, fmt), [draft, fmt]);
  const buttonLabel = useMemo(() => formatRangeLabel(range, fmt), [range, fmt]);

  const rightMonth = useMemo(
    () => new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1),
    [viewMonth],
  );

  // Never let the reader page into months that cannot hold data yet.
  const canGoForward =
    today === null ||
    rightMonth.getFullYear() < today.getFullYear() ||
    (rightMonth.getFullYear() === today.getFullYear() &&
      rightMonth.getMonth() < today.getMonth());

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openPanel())}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cx(
          "flex items-center gap-2 rounded-[8px] border bg-surface px-3 py-1.5 text-[12px] transition-colors",
          open ? "border-accent text-ink" : "border-line-strong text-ink-2 hover:border-accent",
        )}
      >
        <CalendarIcon />
        <span className="font-medium">{buttonLabel}</span>
        {mode !== "maximo" && mode !== "personalizado" && (
          <span className="text-ink-dim">· {PRESET_LABELS[mode]}</span>
        )}
        <span aria-hidden className="text-ink-dim">
          ▾
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Seleccionar rango de fechas"
          className="absolute right-0 top-[calc(100%+6px)] z-50 w-[min(92vw,600px)] overflow-hidden rounded-[12px] border border-line-strong bg-surface shadow-2xl"
        >
          <div className="flex flex-col sm:flex-row">
            <div className="flex shrink-0 flex-col gap-0.5 border-b border-line-subtle p-2 sm:w-[176px] sm:border-b-0 sm:border-r">
              {PRESET_KEYS.map((preset) => (
                <PresetButton
                  key={preset}
                  preset={preset}
                  active={matchesPreset(draft, preset, today)}
                  onSelect={() => {
                    setError(null);
                    setDraft(
                      preset === "maximo"
                        ? MAX_RANGE
                        : resolvePreset(preset, today ?? new Date()),
                    );
                  }}
                />
              ))}
            </div>

            <div className="min-w-0 flex-1 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <NavButton
                  label="Mes anterior"
                  glyph="‹"
                  onClick={() =>
                    setViewMonth(
                      (m) => new Date(m.getFullYear(), m.getMonth() - 1, 1),
                    )
                  }
                />
                <p className="flex-1 text-center text-[12px] font-semibold capitalize text-ink">
                  <span>{monthTitle(viewMonth)}</span>
                  <span className="hidden sm:inline"> — {monthTitle(rightMonth)}</span>
                </p>
                <NavButton
                  label="Mes siguiente"
                  glyph="›"
                  disabled={!canGoForward}
                  onClick={() =>
                    setViewMonth(
                      (m) => new Date(m.getFullYear(), m.getMonth() + 1, 1),
                    )
                  }
                />
              </div>

              <div className="flex gap-4">
                <MonthGrid
                  month={viewMonth}
                  draft={draft}
                  todayIso={todayIso}
                  onPick={pickDay}
                />
                <div className="hidden sm:block">
                  <MonthGrid
                    month={rightMonth}
                    draft={draft}
                    todayIso={todayIso}
                    onPick={pickDay}
                  />
                </div>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-2">
                <Endpoint label="Inicio" value={draft.from} country={fmt} />
                <Endpoint label="Fin" value={draft.to} country={fmt} />
              </div>
            </div>
          </div>

          <div className="border-t border-line-subtle px-3 py-2.5">
            {/* States the question the CHOSEN date answers. No promise about
                which date each card used - four endpoints have a fixed basis
                and say so on the card itself. */}
            <p className="text-[11px] leading-snug text-ink-dim">
              {FIELD_QUESTIONS[field]} Cada tarjeta indica debajo sobre qué fecha se
              aplicó el rango.
            </p>

            {error && (
              <p role="alert" className="mt-2 text-[11.5px] font-semibold text-negative">
                {error}
              </p>
            )}

            <div className="mt-2.5 flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-[12px] font-medium text-ink-2">
                {draftLabel}
              </span>
              <div className="flex shrink-0 gap-2">
                <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                  Cancelar
                </Button>
                <Button size="sm" onClick={applyDraft}>
                  Aplicar
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Highlight a shortcut when the draft happens to equal it, however it got there. */
function matchesPreset(draft: DateRange, preset: PresetKey, today: Date | null): boolean {
  if (preset === "maximo") return !draft.from && !draft.to;
  if (!today) return false;
  const candidate = resolvePreset(preset, today);
  return candidate.from === draft.from && candidate.to === draft.to;
}

function monthTitle(month: Date): string {
  return `${MONTHS[month.getMonth()]} ${month.getFullYear()}`;
}

function PresetButton({
  preset,
  active,
  onSelect,
}: {
  preset: PresetKey;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={cx(
        "rounded-[6px] px-2.5 py-1.5 text-left text-[12px] transition-colors",
        active
          ? "bg-accent/[0.14] font-semibold text-accent"
          : "text-ink-2 hover:bg-sunken hover:text-ink",
      )}
    >
      {PRESET_LABELS[preset]}
    </button>
  );
}

function NavButton({
  label,
  glyph,
  onClick,
  disabled,
}: {
  label: string;
  glyph: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="rounded-[6px] border border-line-strong px-2 py-0.5 text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-line-strong disabled:hover:text-ink-2"
    >
      <span aria-hidden>{glyph}</span>
    </button>
  );
}

function Endpoint({
  label,
  value,
  country,
}: {
  label: string;
  value: string | null;
  country: FormatCountry;
}) {
  return (
    <div className="rounded-[8px] border border-line-input px-2.5 py-1.5">
      <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </p>
      <p className="text-[12px] text-ink">{value ? formatDate(value, country) : "—"}</p>
    </div>
  );
}

/** One month of clickable days, with the selected span tinted between the ends. */
function MonthGrid({
  month,
  draft,
  todayIso,
  onPick,
}: {
  month: Date;
  draft: DateRange;
  todayIso: string | null;
  onPick: (iso: string) => void;
}) {
  const days = useMemo(() => monthDays(month), [month]);

  return (
    <div className="min-w-0 flex-1">
      <div className="grid grid-cols-7 gap-y-0.5">
        {WEEKDAYS.map((day) => (
          <span
            key={day}
            className="py-1 text-center text-[10px] font-semibold uppercase text-ink-faint"
          >
            {day}
          </span>
        ))}

        {days.map((day, index) => {
          if (!day) return <span key={`pad-${index}`} />;

          const iso = toIso(day);
          const isFuture = todayIso !== null && iso > todayIso;
          const isStart = iso === draft.from;
          const isEnd = iso === draft.to;
          const inside =
            Boolean(draft.from && draft.to) && iso > draft.from! && iso < draft.to!;

          return (
            <button
              key={iso}
              type="button"
              disabled={isFuture}
              onClick={() => onPick(iso)}
              aria-pressed={isStart || isEnd}
              className={cx(
                "h-[26px] rounded-[5px] text-[11.5px] transition-colors",
                isFuture && "cursor-not-allowed text-ink-faint opacity-45",
                !isFuture && !isStart && !isEnd && !inside && "text-ink-2 hover:bg-sunken",
                inside && "bg-accent/[0.14] text-ink",
                (isStart || isEnd) && "bg-accent font-semibold text-on-accent",
                iso === todayIso && !isStart && !isEnd && "ring-1 ring-inset ring-line-input",
              )}
            >
              {day.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The month laid out on a Monday-first grid, padded with blanks up front. */
function monthDays(month: Date): (Date | null)[] {
  const first = startOfMonth(month);
  const last = endOfMonth(month);
  // getDay() is Sunday-based; shift it so Monday lands on 0.
  const lead = (first.getDay() + 6) % 7;

  const cells: (Date | null)[] = Array.from({ length: lead }, () => null);
  for (let day = first; day <= last; day = addDays(day, 1)) {
    cells.push(day);
  }
  return cells;
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="size-3.5 shrink-0" aria-hidden>
      <rect
        x="2.2"
        y="3.4"
        width="11.6"
        height="10.4"
        rx="1.8"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path
        d="M2.2 6.6h11.6M5.6 2.2v2.4M10.4 2.2v2.4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}
