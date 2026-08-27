"use client";

/**
 * El tablero que cada persona acomoda a su gusto.
 *
 * Se arrastra una tarjeta y se suelta donde debe ir; se jala su borde derecho
 * para que ocupe una columna o dos. Lo que quede se guarda en la cuenta (no en
 * el navegador), así que el mismo tablero aparece desde el celular y desde otra
 * computadora.
 *
 * SIEMPRE ARRASTRABLE, SIN MODO EDICIÓN
 * Fue una decisión del comerciante: acomodar es parte de mirar, no una
 * ceremonia aparte. El precio es que un arrastre accidental no puede costar
 * nada, y por eso el guardado es idempotente y siempre reversible - hay un
 * "Restablecer" que borra la personalización y devuelve el orden de fábrica.
 *
 * OPTIMISTA, PERO HONESTO
 * El reordenamiento se pinta al instante contra el estado local; el PUT viaja
 * después. Si falla, se revierte y se dice - un tablero que muestra un orden que
 * el servidor no guardó es peor que uno que no se deja mover, porque la persona
 * cree que ya quedó.
 */

import { useCallback, useEffect, useState } from "react";

import { WidgetRenderer } from "@/components/WidgetRenderer";
import { cx } from "@/components/ui";
import { api } from "@/lib/api";
import type { Country, LayoutWidget } from "@/lib/types";

interface Props {
  widgets: readonly LayoutWidget[];
  country: Country;
  /** Anchos de fábrica, para las tarjetas que la persona nunca tocó. */
  defaultFullWidth: ReadonlySet<string>;
  /** Si esta persona ya acomodó el tablero (lo dice el servidor). */
  customised: boolean;
  onSaved?: () => void;
}

export function DashboardGrid({
  widgets,
  country,
  defaultFullWidth,
  customised,
  onSaved,
}: Props) {
  // El orden que se está viendo. Arranca del servidor y se adelanta a él
  // mientras se arrastra.
  const [order, setOrder] = useState<LayoutWidget[]>([...widgets]);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // El servidor manda: si llega otro layout (cambió el país o la pestaña), se
  // descarta el local en vez de intentar conciliarlos.
  useEffect(() => {
    setOrder([...widgets]);
  }, [widgets]);

  /** El ancho efectivo: lo que la persona guardó, o el de fábrica. */
  const widthOf = useCallback(
    (widget: LayoutWidget) =>
      widget.width === 2 || widget.width === 1
        ? widget.width
        : defaultFullWidth.has(widget.widget_code)
          ? 2
          : 1,
    [defaultFullWidth],
  );

  const persist = useCallback(
    async (next: LayoutWidget[], previous: LayoutWidget[]) => {
      setSaving(true);
      setError(null);
      try {
        await api.put(`/kpis/layout?country=${country.code}`, {
          placements: next.map((widget, index) => ({
            widget_code: widget.widget_code,
            sort_order: index + 1,
            width: widthOf(widget),
            hidden: false,
          })),
        });
        onSaved?.();
      } catch {
        // Revertir es lo honesto: dejar el orden nuevo en pantalla haría creer
        // que quedó guardado.
        setOrder(previous);
        setError(
          "No se pudo guardar cómo acomodaste el tablero. Intenta otra vez.",
        );
      } finally {
        setSaving(false);
      }
    },
    [country.code, widthOf, onSaved],
  );

  const move = useCallback(
    (from: string, to: string) => {
      if (from === to) return;
      setOrder((current) => {
        const fromIndex = current.findIndex((w) => w.widget_code === from);
        const toIndex = current.findIndex((w) => w.widget_code === to);
        if (fromIndex < 0 || toIndex < 0) return current;
        const next = [...current];
        const [moved] = next.splice(fromIndex, 1);
        next.splice(toIndex, 0, moved);
        void persist(next, current);
        return next;
      });
    },
    [persist],
  );

  const toggleWidth = useCallback(
    (code: string) => {
      setOrder((current) => {
        const next = current.map((widget) =>
          widget.widget_code === code
            ? { ...widget, width: widthOf(widget) === 2 ? 1 : 2 }
            : widget,
        );
        void persist(next, current);
        return next;
      });
    },
    [persist, widthOf],
  );

  const reset = useCallback(async () => {
    const previous = order;
    setSaving(true);
    setError(null);
    try {
      // Ancho de fábrica y el orden del catálogo: se manda `width` de fábrica y
      // el orden actual del servidor, que es lo que el catálogo dictó.
      await api.put(`/kpis/layout?country=${country.code}`, {
        placements: widgets.map((widget, index) => ({
          widget_code: widget.widget_code,
          sort_order: index + 1,
          width: defaultFullWidth.has(widget.widget_code) ? 2 : 1,
          hidden: false,
        })),
      });
      onSaved?.();
    } catch {
      setOrder(previous);
      setError("No se pudo restablecer el tablero.");
    } finally {
      setSaving(false);
    }
  }, [country.code, widgets, defaultFullWidth, onSaved, order]);

  return (
    <>
      {(error || saving || customised) && (
        <div className="mb-2 flex items-center justify-end gap-3 text-xs">
          {error && <span className="text-negative-ink">{error}</span>}
          {saving && !error && (
            <span className="text-ink-faint">Guardando…</span>
          )}
          {customised && !saving && !error && (
            <button
              type="button"
              onClick={() => void reset()}
              className="text-ink-muted underline decoration-dotted hover:text-ink"
            >
              Restablecer el orden
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-5">
        {order.map((widget) => {
          const wide = widthOf(widget) === 2;
          const isDragging = dragging === widget.widget_code;
          const isOver = over === widget.widget_code && !isDragging;
          return (
            <div
              key={widget.widget_code}
              draggable
              onDragStart={(event) => {
                setDragging(widget.widget_code);
                event.dataTransfer.effectAllowed = "move";
                // Firefox no arranca el arrastre sin datos en el portapapeles.
                event.dataTransfer.setData("text/plain", widget.widget_code);
              }}
              onDragEnd={() => {
                setDragging(null);
                setOver(null);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                if (over !== widget.widget_code) setOver(widget.widget_code);
              }}
              onDragLeave={() => {
                if (over === widget.widget_code) setOver(null);
              }}
              onDrop={(event) => {
                event.preventDefault();
                const from =
                  event.dataTransfer.getData("text/plain") || dragging || "";
                if (from) move(from, widget.widget_code);
                setDragging(null);
                setOver(null);
              }}
              className={cx(
                "group relative min-w-0 transition-opacity",
                wide && "lg:col-span-2",
                isDragging && "opacity-40",
                // El hueco donde caería: un borde, no un salto de layout, para
                // que la tarjeta bajo el cursor no se mueva mientras se apunta.
                isOver && "rounded-card ring-2 ring-accent ring-offset-2",
              )}
            >
              {/* Aparece al pasar el mouse: un control siempre visible en cada
                  tarjeta convertiría el tablero en una consola de edición. */}
              <button
                type="button"
                onClick={() => toggleWidth(widget.widget_code)}
                title={wide ? "Ocupar una columna" : "Ocupar dos columnas"}
                aria-label={
                  wide
                    ? `${widget.title}: ocupar una columna`
                    : `${widget.title}: ocupar dos columnas`
                }
                className="absolute right-2 top-2 z-10 hidden rounded-md border border-line-strong bg-surface px-2 py-1 text-xs text-ink-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100 lg:block"
              >
                {wide ? "◧" : "◨"}
              </button>
              <WidgetRenderer widget={widget} country={country} />
            </div>
          );
        })}
      </div>
    </>
  );
}
