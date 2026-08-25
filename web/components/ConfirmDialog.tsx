"use client";

/**
 * The dialog the app uses to confirm something it cannot undo.
 *
 * It exists because `window.confirm` fails this product three ways: it paints
 * an operating-system box in the middle of a screen that was designed, it
 * freezes the page thread so Playwright and any browser tooling hang on it,
 * and it can only hold one line of plain text - which is not enough to say
 * WHICH connection is about to die and WHAT breaks outside Master Data when it
 * does.
 *
 * The information is deliberately ranked: title (what), body (what happens),
 * consequence (what you cannot take back), details (which exact thing). A
 * reader who stops after the title still knows enough to hesitate.
 *
 * Confirming is never the preselected gesture. Focus lands on Cancel, Escape
 * cancels, and clicking the backdrop cancels - every reflex an operator has
 * resolves to "no". Destroying something has to be aimed at.
 */

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { Button, cx } from "@/components/ui";

export interface ConfirmDetail {
  label: string;
  value: ReactNode;
}

export interface ConfirmDialogProps {
  title: string;
  /** What is about to happen, in one or two sentences. */
  children: ReactNode;
  /** The part that cannot be undone. Rendered in its own tinted box. */
  consequence?: ReactNode;
  /** Which exact record this is about: name, platform, country. */
  details?: ConfirmDetail[];
  confirmLabel: string;
  cancelLabel?: string;
  /** `danger` destroys something; `warning` is disruptive but recoverable. */
  tone?: "danger" | "warning";
  /** Disables both buttons and renders a busy label while the call runs. */
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ConfirmDialog({
  title,
  children,
  consequence,
  details,
  confirmLabel,
  cancelLabel = "Cancelar",
  tone = "danger",
  pending = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const surfaceRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    // Found by attribute rather than by ref: `Button` is a plain function
    // component whose props do not carry one, and widening the shared
    // component for this one caller is not worth the blast radius.
    surfaceRef.current
      ?.querySelector<HTMLElement>("[data-confirm-cancel]")
      ?.focus();

    function onKey(event: KeyboardEvent) {
      // Captured, so a slide-over listening for Escape underneath does not also
      // close itself when the operator meant to dismiss only this dialog.
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = surfaceRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable || focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      // The row that opened the dialog may have just been deleted along with it.
      if (previous && document.contains(previous)) previous.focus();
    };
  }, [onCancel]);

  const accent =
    tone === "danger"
      ? "border-negative/30 bg-negative/[0.08] text-negative-ink"
      : "border-warning/30 bg-warning/[0.07] text-warning-ink";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      role="presentation"
    >
      <div
        className="absolute inset-0 bg-scrim"
        onClick={pending ? undefined : onCancel}
        aria-hidden
      />
      <div
        ref={surfaceRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-body"
        className="relative w-full max-w-[420px] rounded-card border border-line bg-surface p-5 shadow-pop"
      >
        <h2
          id="confirm-dialog-title"
          className="text-lg font-bold leading-tight tracking-tight text-ink"
        >
          {title}
        </h2>

        <div
          id="confirm-dialog-body"
          className="mt-2 text-sm leading-relaxed text-ink-2"
        >
          {children}
        </div>

        {consequence && (
          <p
            className={cx(
              "mt-3 rounded-control border px-3 py-2.5 text-sm leading-relaxed",
              accent,
            )}
          >
            {consequence}
          </p>
        )}

        {details && details.length > 0 && (
          <dl className="mt-3 space-y-1.5 rounded-control border border-line bg-sunken px-3 py-2.5">
            {details.map((detail) => (
              <div key={detail.label} className="flex items-baseline gap-3">
                <dt className="w-[88px] shrink-0 text-xs text-ink-dim sm:w-[104px]">
                  {detail.label}
                </dt>
                <dd className="min-w-0 flex-1 truncate text-sm text-ink-2">
                  {detail.value}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <Button
            data-confirm-cancel
            type="button"
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="danger"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? "Un momento…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
