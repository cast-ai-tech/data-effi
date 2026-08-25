"use client";

/**
 * Crea tu empresa: a name and ONE country, chosen by clicking a flag.
 *
 * This is the screen a person lands on right after registering, and the one
 * they come back to for every further company their plan allows. One
 * company = one country: the operator's own words were "una empresa en
 * Ecuador, otra en Colombia, otra en Guate". The org chart underneath can
 * still hold more countries per company, but nobody has to know that here.
 *
 * On success the session is switched to the new company and the person is
 * taken straight to its dashboard - no assistant, no "activate a country"
 * step: the country was the second thing they clicked.
 */

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Card, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { PLANS_PATH } from "@/lib/billing";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { SupportedCountry, TenantRow, Tokens, User } from "@/lib/types";

export default function NuevaEmpresaPage() {
  const router = useRouter();
  const { data: user, reload: reloadUser } = useApi<User>("/auth/me");
  const { data: countries, error, loading, reload } = useApi<SupportedCountry[]>("/org/countries");

  const [name, setName] = useState("");
  const [country, setCountry] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; plan: boolean } | null>(null);

  const subscription = user?.subscription ?? null;
  const first = (user?.workspaces?.length ?? 0) === 0;
  const limitReached =
    subscription?.max_tenants != null && subscription.tenants_used >= subscription.max_tenants;

  const sorted = useMemo(
    () => [...(countries ?? [])].sort((a, b) => a.name.localeCompare(b.name, "es")),
    [countries],
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!country) {
      setFailure({ message: "Elige el país de la empresa.", plan: false });
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      const tenant = await api.post<TenantRow>("/org/tenants", {
        name: name.trim(),
        countries: [country],
      });
      // Stand in the new company: the proxy rotates the session cookies.
      await api.post<Tokens>("/auth/switch", { tenant_id: tenant.tenant_id });
      await reloadUser();
      router.push(`/${country.toLowerCase()}`);
      router.refresh();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setFailure({
        message:
          err instanceof ApiError ? err.message : "No se pudo crear la empresa. Intenta de nuevo.",
        plan: status === 402,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-[640px]">
        <header className="mb-5">
          <h1 className="text-2xl font-bold tracking-tight">
            {first ? "Crea tu empresa" : "Nueva empresa"}
          </h1>
          <p className="mt-1 text-sm text-ink-dim">
            {first
              ? "Un nombre y el país donde opera. Después subes tus reportes de Effi o Dropi."
              : "Cada empresa opera en un país y tiene sus propios usuarios y reportes."}
          </p>
          {subscription && subscription.max_tenants != null && (
            <p className="mt-1 text-sm text-ink-dim">
              Tu plan permite {subscription.max_tenants}{" "}
              {subscription.max_tenants === 1 ? "empresa" : "empresas"}; tienes{" "}
              {subscription.tenants_used}.
            </p>
          )}
        </header>

        {limitReached ? (
          <Card>
            <p className="text-base font-semibold">Ya usaste las empresas de tu plan.</p>
            <p className="mt-1 text-sm text-ink-dim">
              Para crear otra, elige un plan más grande.
            </p>
            <a
              href={PLANS_PATH}
              className="mt-3 inline-block rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
            >
              Ver planes
            </a>
          </Card>
        ) : (
          <Card>
            <form onSubmit={submit} className="flex flex-col gap-4">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.06em] text-ink-faint">
                  Nombre de la empresa
                </span>
                <input
                  type="text"
                  required
                  minLength={2}
                  maxLength={120}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Distrilatam Ecuador"
                  className="w-full rounded-control border border-line-input bg-surface px-3 py-2.5 text-base text-ink placeholder:text-ink-dim focus:border-accent focus:outline-none"
                />
              </label>

              <div role="group" aria-label="País de la empresa">
                <span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.06em] text-ink-faint">
                  País donde opera
                </span>
                {loading && <SkeletonRows rows={2} />}
                {error && <ErrorState message={error.message} onRetry={reload} />}
                {!loading && !error && (
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {sorted.map((item) => {
                      const on = country === item.code;
                      return (
                        <button
                          key={item.code}
                          type="button"
                          aria-pressed={on}
                          onClick={() => setCountry(item.code)}
                          className={cx(
                            "flex items-center gap-2 rounded-control border px-3 py-2.5 text-left text-sm transition",
                            on
                              ? "border-accent bg-accent/15 font-semibold text-ink"
                              : "border-line-strong text-ink-2 hover:border-line-input hover:text-ink",
                          )}
                        >
                          <span aria-hidden className="text-xl leading-none">
                            {countryFlag(item.code)}
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate">{item.name}</span>
                            <span className="block text-xs text-ink-dim">{item.currency_code}</span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {failure && (
                <p role="alert" className="rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
                  {failure.message}{" "}
                  {failure.plan && (
                    <a href={PLANS_PATH} className="font-semibold underline underline-offset-2">
                      Ver planes
                    </a>
                  )}
                </p>
              )}

              <button
                type="submit"
                disabled={busy || !name.trim() || !country}
                className="rounded-control bg-accent px-3.5 py-2.5 text-base font-semibold text-on-accent disabled:opacity-50"
              >
                {busy ? "Creando…" : "Crear empresa"}
              </button>
            </form>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
