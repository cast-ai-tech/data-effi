"use client";

/** Countries, connections, users and the maturation window. */

import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Button, Card, Chip, EmptyState, SkeletonRows, StatusDot } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Connection, Country, Platform, User } from "@/lib/types";

export default function SettingsPage() {
  const [error, setError] = useState<string | null>(null);

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-[22px] font-bold tracking-tight">Configuración</h1>
        <p className="mt-1 text-[12px] text-ink-dim">
          Países, conexiones, personas y cómo se mide la maduración.
        </p>
      </header>

      {error && (
        <p className="mb-4 rounded-[8px] border border-negative/30 bg-negative/[0.08] px-3 py-2 text-[12px] text-negative">
          {error}
        </p>
      )}

      <div className="space-y-4">
        <CountriesSection onError={setError} />
        <ConnectionsSection onError={setError} />
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
              <p className="text-[13px] font-medium text-ink">
                {countryFlag(country.code)} {country.name}
                <span className="ml-2 text-[11px] text-ink-dim">
                  {country.currency_code} · {country.date_format}
                </span>
              </p>
              {country.is_active && (
                <p className="mt-0.5 text-[11px] text-ink-dim">
                  Maduración: {country.maturation_days ?? 21} días
                  {country.maturation_days_suggested &&
                    country.maturation_days_suggested !== country.maturation_days && (
                      <span className="ml-2 text-warning">
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

function ConnectionsSection({ onError }: { onError: (message: string) => void }) {
  const { data: connections, loading, reload } = useApi<Connection[]>("/config/connections");
  const { data: countries } = useApi<Country[]>("/config/countries");
  const [adding, setAdding] = useState(false);

  const active = (countries ?? []).filter((country) => country.is_active);

  async function remove(connection: Connection) {
    try {
      await api.delete(`/config/connections/${connection.connection_id}`);
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo eliminar");
    }
  }

  const tone = (health: Connection["health"]) =>
    health === "ok"
      ? "positive"
      : health === "error"
        ? "negative"
        : health === "disabled"
          ? "neutral"
          : "warning";

  return (
    <Card
      title="Conexiones"
      subtitle="Las credenciales viven en el servidor, nunca en esta pantalla"
      actions={
        <Button size="sm" variant="ghost" onClick={() => setAdding(!adding)}>
          {adding ? "Cancelar" : "Nueva conexión"}
        </Button>
      }
    >
      {adding && (
        <NewConnectionForm
          countries={active}
          onCreated={() => {
            setAdding(false);
            reload();
          }}
          onError={onError}
        />
      )}

      {loading && <SkeletonRows rows={3} />}

      {!loading && (connections ?? []).length === 0 && !adding && (
        <EmptyState
          title="Sin conexiones"
          instruction="Crea una conexión de carga manual para poder subir tus reportes."
        />
      )}

      {(connections ?? []).map((connection) => (
        <div
          key={connection.connection_id}
          className="flex flex-wrap items-center justify-between gap-3 border-t border-line-row py-3"
        >
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
              {countryFlag(connection.country_code)} {connection.connection_name}
              {connection.tier === 3 && <Chip tone="warning">Tier 3</Chip>}
            </p>
            <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-ink-dim">
              <StatusDot tone={tone(connection.health)} />
              {connection.platform_name} ·{" "}
              {connection.last_sync_at
                ? `sincronizó ${formatRelative(connection.last_sync_at)}`
                : "nunca sincronizó"}
            </p>
            {connection.last_error && (
              <p className="mt-1 text-[11px] text-negative">{connection.last_error}</p>
            )}
            {connection.consent_granted_at && (
              <p className="mt-1 text-[11px] text-ink-faint">
                Consentimiento otorgado {formatRelative(connection.consent_granted_at)}
              </p>
            )}
          </div>

          <Button size="sm" variant="danger" onClick={() => remove(connection)}>
            Eliminar
          </Button>
        </div>
      ))}
    </Card>
  );
}

function NewConnectionForm({
  countries,
  onCreated,
  onError,
}: {
  countries: Country[];
  onCreated: () => void;
  onError: (message: string) => void;
}) {
  const [countryCode, setCountryCode] = useState(countries[0]?.code ?? "");
  const [platformCode, setPlatformCode] = useState("");
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [pending, setPending] = useState(false);

  const { data: platforms } = useApi<Platform[]>(
    countryCode ? `/config/platforms?country=${countryCode}` : null,
    [countryCode],
  );

  const selected = (platforms ?? []).find((p) => p.platform_code === platformCode);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    try {
      await api.post("/config/connections", {
        country_code: countryCode,
        platform_code: platformCode,
        name: name || selected?.platform_name || "Conexión",
        consent_granted: consent,
      });
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo crear la conexión");
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-4 space-y-3 rounded-[10px] border border-line bg-sunken p-4"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1 block text-[11.5px] text-ink-muted">País</span>
          <select
            value={countryCode}
            onChange={(event) => {
              setCountryCode(event.target.value);
              setPlatformCode("");
            }}
            className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px]"
          >
            {countries.map((country) => (
              <option key={country.code} value={country.code}>
                {country.name}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-[11.5px] text-ink-muted">Plataforma</span>
          <select
            value={platformCode}
            onChange={(event) => setPlatformCode(event.target.value)}
            className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px]"
            required
          >
            <option value="">Elige…</option>
            {(platforms ?? []).map((platform) => (
              <option key={platform.platform_code} value={platform.platform_code}>
                {platform.platform_name} (Tier {platform.tier})
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-[11.5px] text-ink-muted">Nombre</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={selected?.platform_name ?? "Mi conexión"}
            className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px]"
          />
        </label>
      </div>

      {selected?.requires_consent && (
        <label className="flex items-start gap-2.5 rounded-[8px] border border-warning/30 bg-warning/[0.07] p-3">
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
            className="mt-0.5"
          />
          <span className="text-[11.5px] leading-relaxed text-warning">
            Entiendo que esta es una conexión <b>Tier 3</b>: Data Effi entrará con mi sesión de
            usuario a {selected.platform_name}. Puede ir contra los términos de esa
            plataforma y la responsabilidad es mía. Leí <code>docs/tier3-politica.md</code>.
          </span>
        </label>
      )}

      <div className="flex justify-end gap-2">
        <Button type="submit" size="sm" disabled={pending || !platformCode}>
          {pending ? "Creando…" : "Crear conexión"}
        </Button>
      </div>
    </form>
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
            <p className="truncate text-[13px] text-ink">{user.full_name ?? user.email}</p>
            <p className="truncate text-[11px] text-ink-dim">{user.email}</p>
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
    <Card title="Sobre las conexiones Tier 3">
      <div className="space-y-2 text-[12px] leading-relaxed text-ink-2">
        <p>
          Una conexión Tier 3 entra con <b>tu sesión</b> a una plataforma que no publica
          API. Data Effi se identifica, espera entre peticiones y se detiene apenas la
          plataforma rechaza la sesión: nunca disfraza el tráfico.
        </p>
        <p>
          Aun así, muchas plataformas prohíben el acceso automatizado incluso a tus propios
          datos. Podrían suspenderte la cuenta. La decisión es tuya, y siempre existe la
          alternativa: exportar el reporte a mano y subirlo.
        </p>
        <p className="text-ink-dim">
          El detalle completo está en <code className="text-ink-muted">docs/tier3-politica.md</code>.
        </p>
      </div>
    </Card>
  );
}
