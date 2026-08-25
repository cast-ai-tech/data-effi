"use client";

/**
 * Planes: the free month, three fixed plans, and a custom deal with an advisor.
 *
 * Billing is manual (migration 048): choosing a plan records the choice and
 * an advisor activates it after payment. The screen says so in plain words -
 * a person who just clicked "Elegir" must not wait for a card form that will
 * never appear. This is also the one screen that still opens when the free
 * month ended: the API answers 402 everywhere else and AppShell sends people
 * here, so nothing on this page may depend on a data endpoint.
 */

import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Card, Chip, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api } from "@/lib/api";
import { planCompanies, planPrice } from "@/lib/billing";
import { formatDate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { BillingResponse, Plan, SubscriptionState } from "@/lib/types";

export default function PlanesPage() {
  const { data, error, loading, reload } = useApi<BillingResponse>("/billing");
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function choose(code: string) {
    setBusy(code);
    setFailure(null);
    try {
      await api.post<BillingResponse>("/billing/choose", { plan_code: code });
      reload();
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "No se pudo guardar tu elección");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="Planes"
        subtitle="Un mes gratis para 1 empresa. Después, el plan que te sirva; un asesor lo activa cuando pagas."
      />

      {loading && <SkeletonRows rows={6} />}
      {!loading && error && <ErrorState message={error.message} onRetry={reload} />}

      {!loading && data && (
        <div className="space-y-4">
          <StatusCard state={data.subscription} />

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {data.plans.map((plan) => (
              <PlanCard
                key={plan.code}
                plan={plan}
                state={data.subscription}
                canChoose={data.can_choose}
                busy={busy === plan.code}
                whatsappUrl={data.advisor_whatsapp_url}
                onChoose={() => choose(plan.code)}
              />
            ))}
          </div>

          {failure && (
            <p role="alert" className="text-sm text-negative-ink">
              {failure}
            </p>
          )}

          {!data.can_choose && (
            <p className="text-sm text-ink-dim">
              Solo el administrador de la cuenta puede elegir el plan.
            </p>
          )}
        </div>
      )}

      {!loading && !error && !data && (
        <Card>
          <EmptyState title="Sin información de planes" instruction="Vuelve a cargar la página." />
        </Card>
      )}
    </AppShell>
  );
}

function StatusCard({ state }: { state: SubscriptionState }) {
  const tone = state.blocked
    ? "negative"
    : state.status === "active"
      ? "positive"
      : state.status === "pending"
        ? "accent"
        : "neutral";
  const title = state.blocked
    ? "Tu acceso está en pausa"
    : state.status === "active"
      ? `Plan ${state.plan_name}`
      : state.status === "pending"
        ? `Elegiste ${state.requested_plan_name}`
        : "Prueba gratis";

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold">{title}</h2>
            <Chip tone={tone}>{state.blocked ? "en pausa" : state.status === "active" ? "activo" : state.status === "pending" ? "pendiente" : "gratis"}</Chip>
          </div>
          <p className="mt-1 text-base text-ink-2">{state.message}</p>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-ink-dim">Empresas</dt>
          <dd className="font-semibold">
            {state.tenants_used} de {state.max_tenants ?? "sin límite"}
          </dd>
          <dt className="text-ink-dim">
            {state.status === "active" ? "Vence" : "Mes gratis hasta"}
          </dt>
          <dd className="font-semibold">
            {state.status === "active"
              ? state.current_period_end
                ? formatDate(state.current_period_end.slice(0, 10))
                : "sin vencimiento"
              : formatDate(state.trial_ends_at.slice(0, 10))}
          </dd>
        </dl>
      </div>
    </Card>
  );
}

function PlanCard({
  plan,
  state,
  canChoose,
  busy,
  whatsappUrl,
  onChoose,
}: {
  plan: Plan;
  state: SubscriptionState;
  canChoose: boolean;
  busy: boolean;
  whatsappUrl: string | null;
  onChoose: () => void;
}) {
  const isCurrent = state.status === "active" && state.plan_code === plan.code;
  const isRequested = state.status !== "active" && state.requested_plan_code === plan.code;
  const highlight = plan.code === "master_pro";

  return (
    <Card
      className={cx(
        "flex flex-col",
        highlight && "border-accent/60",
        (isCurrent || isRequested) && "border-positive/60",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-lg font-bold">{plan.name}</h3>
        {isCurrent && <Chip tone="positive">tu plan</Chip>}
        {isRequested && <Chip tone="accent">elegido</Chip>}
        {!isCurrent && !isRequested && highlight && <Chip tone="accent">más elegido</Chip>}
      </div>
      <p className="mt-2 text-2xl font-extrabold leading-tight">{planPrice(plan)}</p>
      <ul className="mt-3 flex-1 space-y-1 text-sm text-ink-2">
        <li>✓ {planCompanies(plan)}</li>
        <li>✓ Usuarios ilimitados por empresa</li>
        <li>✓ Todos los países y plataformas</li>
        <li>✓ Copiloto e informe diario</li>
        {plan.is_custom && <li>✓ Acompañamiento de un asesor</li>}
      </ul>

      {plan.is_custom ? (
        whatsappUrl ? (
          <a
            href={whatsappUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 rounded-control bg-positive px-3.5 py-2 text-center text-base font-semibold text-on-solid no-underline"
          >
            Hablar con un asesor
          </a>
        ) : (
          <p className="mt-4 text-sm text-ink-dim">
            Escríbenos y armamos el plan contigo.
          </p>
        )
      ) : (
        <button
          type="button"
          disabled={!canChoose || busy || isCurrent || isRequested}
          onClick={onChoose}
          className={cx(
            "mt-4 rounded-control px-3.5 py-2 text-base font-semibold disabled:opacity-50",
            highlight ? "bg-accent text-on-accent" : "border border-line-strong bg-surface text-ink",
          )}
        >
          {isCurrent ? "Plan actual" : isRequested ? "Esperando activación" : busy ? "Guardando…" : "Elegir este plan"}
        </button>
      )}
    </Card>
  );
}
