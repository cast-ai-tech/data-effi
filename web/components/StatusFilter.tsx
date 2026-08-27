"use client";

/**
 * El filtro global de estados.
 *
 * Apagar un estado aquí lo saca del universo de TODOS los números que honran el
 * filtro - conteos, porcentajes y dinero -, no de una tarjeta suelta. El
 * comerciante lo pidió así: "si desactivo un estado los números calculan en
 * todas partes sin contar la data de ese estado".
 *
 * El filtro se aplica ANTES de agregar, en la propia función SQL, no restando
 * después: por eso el % de devolución de un universo sin "novedad" es la
 * devolución de ese universo, y no el mismo número con otra etiqueta.
 *
 * POR QUÉ UN DESPLEGABLE Y NO CINCO BOTONES
 * La primera versión ponía los cinco estados sueltos en la barra. Ocupaban más
 * ancho que el resto del header junto, el aviso de "filtrando" se desbordaba a
 * una segunda línea y rompía la altura de la barra, y tres controles con tres
 * formas distintas competían por la misma mirada. Aquí el filtro toma la MISMA
 * forma que el selector de rango - botón con etiqueta, resumen y flecha -, así
 * que plataforma, estados y fechas se leen como lo que son: tres filtros
 * hermanos. Lo que estaba fuera cabe dentro, y el ancho vuelve a ser el de un
 * botón.
 *
 * Vive en la URL, como el rango y la plataforma, así que un tablero filtrado se
 * puede compartir por WhatsApp y el otro ve exactamente lo mismo.
 */

import { useEffect, useRef, useState } from "react";

import { cx } from "@/components/ui";
import {
  STATUS_GROUPS,
  STATUS_GROUP_LABELS,
  useDateRange,
  type StatusGroup,
} from "@/lib/date-range";

export function StatusFilter() {
  const { statuses, setStatuses } = useDateRange();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // `null` = todos activos. Es el estado normal y el que ve quien nunca tocó el
  // filtro, así que se dibuja con los cinco encendidos.
  const active = statuses ?? [...STATUS_GROUPS];
  const filtering = statuses !== null;

  // Cerrar al hacer clic fuera y con Escape, igual que el selector de rango.
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

  function toggle(group: StatusGroup) {
    const isOn = active.includes(group);
    const next = isOn ? active.filter((g) => g !== group) : [...active, group];
    // Apagar el último dejaría un tablero sin una sola guía y sin forma obvia
    // de volver: se ignora el clic que vaciaría el filtro.
    if (next.length === 0) return;
    setStatuses(next);
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="true"
        title="Qué estados entran en todos los cálculos"
        className={cx(
          "flex items-center gap-2 rounded-control border bg-surface px-3 py-1.5 text-sm transition-colors",
          open
            ? "border-accent text-ink"
            : "border-line-strong text-ink-2 hover:border-accent",
        )}
      >
        <span className="font-medium">Estados</span>
        {/* El resumen es la única defensa contra leer un tablero filtrado
            creyendo que es el completo. "Todos" también se dice, en gris. */}
        <span
          className={cx(filtering ? "font-medium text-accent" : "text-ink-dim")}
        >
          {filtering ? `${active.length} de ${STATUS_GROUPS.length}` : "Todos"}
        </span>
        <span aria-hidden className="text-ink-dim">
          ▾
        </span>
      </button>

      {open && (
        <div
          role="group"
          aria-label="Estados incluidos en todos los cálculos"
          className="absolute right-0 top-[calc(100%+6px)] z-50 w-[min(92vw,264px)] overflow-hidden rounded-card border border-line-strong bg-surface shadow-pop"
        >
          <p className="border-b border-line-subtle px-3 py-2 text-xs leading-relaxed text-ink-muted">
            Lo que apagues deja de contar en todos los números del tablero.
          </p>

          <div className="flex flex-col p-1">
            {STATUS_GROUPS.map((group) => {
              const on = active.includes(group);
              const last = on && active.length === 1;
              return (
                <button
                  key={group}
                  type="button"
                  onClick={() => toggle(group)}
                  aria-pressed={on}
                  disabled={last}
                  title={
                    last
                      ? "Es el único estado activo: no se puede apagar."
                      : on
                        ? "Se está contando. Clic para excluirlo."
                        : "Excluido. Clic para volver a contarlo."
                  }
                  className={cx(
                    "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors",
                    last
                      ? "cursor-not-allowed opacity-60"
                      : "hover:bg-hover-strong",
                    on ? "text-ink" : "text-ink-faint",
                  )}
                >
                  <span
                    aria-hidden
                    className={cx(
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded border text-xs font-bold",
                      on
                        ? "border-accent bg-accent text-on-accent"
                        : "border-line-strong bg-surface text-transparent",
                    )}
                  >
                    ✓
                  </span>
                  {STATUS_GROUP_LABELS[group]}
                </button>
              );
            })}
          </div>

          {filtering && (
            <div className="border-t border-line-subtle p-1">
              <button
                type="button"
                onClick={() => setStatuses(null)}
                className="w-full rounded-md px-2.5 py-1.5 text-left text-sm font-medium text-accent hover:bg-hover-strong"
              >
                Contar todos otra vez
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
