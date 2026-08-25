"use client";

/**
 * The two pickers of the users screen: countries as flags you click, and the
 * side of the business a person is on. Pure: value in, onChange out; the page
 * decides what to send to the API (see lib/members.ts for the rules).
 */

import { cx } from "@/components/ui";
import { countryFlag } from "@/lib/format";
import {
  BUSINESS_MODELS,
  BUSINESS_MODEL_META,
  type BusinessModel,
  toggleCountry,
} from "@/lib/members";

export interface CountryOption {
  code: string;
  name: string;
}

/**
 * One flag per country the company operates in. Selected flags are lit; a
 * click toggles one. Empty selection reads as "todos".
 */
export function CountryFlagPicker({
  countries,
  selected,
  onChange,
  disabled = false,
  label = "Países que puede ver",
}: {
  countries: readonly CountryOption[];
  selected: readonly string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  const codes = countries.map((c) => c.code);
  const allSelected = selected.length === 0 || codes.every((c) => selected.includes(c));

  return (
    <div role="group" aria-label={label} className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-1.5">
        {countries.map((country) => {
          const on = selected.includes(country.code);
          return (
            <button
              key={country.code}
              type="button"
              disabled={disabled}
              aria-pressed={on}
              aria-label={`${country.name} (${country.code})`}
              title={country.name}
              onClick={() => onChange(toggleCountry(selected, country.code, codes))}
              className={cx(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-sm font-semibold transition disabled:opacity-40",
                on
                  ? "border-accent/60 bg-accent/15 text-ink"
                  : "border-line-strong text-ink-muted hover:border-line-input hover:text-ink",
              )}
            >
              <span aria-hidden className="text-lg leading-none">
                {countryFlag(country.code)}
              </span>
              {country.code}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-ink-dim">
        {allSelected
          ? "Ninguno marcado = ve todos los países de la empresa."
          : `Solo ${selected.join(", ")}. Haz clic en una bandera para agregar o quitar.`}
      </p>
    </div>
  );
}

/** Ecommerce / Proveeduría as two buttons; clicking the lit one clears it. */
export function BusinessModelPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: BusinessModel | null;
  onChange: (next: BusinessModel | null) => void;
  disabled?: boolean;
}) {
  return (
    <div role="group" aria-label="Modelo de negocio" className="flex flex-wrap gap-1.5">
      {BUSINESS_MODELS.map((model) => {
        const on = value === model;
        return (
          <button
            key={model}
            type="button"
            disabled={disabled}
            aria-pressed={on}
            title={BUSINESS_MODEL_META[model].detail}
            onClick={() => onChange(on ? null : model)}
            className={cx(
              "rounded-full border px-2.5 py-1.5 text-sm font-semibold transition disabled:opacity-40",
              on
                ? "border-positive/60 bg-positive/15 text-ink"
                : "border-line-strong text-ink-muted hover:text-ink",
            )}
          >
            {BUSINESS_MODEL_META[model].label}
          </button>
        );
      })}
    </div>
  );
}
