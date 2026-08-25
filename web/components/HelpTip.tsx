"use client";

/**
 * A small "?" that explains a word.
 *
 * Opens on click/tap (and on hover for the mouse), closes on Escape, on a
 * click anywhere else or on a second tap. Native `title=` tooltips are kept
 * where they already exist, but they never show on a phone - this does.
 */

import { useEffect, useId, useRef, useState } from "react";

import { cx } from "@/components/ui/cx";
import { GLOSSARY, type GlossaryKey } from "@/lib/glossary";

export function HelpTip({
  term,
  text,
  label,
  className,
}: {
  /** A glossary entry: the title and text come from lib/glossary.ts. */
  term?: GlossaryKey;
  /** Free text, for one-off explanations. */
  text?: string;
  /** What the button announces; defaults to the term. */
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);

  const entry = term ? GLOSSARY[term] : null;
  const title = entry?.term ?? label ?? "Ayuda";
  const body = entry?.short ?? text ?? "";

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onClick(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  if (!body) return null;

  return (
    <span
      ref={root}
      className={cx("relative inline-flex align-middle", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        aria-label={`Qué significa ${title}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        className={cx(
          "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-bold leading-none transition-colors",
          open
            ? "border-accent-deep bg-accent/15 text-accent-ink"
            : "border-line-strong text-ink-muted hover:border-accent-deep hover:text-accent-ink",
        )}
      >
        ?
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-1/2 top-full z-40 mt-2 w-[min(18rem,calc(100vw-2rem))] -translate-x-1/2 rounded-control border border-line bg-surface p-3 text-left shadow-pop"
        >
          <span className="block text-sm font-semibold text-ink">{title}</span>
          <span className="mt-1 block text-sm leading-relaxed text-ink-2">{body}</span>
        </span>
      )}
    </span>
  );
}
