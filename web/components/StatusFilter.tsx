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
 * Vive en la URL, como el rango y la plataforma, así que un tablero filtrado se
 * puede compartir por WhatsApp y el otro ve exactamente lo mismo.
 */

import { cx } from "@/components/ui";
import {
  STATUS_GROUPS,
  STATUS_GROUP_LABELS,
  useDateRange,
  type StatusGroup,
} from "@/lib/date-range";

export function StatusFilter() {
  const { statuses, setStatuses } = useDateRange();
  // `null` = todos activos. Es el estado normal y el que ve quien nunca tocó
  // el filtro, así que se dibuja con los cinco encendidos.
  const active = statuses ?? [...STATUS_GROUPS];

  function toggle(group: StatusGroup) {
    const isOn = active.includes(group);
    const next = isOn ? active.filter((g) => g !== group) : [...active, group];
    // Apagar el último dejaría un tablero sin una sola guía y sin forma obvia
    // de volver: se ignora el clic que vaciaría el filtro.
    if (next.length === 0) return;
    setStatuses(next);
  }

  const filtering = statuses !== null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div
        className="flex flex-wrap items-center gap-0.5 rounded-control border border-line-strong bg-surface p-0.5"
        role="group"
        aria-label="Estados incluidos en todos los cálculos"
      >
        {STATUS_GROUPS.map((group) => {
          const on = active.includes(group);
          return (
            <button
              key={group}
              type="button"
              onClick={() => toggle(group)}
              aria-pressed={on}
              title={
                on
                  ? `${STATUS_GROUP_LABELS[group]}: se está contando. Clic para excluirlo de todos los números.`
                  : `${STATUS_GROUP_LABELS[group]}: excluido. Clic para volver a contarlo.`
              }
              className={cx(
                "rounded-md px-2.5 py-1 text-sm font-medium transition-colors",
                on
                  ? "bg-range-active text-ink"
                  : "text-ink-faint line-through hover:text-ink-2",
              )}
            >
              {STATUS_GROUP_LABELS[group]}
            </button>
          );
        })}
      </div>

      {/* Un tablero filtrado que no se anuncia es un tablero que se lee mal:
          quien vuelva a él en diez minutos tiene que saber por qué los totales
          no cuadran con Effi. */}
      {filtering && (
        <button
          type="button"
          onClick={() => setStatuses(null)}
          className="rounded-md px-2 py-1 text-xs font-medium text-ink-muted underline decoration-dotted hover:text-ink"
          title="Volver a contar los cinco estados"
        >
          Filtrando {active.length} de {STATUS_GROUPS.length} · quitar filtro
        </button>
      )}
    </div>
  );
}
