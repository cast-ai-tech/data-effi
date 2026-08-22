"use client";

/**
 * Conversión de monedas: cada moneda contra el dólar y contra el peso colombiano.
 *
 * WHY BOTH COLUMNS
 * The consolidated roll-up converts to USD, so that column is what the totals
 * are built on. But the operator prices, pays and thinks in pesos, so "1 GTQ =
 * 510 COP" is the number that actually answers a question. Showing only the
 * dollar would force a second calculation in the head every time.
 *
 * WHY THE DATE IS ON EVERY ROW
 * A rate is a fact with an expiry. One from four days ago is not wrong, it is
 * old, and the difference matters when a margin is 6%. Stale rows say so out
 * loud instead of looking identical to fresh ones.
 *
 * WHY YOU CAN OVERWRITE ONE
 * The bolívar. VES moves faster than any daily feed, and the official rate is
 * not the one the street uses. Whoever operates there knows which one applies.
 */

import { useState } from "react";

import { Button, Card, Chip, SkeletonRows } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { FxRate, User } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = {
  trm_oficial: "TRM · Superfinanciera",
  oficial_bcrp: "Oficial · BCRP",
  oficial_bcch: "Oficial · Banco Central de Chile",
  oficial_banguat: "Oficial · Banguat",
  oficial_banxico: "Oficial · Banxico",
  oficial_bccr: "Oficial · BCCR",
  api: "Proveedor internacional",
  manual: "Puesta a mano",
  carried_forward: "Arrastrada del día anterior",
};

/** Las que vienen de un banco central se marcan distinto: no es lo mismo la
 *  tasa contra la que se miden los libros que una cotización de mercado. */
const OFICIAL = new Set(Object.keys(SOURCE_LABEL).filter((k) => k.startsWith("oficial_") || k === "trm_oficial"));

