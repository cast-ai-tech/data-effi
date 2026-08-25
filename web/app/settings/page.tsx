"use client";

/**
 * Countries, people and the maturation window.
 *
 * Connections used to live here too. They earned their own screen once the
 * catalogue grew past a dropdown - see app/connections/page.tsx.
 */

import Link from "next/link";
import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { FxRatesSection } from "@/components/FxRatesSection";
import { Button, Card, Chip, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Country, User } from "@/lib/types";

export default function SettingsPage() {
  const [error, setError] = useState<string | null>(null);

  return (
    <AppShell>
      <PageHeader
        title="Configuración"
        subtitle="Países, personas y cuántos días esperar antes de dar una guía por cerrada."
      />

      {error && (
        <p className="mb-4 rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
          {error}
        </p>
      )}

      <div className="space-y-4">
        <CountriesSection onError={setError} />
        <FxRatesSection onError={setError} />
        <ConnectionsLink />
        <UsersSection />
        <Tier3Notice />
      </div>
    </AppShell>
  );
}

function CountriesSection({ onError }: { onError: (message: string) => void }) {
  const { data, loading, reload } = useApi<Country[]>("/config/countries");

  async function toggle(country: Country, active: boolean) {
    try {
      await api.put("/config/countries", {
        country_code: country.code,
        is_active: active,
      });
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo guardar");
    }
  }

  async function applyMaturation(country: Country) {
    if (!country.maturation_days_suggested) return;
    try {
      await api.put("/config/countries", {
        country_code: country.code,
        is_active: true,
        maturation_days: country.maturation_days_suggested,
      });
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo aplicar");
    }
  }

  return (
    <Card title="Países" subtitle="Cada país trae su moneda, sus formatos y sus plataformas">
      {loading && <SkeletonRows rows={4} />}
      {!loading &&
        (data ?? []).map((country) => (
          <div
            key={country.code}
            className="flex flex-wrap items-center justify-between gap-3 border-t border-line-row py-3 first:border-t-0"
          >
            <div className="min-w-0">
              <p className="text-base font-medium text-ink">
                {countryFlag(country.code)} {country.name}
                <span className="ml-2 text-xs text-ink-dim">
                  {country.currency_code} · {country.date_format}
                </span>
              </p>
              {country.is_active && (
                <p className="mt-0.5 text-xs text-ink-dim">
                  Maduración: {country.maturation_days ?? 21} días
                  {country.maturation_days_suggested &&
                    country.maturation_days_suggested !== country.maturation_days && (
                      <span className="ml-2 text-warning-ink">
                        medimos {country.maturation_days_suggested}
                      </span>
                    )}
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              {country.is_active &&
                country.maturation_days_suggested &&
                country.maturation_days_suggested !== country.maturation_days && (
                  <Button size="sm" variant="ghost" onClick={() => applyMaturation(country)}>
                    Aplicar {country.maturation_days_suggested} días
                  </Button>
                )}
              <Button
                size="sm"
                variant={country.is_active ? "ghost" : "primary"}
                onClick={() => toggle(country, !country.is_active)}
              >
                {country.is_active ? "Desactivar" : "Activar"}
              </Button>
            </div>
          </div>
        ))}
    </Card>
  );
}

/**
 * Connections moved out. A link is left behind because this is where anyone who
 * used the old screen will look first.
 */
function ConnectionsLink() {
  return (
    <Card title="Conexiones" subtitle="Ahora viven en su propia pantalla">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-lg text-sm leading-relaxed text-ink-2">
          El catálogo completo —pauta, tiendas, CRM, fulfillment, automatización y
          archivos— y el estado de cada fuente están en Conexiones. Las credenciales
          siguen viviendo en el servidor, nunca en una pantalla.
        </p>
        <Link
          href="/connections"
          className="inline-flex shrink-0 items-center rounded-control border border-line-input px-3 py-1.5 text-sm font-semibold text-ink-2 no-underline transition-colors hover:border-accent hover:text-accent-ink"
        >
          Ir a Conexiones
        </Link>
      </div>
    </Card>
  );
}

function UsersSection() {
  const { data, loading } = useApi<User[]>("/config/users");

  const roleLabel: Record<string, string> = {
    owner: "Dueño",
    analyst: "Analista",
    viewer: "Solo lectura",
  };

  return (
    <Card title="Personas" subtitle="Se entra por invitación; no hay registro abierto">
      {loading && <SkeletonRows rows={2} />}
      {(data ?? []).map((user) => (
        <div
          key={user.id}
          className="flex items-center justify-between gap-3 border-t border-line-row py-2.5 first:border-t-0"
        >
          <div className="min-w-0">
            <p className="truncate text-base text-ink">{user.full_name ?? user.email}</p>
            <p className="truncate text-xs text-ink-dim">{user.email}</p>
          </div>
          <Chip tone={user.role === "owner" ? "accent" : "neutral"}>
            {roleLabel[user.role] ?? user.role}
          </Chip>
        </div>
      ))}
    </Card>
  );
}

function Tier3Notice() {
  return (
    <Card title="Conexiones con tu usuario y contraseña (riesgo alto)" help="tier">
      <div className="space-y-2 text-sm leading-relaxed text-ink-2">
        <p>
          Este tipo de conexión entra con <b>tu usuario y contraseña</b> a una plataforma que
          no ofrece una conexión oficial. Master Data se identifica, espera entre peticiones y se detiene apenas la
          plataforma rechaza la sesión: nunca disfraza el tráfico.
        </p>
        <p>
          Aun así, muchas plataformas prohíben el acceso automatizado incluso a tus propios
          datos. Podrían suspenderte la cuenta. La decisión es tuya, y siempre existe la
          alternativa: exportar el reporte a mano y subirlo.
        </p>
        <p className="text-ink-dim">
          Si quieres el detalle técnico, pídele a quien instaló Master Data el documento
          <code className="text-ink-muted"> docs/tier3-politica.md</code>.
        </p>
      </div>
    </Card>
  );
}
