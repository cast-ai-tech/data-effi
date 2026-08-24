"use client";

/**
 * Geographic traffic light.
 *
 * The country-level delivery rate is an average that hides everything worth
 * knowing. This widget breaks it down by level 1 (departamento, estado,
 * provincia - the label comes from `country.geo_level1_label`) and lets you open
 * one to see the cities underneath it, which is where a bad zone actually lives.
 *
 * The group light is NOT the average of its children's colours: it is weighted
 * by shipments, so a red city with 4 guías cannot paint a whole department red.
 */

import { useMemo, useState } from "react";

import {
  Card,
  EmptyState,
  ErrorState,
  MicroBar,
  SkeletonRows,
  StatusDot,
  cx,
} from "@/components/ui";
import type { WidgetProps } from "@/components/widgets/types";
import { bestCarrierByZone } from "@/lib/carrier-zones";
import { useRangedApi } from "@/lib/date-range";
import { formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { CarrierZoneRow, GeoRow, TrafficLight } from "@/lib/types";

type LightTone = "positive" | "warning" | "negative" | "neutral";

const LIGHT_TONE: Record<TrafficLight, LightTone> = {
  verde: "positive",
  amarillo: "warning",
  rojo: "negative",
  sin_datos: "neutral",
};

const LIGHT_LABEL: Record<TrafficLight, string> = {
  verde: "Entrega sana",
  amarillo: "Entrega en observación",
  rojo: "Entrega crítica",
  sin_datos: "Sin datos suficientes",
};

/** verde = 2, amarillo = 1, rojo = 0. `sin_datos` carries no weight at all. */
const LIGHT_SCORE: Record<TrafficLight, number | null> = {
  verde: 2,
  amarillo: 1,
  rojo: 0,
  sin_datos: null,
};

interface GeoGroup {
  key: string;
  name: string;
  shipments: number;
  delivered: number;
  deliveryRate: number | null;
  light: TrafficLight;
  cities: GeoRow[];
}

/** Shipment-weighted colour for a group, from the colours of its cities. */
function aggregateLight(cities: GeoRow[]): TrafficLight {
  let weight = 0;
  let score = 0;

  for (const city of cities) {
    const value = LIGHT_SCORE[city.traffic_light];
    if (value === null) continue;
    const cityWeight = Math.max(city.shipments, 1);
    weight += cityWeight;
    score += value * cityWeight;
  }

  if (weight === 0) return "sin_datos";
  const average = score / weight;
  if (average >= 1.5) return "verde";
  if (average >= 0.75) return "amarillo";
  return "rojo";
}

function groupByLevel1(rows: GeoRow[]): GeoGroup[] {
  const groups = new Map<string, GeoRow[]>();

  for (const row of rows) {
    const key = row.level1_name || "Sin clasificar";
    const bucket = groups.get(key);
    if (bucket) bucket.push(row);
    else groups.set(key, [row]);
  }

  return Array.from(groups.entries())
    .map(([name, cities]) => {
      const shipments = cities.reduce((sum, city) => sum + city.shipments, 0);
      const delivered = cities.reduce((sum, city) => sum + city.delivered, 0);
      return {
        key: name,
        name,
        shipments,
        delivered,
        deliveryRate: shipments > 0 ? (delivered / shipments) * 100 : null,
        light: aggregateLight(cities),
        cities: [...cities].sort((a, b) => b.shipments - a.shipments),
      };
    })
    .sort((a, b) => b.shipments - a.shipments);
}

export default function GeoTrafficLight({ countryCode, country }: WidgetProps) {
  const { data, error, loading, reload } = useRangedApi<GeoRow[]>(
    `/kpis/geo?country=${countryCode}`,
    [countryCode],
  );
  const [expanded, setExpanded] = useState<string | null>(null);

  const groups = useMemo(() => (data ? groupByLevel1(data) : []), [data]);

  // A red department is only half the finding; the other half is who could
  // deliver there instead. Measured over 90 days, not over the range, so the
  // note does not vanish the moment the reader narrows to last week.
  const { data: zoneRows } = useApi<CarrierZoneRow[]>(
    `/kpis/carrier-by-zone?country=${countryCode}`,
    [countryCode],
  );
  const bestByLevel1 = useMemo(() => bestCarrierByZone(zoneRows ?? []), [zoneRows]);

  const level1Label = country.geo_level1_label || "Zona";
  const subtitle = `Entrega por ${level1Label.toLowerCase()}; abra uno para ver sus ciudades`;

  if (loading) {
    return (
      <Card title="Semáforo geográfico" subtitle={subtitle}>
        <SkeletonRows rows={6} />
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Semáforo geográfico" subtitle={subtitle}>
        <ErrorState message={error.message} onRetry={reload} />
      </Card>
    );
  }

  if (groups.length === 0) {
    return (
      <Card title="Semáforo geográfico" subtitle={subtitle}>
        <EmptyState
          title="Sin cobertura geográfica todavía"
          instruction={`Las guías cargadas no traen ${level1Label.toLowerCase()} ni ciudad de destino. Revise el mapeo de columnas de destino en la conexión de guías para que Data Effi pueda ubicar cada despacho.`}
        />
      </Card>
    );
  }

  return (
    <Card title="Semáforo geográfico" subtitle={subtitle} bodyClassName="p-0">
      <div className="px-4 pt-3">
        <ColumnHeader level1Label={level1Label} />
      </div>

      <ul className="divide-y divide-line-row">
        {groups.map((group) => {
          const isOpen = expanded === group.key;
          return (
            <li key={group.key}>
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : group.key)}
                aria-expanded={isOpen}
                className={cx(
                  "flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors",
                  "hover:bg-white/[0.03] focus:outline-none focus-visible:bg-white/[0.05]",
                  isOpen && "bg-white/[0.03]",
                )}
              >
                <Caret open={isOpen} />
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-ink">
                  {group.name}
                </span>
                <span className="w-[130px] shrink-0">
                  <MicroBar
                    value={group.deliveryRate}
                    max={100}
                    tone={LIGHT_TONE[group.light]}
                    label={formatPercent(group.deliveryRate)}
                  />
                </span>
                <span className="w-[74px] shrink-0 text-right text-[12px] text-ink-2">
                  {formatNumber(group.shipments, country, 0)}
                </span>
                <span
                  className="w-[16px] shrink-0 text-right"
                  title={LIGHT_LABEL[group.light]}
                >
                  <StatusDot tone={LIGHT_TONE[group.light]} />
                  <span className="sr-only">{LIGHT_LABEL[group.light]}</span>
                </span>
              </button>

              {isOpen && bestByLevel1.get(group.key) && (
                <p className="bg-sunken/60 px-4 py-1.5 pl-10 text-[11px] text-ink-muted">
                  Mejor transportadora aquí:{" "}
                  <span className="font-semibold text-accent">
                    {bestByLevel1.get(group.key)!.carrier_name}
                  </span>{" "}
                  ({formatPercent(bestByLevel1.get(group.key)!.delivery_rate_pct)} de
                  entrega en 90 días)
                </p>
              )}

              {isOpen && (
                <ul className="bg-sunken/60 pb-1">
                  {group.cities.map((city) => (
                    <li
                      key={city.geo_id ?? `${group.key}-${city.city_name}`}
                      className="flex items-center gap-3 px-4 py-2 pl-10"
                    >
                      <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">
                        {city.city_name || "Sin ciudad"}
                      </span>
                      <span className="w-[130px] shrink-0">
                        <MicroBar
                          value={city.delivery_rate_pct}
                          max={100}
                          tone={LIGHT_TONE[city.traffic_light]}
                          label={formatPercent(city.delivery_rate_pct)}
                        />
                      </span>
                      <span className="w-[74px] shrink-0 text-right text-[12px] text-ink-muted">
                        {formatNumber(city.shipments, country, 0)}
                      </span>
                      <span
                        className="w-[16px] shrink-0 text-right"
                        title={LIGHT_LABEL[city.traffic_light]}
                      >
                        <StatusDot tone={LIGHT_TONE[city.traffic_light]} />
                        <span className="sr-only">{LIGHT_LABEL[city.traffic_light]}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function ColumnHeader({ level1Label }: { level1Label: string }) {
  return (
    <div className="flex items-center gap-3 border-b border-line-subtle pb-2 text-[10.5px] font-bold uppercase tracking-[0.06em] text-ink-faint">
      <span className="w-[14px] shrink-0" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{level1Label}</span>
      <span className="w-[130px] shrink-0 pl-[52px]">% entrega</span>
      <span className="w-[74px] shrink-0 text-right">Guías</span>
      <span className="w-[16px] shrink-0" aria-hidden />
    </div>
  );
}

function Caret({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      className={cx(
        "size-[14px] shrink-0 text-ink-dim transition-transform",
        open && "rotate-90",
      )}
      aria-hidden
    >
      <path
        d="M4.5 2.5 8 6l-3.5 3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
