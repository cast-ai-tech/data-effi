"use client";

/**
 * `/ingest` used to be the one upload screen for the whole workspace. Since
 * migration 042 every file belongs to a country and names its platform, so the
 * upload lives under each country (`/[país]/cargar`) and this address only
 * forwards there - kept so an old link, a bookmark or the onboarding button
 * still lands somewhere useful.
 *
 * One country: straight there. Several: a short list, because guessing which
 * one the operator meant is how a Colombian file ends up under Ecuador.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";

import { AppShell } from "@/components/AppShell";
import { Card, EmptyState, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Country, User } from "@/lib/types";

export default function IngestForwardPage() {
  const router = useRouter();
  const { data: user } = useApi<User>("/auth/me");
  const { data: countries, loading } = useApi<Country[]>("/config/countries");

  const allowed = useMemo(() => {
    const active = (countries ?? []).filter((country) => country.is_active);
    const scope = user?.countries;
    return scope ? active.filter((country) => scope.includes(country.code)) : active;
  }, [countries, user]);

  useEffect(() => {
    if (allowed.length === 1) {
      router.replace(`/${allowed[0].code.toLowerCase()}/cargar`);
    }
  }, [allowed, router]);

  return (
    <AppShell>
      <PageHeader
        title="Cargar datos"
        subtitle="Cada país tiene su propia carga: ahí eliges de qué plataforma es el archivo."
      />

      <Card>
        {loading && <SkeletonRows rows={3} />}

        {!loading && allowed.length === 0 && (
          <EmptyState
            title="No tienes ningún país activo"
            instruction="Activa tu país en Configuración y vuelve aquí."
            action={
              <Link
                href="/settings"
                className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
              >
                Ir a Configuración
              </Link>
            }
          />
        )}

        {!loading && allowed.length === 1 && (
          <p className="text-base text-ink-2">
            Abriendo la carga de {allowed[0].name}…
          </p>
        )}

        {!loading && allowed.length > 1 && (
          <div>
            <p className="mb-3 text-base text-ink-2">¿De qué país es el archivo?</p>
            <ul className="grid gap-2 sm:grid-cols-2">
              {allowed.map((country) => (
                <li key={country.code}>
                  <Link
                    href={`/${country.code.toLowerCase()}/cargar`}
                    className="flex items-center gap-2.5 rounded-control border border-line-strong bg-surface px-3.5 py-2.5 text-base font-medium text-ink no-underline hover:border-accent/60"
                  >
                    <span className="text-lg leading-none">{countryFlag(country.code)}</span>
                    {country.name}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </AppShell>
  );
}
