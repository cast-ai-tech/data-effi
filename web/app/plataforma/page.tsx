"use client";

/**
 * Plataforma: la pantalla de quien opera Master Data, no de quien la usa.
 *
 * POR QUÉ ES UNA PÁGINA APARTE Y NO UNA SECCIÓN EN CONEXIONES
 * -----------------------------------------------------------
 * Porque el contenido no pertenece a ninguna empresa. Todo lo demás en esta app
 * se lee dentro de un espacio de trabajo - las guías de esta empresa, las
 * conexiones de esta empresa - y esto es lo contrario: las organizaciones que
 * existen, y capturas que describen cómo funciona Effi para todos.
 *
 * Meterlo dentro de Conexiones hacía que un dato de plataforma pareciera un dato
 * del cliente que tuviera la pantalla abierta, que es exactamente la confusión
 * que causó el error original (la captura le llegaba al comerciante en vez de a
 * quien opera).
 *
 * LO QUE ESTA PANTALLA NO PUEDE MOSTRAR
 * -------------------------------------
 * Ninguna cifra de negocio de nadie. No es una decisión del componente: la API
 * no la entrega y la vista de la base no la selecciona (migración 053). Si algún
 * día alguien quiere añadir "ventas por organización" aquí, tendrá que cambiar
 * las tres capas a propósito - que es la idea.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { CaptureInbox } from "@/components/CaptureInbox";
import {
  Chip,
  EmptyState,
  ErrorState,
  SectionTitle,
  SkeletonRows,
  StatusDot,
  cx,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { PlatformOrgRow, User } from "@/lib/types";
import { useApi } from "@/lib/hooks";

const ESTADO_LABELS: Record<string, string> = {
  trial: "Mes gratis",
  pending: "Esperando activación",
  active: "Activa",
  expired: "Vencida",
};

const ESTADO_TONES: Record<
  string,
  "positive" | "warning" | "negative" | "neutral"
> = {
  trial: "neutral",
  pending: "warning",
  active: "positive",
  expired: "negative",
};

export default function PlataformaPage() {
  const me = useApi<User>("/account/me");
  const [orgs, setOrgs] = useState<PlatformOrgRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOrgs(await api.get<PlatformOrgRow[]>("/captures/orgs"));
    } catch (err) {
      setError(err instanceof ApiError ? err : null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 403 aquí no es un fallo: es la respuesta correcta para casi todo el mundo.
  // Se explica en vez de mostrar una pantalla rota.
  if (error?.status === 403) {
    return (
      <AppShell>
        <header className="mb-5">
          <h1 className="text-2xl font-bold tracking-tight">Plataforma</h1>
        </header>
        <EmptyState
          title="Esta pantalla no es para tu cuenta"
          instruction={
            "Es para quien opera Master Data. Si crees que deberías tener acceso, " +
            "pídele a quien administra el servidor que ejecute " +
            "«python -m scripts.grant_platform_admin tu@correo.com»."
          }
        />
      </AppShell>
    );
  }

  const conError = orgs.filter((o) => o.connection_errors > 0);

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Plataforma</h1>
      </header>
      <p className="mb-6 max-w-2xl text-sm leading-relaxed text-ink-muted">
        Lo que hace falta para operar Master Data: quién existe, en qué plan está,
        y qué se rompió. <strong>No verás datos de ningún comerciante</strong> —
        ni guías, ni plata, ni compradores. El tablero de cada empresa es de su
        dueño.
      </p>

      {/* Las capturas primero: es a donde lleva la notificación. */}
      <div className="mb-8">
        <CaptureInbox isOwner={me.data?.role === "owner"} />
      </div>

      <SectionTitle hint="quién usa la plataforma">Organizaciones</SectionTitle>

      {loading && <SkeletonRows rows={3} />}

      {!loading && error && (
        <ErrorState
          message="No se pudo leer la lista de organizaciones."
          onRetry={() => void load()}
        />
      )}

      {!loading && !error && orgs.length === 0 && (
        <EmptyState
          title="Todavía no hay organizaciones"
          instruction="Aparecerán aquí en cuanto alguien se registre."
        />
      )}

      {!loading && !error && orgs.length > 0 && (
        <>
          {conError.length > 0 && (
            <p className="mb-3 rounded-card border border-warning/40 bg-warning/10 px-3 py-2.5 text-sm leading-relaxed text-warning-ink">
              {conError.length === 1
                ? `A ${conError[0].org_name} se le rompió una conexión.`
                : `Hay ${conError.length} organizaciones con conexiones rotas.`}{" "}
              Es lo primero que hay que mirar: una conexión caída significa que
              su tablero dejó de actualizarse sin avisarles.
            </p>
          )}

          <ul className="flex flex-col gap-2">
            {orgs.map((org) => (
              <OrgRow key={org.org_id} org={org} />
            ))}
          </ul>
        </>
      )}
    </AppShell>
  );
}

function OrgRow({ org }: { org: PlatformOrgRow }) {
  const roto = org.connection_errors > 0;
  const estado = org.subscription_status;

  return (
    <li
      className={cx(
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-card border px-3.5 py-3",
        roto ? "border-warning/40 bg-warning/[0.05]" : "border-line bg-surface",
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{org.org_name}</p>
        <p className="mt-0.5 text-xs text-ink-dim">
          {org.tenant_count} {org.tenant_count === 1 ? "empresa" : "empresas"} ·{" "}
          {org.user_count} {org.user_count === 1 ? "persona" : "personas"} ·
          desde {formatRelative(org.created_at)}
        </p>
      </div>

      <Chip tone={ESTADO_TONES[estado] ?? "neutral"}>
        {org.plan_name ?? ESTADO_LABELS[estado] ?? estado}
      </Chip>

      <span className="flex items-center gap-1.5 text-xs text-ink-dim">
        <StatusDot
          tone={
            roto ? "warning" : org.connection_count > 0 ? "positive" : "neutral"
          }
        />
        {org.connection_count === 0
          ? "sin conexiones"
          : roto
            ? `${org.connection_errors} de ${org.connection_count} rotas`
            : `${org.connection_count} conectadas`}
      </span>
    </li>
  );
}
