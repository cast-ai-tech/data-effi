"use client";

/** Multi-country view: the four numbers that matter, then a country ranking. */

import Link from "next/link";
import { useMemo } from "react";

import { AppShell } from "@/components/AppShell";
import {
  BasisBand,
  BasisCaption,
  DateBasisFrame,
  useDateBasisNote,
} from "@/components/DateBasisNote";
import GlobalSummary from "@/components/widgets/global_summary";
import { HelpTip } from "@/components/HelpTip";
import { Card, Chip, EmptyState, SkeletonRows, StatusDot } from "@/components/ui";
import { TIER_LABELS, type GlossaryKey } from "@/lib/glossary";
import { useRangedApi } from "@/lib/date-range";
import { FALLBACK_COUNTRY, countryFlag, formatNumber, formatPercent, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Brief, Connection, Country, GlobalRow } from "@/lib/types";

export default function GlobalPage() {
  // These four tiles are numbers on a filtered screen like any other, so they
  // go through the range too, and carry the same disclosure as a dashboard card.
  const { data: rows, loading, dateBasis } = useRangedApi<GlobalRow[]>("/kpis/global");
  const totalsNote = useDateBasisNote(dateBasis);
  const { data: countries } = useApi<Country[]>("/config/countries");
  const { data: connections } = useApi<Connection[]>("/config/connections");

  const active = useMemo(
    () => (countries ?? []).filter((country) => country.is_active),
    [countries],
  );

  const totals = useMemo(() => {
    const list = rows ?? [];
    const shipments = list.reduce((sum, row) => sum + row.shipments, 0);
    const delivered = list.reduce((sum, row) => sum + row.delivered, 0);
    const returned = list.reduce((sum, row) => sum + row.returned, 0);
    // The five words of migration 045: "en tránsito" is only what moves;
    // a guide stopped with an issue is a novedad, not transit.
    const inTransit = list.reduce((sum, row) => sum + row.en_transito, 0);
    const issues = list.reduce((sum, row) => sum + row.novedad, 0);
    const usd = list.reduce((sum, row) => sum + (row.contribution_usd ?? 0), 0);
    const missingFx = list.some((row) => row.fx_missing);
    return { shipments, delivered, returned, inTransit, issues, usd, missingFx };
  }, [rows]);

  const country = active[0] ?? null;

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Global</h1>
        <p className="mt-1 text-sm text-ink-dim">
          {active.length > 0
            ? `${active.length} ${active.length === 1 ? "país activo" : "países activos"}`
            : "Aún no has activado ningún país"}
        </p>
      </header>

      {loading && <SkeletonRows rows={4} />}

      {!loading && active.length === 0 && (
        <Card>
          <EmptyState
            title="Crea tu primera empresa"
            instruction="Un nombre y el país donde opera. Después subes tus reportes."
            action={
              <Link
                href="/empresas/nueva"
                className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
              >
                Crear empresa
              </Link>
            }
          />
        </Card>
      )}

      {!loading && active.length > 0 && (
        <>
          {totalsNote?.kind === "band" && (
            <div className="mb-3">
              <BasisBand note={totalsNote} standalone />
            </div>
          )}

          <div className="mb-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="Contribución (USD)"
              help="contribucion"
              value={
                totals.usd !== 0
                  ? `$ ${formatNumber(totals.usd, { ...FALLBACK_COUNTRY, decimal_places: 0 })}`
                  : "—"
              }
              hint={totals.missingFx ? "Falta la tasa de cambio de un país" : "Convertido a dólares"}
              tone={totals.usd < 0 ? "negative" : "positive"}
            />
            <StatTile
              label="Guías despachadas"
              help="guia"
              value={formatNumber(totals.shipments, { ...FALLBACK_COUNTRY, decimal_places: 0 })}
              hint="Paquetes que salieron en el rango elegido"
            />
            <StatTile
              label="% entregado"
              value={formatPercent(
                totals.delivered + totals.returned > 0
                  ? (totals.delivered / (totals.delivered + totals.returned)) * 100
                  : null,
              )}
              hint="De las guías que ya se cerraron"
            />
            <StatTile
              label="En camino"
              help="novedad"
              value={formatNumber(totals.inTransit, { ...FALLBACK_COUNTRY, decimal_places: 0 })}
              hint={
                totals.issues > 0
                  ? `Todavía pueden entregarse · ${formatNumber(totals.issues, { ...FALLBACK_COUNTRY, decimal_places: 0 })} con novedad`
                  : "Todavía pueden entregarse"
              }
            />
          </div>

            {totalsNote?.kind === "caption" && <BasisCaption note={totalsNote} />}
          </div>

          {country && (
            <div className="mb-4">
              {/* Rendered outside WidgetRenderer, so it needs its own frame to
                  carry the same disclosure the dashboard cards get. */}
              <DateBasisFrame>
                <GlobalSummary
                  countryCode={country.code}
                  country={country}
                  state="available"
                  message={null}
                />
              </DateBasisFrame>
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-2">
            <BriefCard countryCode={active[0]?.code ?? null} />
            <ConnectionsCard connections={connections ?? []} />
          </div>
        </>
      )}
    </AppShell>
  );
}

function StatTile({
  label,
  value,
  hint,
  tone,
  help,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "positive" | "negative";
  help?: GlossaryKey;
}) {
  return (
    <div className="rounded-card border border-line bg-surface p-4 shadow-card">
      <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.06em] text-ink-muted">
        <span>{label}</span>
        {help && <HelpTip term={help} />}
      </p>
      <p
        className={
          "mt-1.5 text-3xl font-bold leading-none " +
          (tone === "negative" ? "text-negative-ink" : tone === "positive" ? "text-positive-ink" : "text-ink")
        }
      >
        {value}
      </p>
      <p className="mt-2 text-sm text-ink-muted">{hint}</p>
    </div>
  );
}

