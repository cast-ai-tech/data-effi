"use client";

import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "@/components/ui/cx";

/**
 * Form controls with the same size everywhere: 44px tall, 15px text, a clear
 * focus ring. `Field` puts a real <label> on whatever it wraps.
 */

export const CONTROL_CLASS =
  "w-full min-h-11 rounded-control border border-line-input bg-surface px-3 text-base text-ink placeholder:text-ink-dim transition-colors focus:border-accent-deep focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-60";

export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  className,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  /** Id of the control, when it is not the only child. */
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className={cx("flex flex-col gap-1.5", className)}>
      <span className="text-sm font-semibold text-ink-2">
        {label}
        {required && (
          <span aria-hidden className="ml-0.5 text-negative-ink">
            *
          </span>
        )}
      </span>
      {children}
      {error ? (
        <span role="alert" className="text-sm text-negative-ink">
          {error}
        </span>
      ) : (
        hint && <span className="text-sm text-ink-muted">{hint}</span>
      )}
    </label>
  );
}

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(CONTROL_CLASS, className)} {...props} />;
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(CONTROL_CLASS, "min-h-24 py-2.5", className)} {...props} />;
}

export function Select({
  className,
  options,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  options?: ReadonlyArray<{ value: string; label: ReactNode; disabled?: boolean }>;
}) {
  const id = useId();
  return (
    <span className="relative block">
      <select
        id={props.id ?? id}
        className={cx(CONTROL_CLASS, "appearance-none pr-9", className)}
        {...props}
      >
        {options?.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
        {children}
      </select>
      <span
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-muted"
      >
        ▾
      </span>
    </span>
  );
}
