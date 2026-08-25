"use client";

/**
 * "¿Tienda o Proveedor?" - the check asked when a company is created and
 * shown again in Configuración. Two groups: the three kinds of store, and the
 * supplier. One click picks; the hint under each says what it means.
 */

import { cx } from "@/components/ui";
import { COMPANY_TYPES, COMPANY_TYPE_META, type CompanyType } from "@/lib/company";

interface Props {
  value: CompanyType | null;
  onChange: (value: CompanyType) => void;
  disabled?: boolean;
}

const SIDE_LABEL = { tienda: "Tienda", proveedor: "Proveedor" } as const;

export function CompanyTypePicker({ value, onChange, disabled }: Props) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {(["tienda", "proveedor"] as const).map((side) => (
        <div key={side} role="group" aria-label={SIDE_LABEL[side]}>
          <span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.06em] text-ink-faint">
            {SIDE_LABEL[side]}
          </span>
          <div className="flex flex-col gap-2">
            {COMPANY_TYPES.filter((type) => COMPANY_TYPE_META[type].side === side).map((type) => {
              const meta = COMPANY_TYPE_META[type];
              const on = value === type;
              return (
                <button
                  key={type}
                  type="button"
                  aria-pressed={on}
                  disabled={disabled}
                  onClick={() => onChange(type)}
                  className={cx(
                    "flex items-start gap-2.5 rounded-control border px-3 py-2.5 text-left text-sm transition disabled:opacity-60",
                    on
                      ? "border-accent bg-accent/15 text-ink"
                      : "border-line-strong text-ink-2 hover:border-line-input hover:text-ink",
                  )}
                >
                  <span
                    aria-hidden
                    className={cx(
                      "mt-0.5 h-4 w-4 shrink-0 rounded-full border-2",
                      on ? "border-accent bg-accent" : "border-line-input",
                    )}
                  />
                  <span className="min-w-0">
                    <span className={cx("block", on && "font-semibold")}>{meta.label}</span>
                    <span className="block text-xs text-ink-dim">{meta.hint}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
