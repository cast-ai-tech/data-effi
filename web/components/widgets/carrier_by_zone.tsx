"use client";

/**
 * The same carrier, city by city.
 *
 * The league table says who is best in the country. This says who is best in
 * each place you actually ship to, which is the only version of the question
 * an operator can act on: you do not change carrier for Colombia, you change
 * it for Barranquilla.
 *
 * Not date-ranged: the view is fixed to the last 90 days so every zone has
 * enough terminal guides to be compared. The subtitle says so.
 */

import { useMemo, useState } from "react";

import {
  Card,
  Chip,
  EmptyState,
  ErrorState,
  MicroBar,
  ShowMore,
  SkeletonRows,
  cx,
  useShowMore,
} from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { MIN_TERMINAL_FOR_BEST, bestCarrierByZone, zoneKey } from "@/lib/carrier-zones";
import { formatMoney, formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { CarrierZoneRow } from "@/lib/types";

const EMPTY: CarrierZoneRow[] = [];

const SUBTITLE = "Quién entrega mejor en cada ciudad. Últimos 90 días, sin filtro de fechas.";

function deliveryTone(pct: number | null): "positive" | "warning" | "negative" {
  if (pct === null || !Number.isFinite(pct)) return "negative";
  if (pct >= 75) return "positive";
  if (pct >= 60) return "warning";
  return "negative";
}

export default function CarrierByZone({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useApi<{ rows: CarrierZoneRow[] }>(
    `/kpis/carrier-by-zone?country=${countryCode}`,
    [countryCode],
  );
  const [level1, setLevel1] = useState<string>("");

  const rows = useMemo(() => data?.rows ?? EMPTY, [data]);

  const zones = useMemo(() => {
    const names = new Set(rows.map((row) => row.level1_name));
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [rows]);

  const best = useMemo(() => bestCarrierByZone(rows, "city"), [rows]);

  const visible = useMemo(() => {
    const filtered = level1 ? rows.filter((row) => row.level1_name === level1) : rows;
    // Biggest cities first, then the carriers inside each by volume: the
    // reader compares carriers within a city, never cities within a carrier.
    const cityVolume = new Map<string, number>();
    for (const row of filtered) {
      const key = zoneKey(row, "city");
      cityVolume.set(key, (cityVolume.get(key) ?? 0) + row.shipments);
    }
    return [...filtered].sort((a, b) => {
      const ka = zoneKey(a, "city");
      const kb = zoneKey(b, "city");
      const volume = (cityVolume.get(kb) ?? 0) - (cityVolume.get(ka) ?? 0);
      if (volume !== 0) return volume;
      if (ka !== kb) return ka.localeCompare(kb);
      return b.shipments - a.shipments;
    });
  }, [rows, level1]);

  const paging = useShowMore(visible);

  const level1Label = country.geo_level1_label || "Zona";

  if (loading && !data) {
    return (
      <Card title="Transportadora por zona" subtitle={SUBTITLE}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Transportadora por zona" subtitle={SUBTITLE}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (rows.length === 0) {
    return (
      <Card title="Transportadora por zona" subtitle={SUBTITLE}>
        <EmptyState
          title="Todavía no hay guías por zona"
          instruction="Cuando las guías traigan ciudad de destino y transportadora, aquí se ve cuál entrega mejor en cada una."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Transportadora por zona"
      subtitle={SUBTITLE}
      bodyClassName="p-0"
      actions={
        <select
          value={level1}
          onChange={(event) => setLevel1(event.target.value)}
          aria-label={`Filtrar por ${level1Label.toLowerCase()}`}
          className="rounded-[8px] border border-line-input bg-surface px-2 py-1 text-[11.5px] text-ink-2 focus:border-accent focus:outline-none"
        >
          <option value="">Todo el país</option>
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-[12px]">
          <thead>
            <tr>
              {["Ciudad", "Transportadora", "Guías", "% entrega", "Días prom.", "Flete prom."].map(
                (header, index) => (
                  <th
                    key={header}
                    scope="col"
                    className={cx(
                      "px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim",
                      index >= 2 ? "text-right" : "text-left",
                    )}
                  >
                    {header}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {paging.shown.map((row, index) => {
              const key = zoneKey(row, "city");
              const winner = best.get(key);
              const isBest = winner?.carrier_name === row.carrier_name;
              const firstOfCity =
                index === 0 || zoneKey(paging.shown[index - 1], "city") !== key;
              return (
                <tr
                  key={`${key}|${row.carrier_id ?? row.carrier_name}`}
                  className={cx("border-t", firstOfCity ? "border-line-strong" : "border-line-row")}
                >
                  <td className="max-w-[200px] px-3 py-2 text-left">
                    {firstOfCity && (
                      <>
                        <span className="block truncate font-medium text-ink-body">
                          {row.city_name || "Sin ciudad"}
                        </span>
                        <span className="block truncate text-[10.5px] text-ink-dim">
                          {row.level1_name}
                        </span>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2 text-left text-ink-2">
                    <span className="inline-flex items-center gap-1.5">
                      {row.carrier_name}
                      {isBest && <Chip tone="accent">Mejor aquí</Chip>}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {formatNumber(row.shipments, country, 0)}
                  </td>
                  <td className="px-3 py-2">
                    <MicroBar
                      value={row.delivery_rate_pct}
                      max={100}
                      tone={deliveryTone(row.delivery_rate_pct)}
                      label={formatPercent(row.delivery_rate_pct)}
                    />
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {formatNumber(row.avg_days_to_deliver, country, 1)}
                  </td>
                  <td className="px-3 py-2 text-right text-ink-2">
                    {formatMoney(row.avg_freight, country)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="px-3">
        <ShowMore
          remaining={paging.remaining}
          total={paging.total}
          shownCount={paging.shown.length}
          onMore={paging.showMore}
          onCollapse={paging.collapse}
          noun="filas"
        />
      </div>

      <p className="border-t border-line-subtle px-3 py-2 text-[10.5px] leading-snug text-ink-dim">
        «Mejor aquí» compara transportadoras con al menos {MIN_TERMINAL_FOR_BEST} guías
        cerradas en esa ciudad. Con menos, el porcentaje es suerte, no medición.
      </p>
    </Card>
  );
}