export function FxRatesSection({ onError }: { onError: (message: string) => void }) {
  const { data: user } = useApi<User>("/auth/me");
  const { data: rates, loading, reload } = useApi<FxRate[]>("/config/fx");
  const [editing, setEditing] = useState<string | null>(null);

  const canEdit = (user?.capabilities ?? []).includes("config");

  return (
    <Card
      title="Conversión de monedas"
      subtitle="Cuánto vale cada moneda en dólares y en pesos colombianos"
    >
      {loading && <SkeletonRows rows={4} />}

      {!loading && (rates?.length ?? 0) === 0 && (
        <p className="text-[12px] text-ink-dim">
          Todavía no hay monedas. Activa un país en la sección de arriba.
        </p>
      )}

      {!loading && rates && rates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-ink-dim">
                <th className="py-2 font-semibold">Moneda</th>
                <th className="py-2 font-semibold">Países</th>
                <th className="py-2 text-right font-semibold">1 unidad en USD</th>
                <th className="py-2 text-right font-semibold">1 unidad en COP</th>
                <th className="py-2 text-right font-semibold">1 USD equivale a</th>
                <th className="py-2 font-semibold">Actualizada</th>
                {canEdit && <th className="py-2" />}
              </tr>
            </thead>
            <tbody>
              {rates.map((rate) => (
                <FxRow
                  key={rate.currency_code}
                  rate={rate}
                  canEdit={canEdit}
                  editing={editing === rate.currency_code}
                  onEdit={() => setEditing(rate.currency_code)}
                  onCancel={() => setEditing(null)}
                  onSaved={() => {
                    setEditing(null);
                    reload();
                  }}
                  onError={onError}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] text-ink-dim">
        Cada moneda usa la tasa <strong>oficial de su banco central</strong> cuando
        ese banco la publica: la TRM de la Superfinanciera en Colombia, el BCRP en
        Perú, el dólar observado del Banco Central en Chile y el Banguat en
        Guatemala. Las demás vienen de un proveedor internacional, y cada fila dice
        cuál de las dos cosas es. Se actualizan solas cada día a las 10:05 UTC; una
        que no se pueda traer se arrastra del día anterior y queda marcada — nunca
        se inventa un valor, porque una tasa inventada deforma en silencio todos
        los totales que la usan.
      </p>
    </Card>
  );
}

function FxRow({
  rate,
  canEdit,
  editing,
  onEdit,
  onCancel,
  onSaved,
  onError,
}: {
  rate: FxRate;
  canEdit: boolean;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [value, setValue] = useState(
    rate.per_usd ? String(Number(rate.per_usd.toFixed(4))) : "",
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    const parsed = Number(value.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      onError("Escribe cuántas unidades equivalen a un dólar, por ejemplo 3900");
      return;
    }
    setSaving(true);
    try {
      await api.put("/config/fx", {
        currency_code: rate.currency_code,
        per_usd: parsed,
      });
      onSaved();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo guardar la tasa");
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr className="border-b border-line-subtle last:border-0">
      <td className="py-2.5">
        <span className="font-semibold text-ink-2">{rate.currency_code}</span>
        <span className="ml-1.5 text-ink-dim">{rate.currency_symbol}</span>
      </td>

      <td className="py-2.5">
        <span className="whitespace-nowrap">
          {rate.country_codes.map((code) => (
            <span key={code} className="mr-1" title={code}>
              {countryFlag(code)}
            </span>
          ))}
        </span>
      </td>

      <td className="py-2.5 text-right tabular-nums">
        {rate.to_usd === null ? <SinTasa /> : formatRate(rate.to_usd)}
      </td>

      <td className="py-2.5 text-right tabular-nums">
        {rate.to_cop === null ? <SinTasa /> : formatRate(rate.to_cop)}
      </td>

      <td className="py-2.5 text-right tabular-nums">
        {editing ? (
          <input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoFocus
            className="w-28 rounded-lg border border-line-subtle bg-transparent px-2 py-1 text-right text-[12px] text-ink outline-none focus:border-line"
          />
        ) : rate.per_usd === null ? (
          <SinTasa />
        ) : (
          <>
            {formatRate(rate.per_usd)}{" "}
            <span className="text-ink-dim">{rate.currency_code}</span>
          </>
        )}
      </td>

      <td className="py-2.5">
        {rate.rate_date ? (
          <span className="flex items-center gap-1.5">
            <span className="text-ink-dim">{rate.rate_date}</span>
            {rate.is_stale && <Chip tone="warning">Desactualizada</Chip>}
            {rate.source && (
              <Chip tone={OFICIAL.has(rate.source) ? "positive" : "neutral"}>
                {SOURCE_LABEL[rate.source] ?? rate.source}
              </Chip>
            )}
          </span>
        ) : (
          <span className="text-ink-dim">—</span>
        )}
      </td>

      {canEdit && (
        <td className="py-2.5 text-right">
          {editing ? (
            <span className="flex items-center justify-end gap-2">
              <Button onClick={save} disabled={saving}>
                {saving ? "…" : "Guardar"}
              </Button>
              <button
                type="button"
                onClick={onCancel}
                className="text-[11.5px] text-ink-dim underline hover:text-ink-2"
              >
                Cancelar
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={onEdit}
              className="text-[11.5px] text-ink-dim underline hover:text-ink-2"
            >
              Fijar a mano
            </button>
          )}
        </td>
      )}
    </tr>
  );
}

function SinTasa() {
  return <span className="text-ink-dim">sin tasa</span>;
}

/**
 * Una tasa se lee con los decimales que necesita, no con dos siempre.
 *
 * El peso vale 0,00027 dólares y el dólar vale 3.900 pesos: forzar el mismo
 * formato a los dos convierte uno de ellos en "0,00" o en un muro de ceros.
 */
function formatRate(value: number): string {
  const digits = value >= 100 ? 2 : value >= 1 ? 4 : 8;
  return value.toLocaleString("es-CO", {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  });
}
