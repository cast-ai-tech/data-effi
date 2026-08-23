"use client";

/**
 * The holding, added up: every company the reader may see, in one currency.
 *
 * WHY EVERY FIGURE SAYS USD
 * A company in Guatemala bills quetzales and one in Colombia bills pesos.
 * Adding those without saying what they were converted into is how a
 * consolidated total becomes fiction, so the currency sits on the tile, not in
 * a footnote - and a company whose rate is missing is named out loud rather
 * than folded in at 1:1 (`unavailable`, and the "sin tasa" chip per row).
 *
 * WHO SEES WHAT
 * The operator sees every company. A partner sees exactly the ones they belong
 * to, which is why this screen exists separately from the per-company
 * dashboard: "el global es mío" is enforced by the API, not hidden here.
 */

import Link from "next/link";
import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Card, Chip, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useRangedApi } from "@/lib/date-range";
import { countryFlag, formatNumber, formatPercent } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { OrgSummary, Tokens, User } from "@/lib/types";

/** USD, grouped the way every other screen groups thousands. */
function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const body = formatNumber(Math.abs(value), undefined, 0);
  return `${value < 0 ? "-" : ""}$ ${body}`;
}

export default function OrganizacionPage() {
  const { data, loading } = useRangedApi<OrgSummary>("/org/summary");
  const { data: user } = useApi<User>("/auth/me");

  const totals = data?.totals;
  const companies = data?.by_tenant ?? [];

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-[22px] font-bold tracking-tight">
          {data?.org_name ?? "Organización"}
        </h1>
        <p className="mt-1 text-[12px] text-ink-dim">
          {companies.length > 0
            ? `${companies.length} ${companies.length === 1 ? "sociedad" : "sociedades"} · todo convertido a ${data?.base_currency ?? "USD"}`
            : "Consolidado de todas tus sociedades"}
        </p>
      </header>

      {loading && <SkeletonRows rows={4} />}

      {!loading && companies.length === 0 && (
        <Card>
          <EmptyState
            title="Todavía no hay sociedades que consolidar"
            instruction="Crea una sociedad por cada operación o socio, y aquí verás la suma de todas."
          />
        </Card>
      )}

      {!loading && data && companies.length > 0 && (
        <>
          {/* Named before any total is read: these companies are NOT in it. */}
          {data.unavailable.length > 0 && (
            <div className="mb-4 rounded-[10px] border border-line-strong bg-surface px-4 py-3 text-[12px] text-ink-2">
              No se pudieron leer, y <strong>no están sumadas</strong>:{" "}
              {data.unavailable.join(", ")}.
            </div>
          )}

          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Tile label="Guías" value={formatNumber(totals?.shipments ?? 0, undefined, 0)} />
            <Tile
              label="Entregadas"
              value={formatNumber(totals?.delivered ?? 0, undefined, 0)}
              hint={formatPercent(totals?.delivery_rate_pct)}
            />
            <Tile label="Ingresos" value={usd(totals?.revenue_usd)} hint="USD" />
            <Tile
              label="Contribución"
              value={usd(totals?.contribution_usd)}
              hint={
                totals?.my_share_usd != null
                  ? `tu parte: ${usd(totals.my_share_usd)}`
                  : "USD"
              }
              strong
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
            <Card title="Por sociedad" subtitle="Entra a una sociedad para ver su detalle">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[12px]">
                  <thead>
                    <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-[0.06em] text-ink-faint">
                      <th className="py-2 pr-3 font-semibold">Sociedad</th>
                      <th className="py-2 pr-3 text-right font-semibold">Guías</th>
                      <th className="py-2 pr-3 text-right font-semibold">Entrega</th>
                      <th className="py-2 pr-3 text-right font-semibold">Contribución</th>
                      <th className="py-2 text-right font-semibold">Tu parte</th>
                    </tr>
                  </thead>
                  <tbody>
                    {companies.map((row) => (
                      <tr key={row.tenant_id} className="border-b border-line-subtle/60">
                        <td className="py-2.5 pr-3">
                          <OpenCompany
                            tenantId={row.tenant_id}
                            name={row.name}
                            disabled={
                              !(user?.workspaces ?? []).some(
                                (ws) => ws.tenant_id === row.tenant_id,
                              )
                            }
                          />
                          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                            <span className="text-[10.5px] text-ink-dim">
                              {row.countries.map((code) => countryFlag(code)).join(" ")}{" "}
                              {row.countries.join(", ") || "sin países"}
                            </span>
                            {row.fx_missing && (
                              <Chip tone="warning">sin tasa de cambio</Chip>
                            )}
                          </div>
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular-nums">
                          {formatNumber(row.shipments, undefined, 0)}
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular-nums">
                          {formatPercent(row.delivery_rate_pct)}
                        </td>
                        <td className="py-2.5 pr-3 text-right font-semibold tabular-nums">
                          {usd(row.contribution_usd)}
                        </td>
                        <td className="py-2.5 text-right tabular-nums text-ink-muted">
                          {row.share_pct
                            ? `${usd(row.my_share_usd)} (${row.share_pct}%)`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card title="Por país" subtitle="El mismo país en dos sociedades se suma aquí">
              {data.by_country.length === 0 && (
                <p className="text-[12px] text-ink-dim">
                  Todavía no hay datos cargados en ningún país.
                </p>
              )}
              <ul className="flex flex-col gap-2.5">
                {data.by_country.map((row) => (
                  <li
                    key={row.country_code}
                    className="flex items-start justify-between gap-3 border-b border-line-subtle/60 pb-2.5 last:border-0 last:pb-0"
                  >
                    <div className="min-w-0">
                      <p className="text-[12.5px] font-semibold">
                        <span className="mr-1.5">{countryFlag(row.country_code)}</span>
                        {row.country_name}
                      </p>
                      <p className="mt-0.5 truncate text-[10.5px] text-ink-dim">
                        {row.tenants.join(" · ")}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-[12.5px] font-semibold tabular-nums">
                        {usd(row.contribution_usd)}
                      </p>
                      <p className="text-[10.5px] tabular-nums text-ink-dim">
                        {formatNumber(row.shipments, undefined, 0)} guías ·{" "}
                        {formatPercent(row.delivery_rate_pct)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          {user?.is_org_admin && (
            <p className="mt-4 text-[11.5px] text-ink-dim">
              Eres el usuario maestro:{" "}
              <Link href="/usuarios" className="text-accent">
                administra quién entra a cada sociedad
              </Link>
              .
            </p>
          )}
        </>
      )}
    </AppShell>
  );
}

function Tile({
  label,
  value,
  hint,
  strong,
}: {
  label: string;
  value: string;
  hint?: string;
  strong?: boolean;
}) {
  return (
    <div
      className={cx(
        "rounded-[12px] border bg-surface px-4 py-3.5",
        strong ? "border-accent/40" : "border-line",
      )}
    >
      <p className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </p>
      <p className="mt-1 text-[20px] font-bold tracking-tight tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-ink-dim">{hint}</p>}
    </div>
  );
}

/**
 * Opening a company means switching the session into it - a new token carrying
 * that company's role - so this is a button, not a link. Disabled for a company
 * the operator can consolidate but holds no membership in: the roll-up may
 * include it, the dashboard cannot open it.
 */
function OpenCompany({
  tenantId,
  name,
  disabled,
}: {
  tenantId: string;
  name: string;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);

  if (disabled) {
    return <span className="text-[12.5px] font-semibold text-ink-2">{name}</span>;
  }

  async function open() {
    setBusy(true);
    try {
      await api.post<Tokens>("/auth/switch", { tenant_id: tenantId });
      window.location.assign("/global");
    } catch {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={open}
      disabled={busy}
      className="text-left text-[12.5px] font-semibold text-ink-2 hover:text-accent disabled:opacity-60"
    >
      {name}
      {busy && " …"}
    </button>
  );
}