function BriefCard({ countryCode }: { countryCode: string | null }) {
  const { data, loading } = useApi<Brief>(
    countryCode ? `/ai/brief?country=${countryCode}` : null,
  );

  return (
    <Card title="Resumen del día" subtitle="Generado a partir de tus cifras, una vez al día">
      {loading && <SkeletonRows rows={3} />}
      {!loading && data && (
        <>
          <p className="whitespace-pre-line text-base leading-[1.65] text-ink-body">
            {data.summary}
          </p>
          {data.degraded && (
            <Chip tone="warning" className="mt-3">
              Modo degradado
            </Chip>
          )}
        </>
      )}
      {!loading && !data && (
        <p className="text-sm text-ink-dim">
          El resumen aparece cuando hay guías cargadas.
        </p>
      )}
    </Card>
  );
}

function ConnectionsCard({ connections }: { connections: Connection[] }) {
  const tone = (health: Connection["health"]) =>
    health === "ok"
      ? "positive"
      : health === "error"
        ? "negative"
        : health === "disabled"
          ? "neutral"
          : "warning";

  const label: Record<Connection["health"], string> = {
    ok: "Al día",
    stale: "Sin sincronizar",
    error: "Con error",
    never_synced: "Nunca sincronizó",
    disabled: "Desactivada",
  };

  return (
    <Card
      title="Salud de conexiones"
      actions={
        <Link href="/settings" className="text-sm no-underline">
          Configurar
        </Link>
      }
    >
      {connections.length === 0 ? (
        <EmptyState
          title="Sin conexiones"
          instruction="Conecta al menos una fuente para que los tableros tengan de dónde leer."
        />
      ) : (
        <div className="space-y-0">
          {connections.map((connection) => (
            <div
              key={connection.connection_id}
              className="flex items-center justify-between gap-3 border-t border-line-row py-2.5 first:border-t-0"
            >
              <div className="min-w-0">
                <p className="truncate text-base font-medium text-ink">
                  {countryFlag(connection.country_code)} {connection.connection_name}
                </p>
                <p className="text-xs text-ink-dim">
                  {connection.platform_name} · {TIER_LABELS[connection.tier] ?? `Tipo ${connection.tier}`}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <StatusDot tone={tone(connection.health)} />
                <span className="text-xs text-ink-muted">
                  {connection.last_sync_at
                    ? formatRelative(connection.last_sync_at)
                    : label[connection.health]}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
