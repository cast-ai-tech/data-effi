"use client";

/**
 * Mis empresas: every company this person may enter, one line each, with its
 * flag, and the button to create the next one the plan allows.
 *
 * Clicking a company switches the session to it and opens its dashboard.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Card, Chip, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { PLANS_PATH } from "@/lib/billing";
import { companyTypeLabel } from "@/lib/company";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Role, Tokens, User, Workspace } from "@/lib/types";

const ROLE_LABEL: Record<Role, string> = {
  owner: "Propietario",
  analyst: "Analista",
  viewer: "Solo lectura",
  uploader: "Solo carga",
};

export default function EmpresasPage() {
  const router = useRouter();
  const { data: user, loading, reload } = useApi<User>("/auth/me");
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const workspaces = user?.workspaces ?? [];
  const subscription = user?.subscription ?? null;
  const canCreate = Boolean(user?.is_org_admin);
  const limitReached =
    subscription?.max_tenants != null && subscription.tenants_used >= subscription.max_tenants;

  async function open(workspace: Workspace) {
    setBusy(workspace.tenant_id);
    setFailure(null);
    try {
      if (workspace.tenant_id !== user?.tenant_id) {
        await api.post<Tokens>("/auth/switch", { tenant_id: workspace.tenant_id });
        await reload();
      }
      const country = workspace.country_scope?.[0] ?? workspace.countries[0];
      router.push(country ? `/${country.toLowerCase()}` : "/global");
      router.refresh();
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "No se pudo abrir la empresa");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Mis empresas</h1>
          <p className="mt-1 text-sm text-ink-dim">
            Cada empresa opera en un país y tiene sus propios usuarios y reportes.
            {subscription?.max_tenants != null &&
              ` Tu plan permite ${subscription.max_tenants} ${subscription.max_tenants === 1 ? "empresa" : "empresas"}.`}
          </p>
        </div>
        {canCreate && (
          <Link
            href={limitReached ? PLANS_PATH : "/empresas/nueva"}
            className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
          >
            {limitReached ? "Ver planes para crear otra" : "Nueva empresa"}
          </Link>
        )}
      </header>

      {loading && <SkeletonRows rows={3} />}

      {!loading && workspaces.length === 0 && (
        <Card>
          <EmptyState
            title="Todavía no tienes ninguna empresa"
            instruction="Crea la primera: un nombre y el país donde opera."
            action={
              canCreate ? (
                <Link
                  href="/empresas/nueva"
                  className="rounded-control bg-accent px-3.5 py-2 text-sm font-semibold text-on-accent no-underline"
                >
                  Crear mi empresa
                </Link>
              ) : undefined
            }
          />
        </Card>
      )}

      {!loading && workspaces.length > 0 && (
        <Card bodyClassName="p-0">
          <ul className="divide-y divide-line-row">
            {workspaces.map((workspace) => {
              const countries = workspace.country_scope ?? workspace.countries;
              const current = workspace.tenant_id === user?.tenant_id;
              return (
                <li key={workspace.tenant_id}>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => open(workspace)}
                    className={cx(
                      "flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-sunken disabled:opacity-60",
                    )}
                  >
                    <span aria-hidden className="text-2xl leading-none">
                      {countries.map((code) => countryFlag(code)).join(" ") || "🌐"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base font-semibold text-ink">
                        {workspace.name}
                      </span>
                      <span className="block text-xs text-ink-dim">
                        {countries.join(", ") || "Sin país"} · {companyTypeLabel(workspace.company_type)} ·{" "}
                        {ROLE_LABEL[workspace.role] ?? workspace.role}
                      </span>
                    </span>
                    {current && <Chip tone="accent">estás aquí</Chip>}
                    <span className="text-sm text-ink-dim">
                      {busy === workspace.tenant_id ? "Abriendo…" : "Abrir →"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      {failure && (
        <p role="alert" className="mt-3 text-sm text-negative-ink">
          {failure}
        </p>
      )}
    </AppShell>
  );
}
