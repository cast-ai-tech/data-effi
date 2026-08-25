"use client";

/**
 * Mi cuenta: lo que una persona administra sobre sí misma.
 *
 * WHY THIS SCREEN EXISTS FOR EVERY ROLE, INCLUDING `uploader`
 * Every other screen answers "what is the business doing". This one answers
 * "who am I here, and what am I allowed to see" - and the person who most needs
 * that answer is the one with the least access, because when a number is missing
 * they cannot tell a permission from a bug. So "Mis accesos" spells out the
 * companies, the role in each and the countries, in words rather than in a
 * silently empty dashboard.
 *
 * CHANGING THE PASSWORD LOGS YOU OUT, AND THE FORM SAYS SO BEFORE YOU TYPE
 * The API revokes every session on a password change - including this one, since
 * an access token carries no reference to the refresh token it came from.
 * Discovering that by being thrown to the login screen reads like a crash, so it
 * is stated up front and the redirect is deliberate.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Button, Card, Chip, EmptyState, SkeletonRows, ThemeToggle } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Session, User } from "@/lib/types";

const ROLE_LABEL: Record<string, string> = {
  owner: "Propietario",
  analyst: "Analista",
  viewer: "Solo lectura",
  uploader: "Solo carga",
};

const ORG_ROLE_LABEL: Record<string, string> = {
  admin: "Administrador de la organización",
  analyst: "Analista de la organización",
  viewer: "Lectura de la organización",
};

const ORG_ROLE_DETAIL: Record<string, string> = {
  admin: "Crea sociedades y reparte accesos en todo el grupo.",
  analyst: "Ve el consolidado de todas las sociedades del grupo.",
  viewer: "Ve el consolidado del grupo, sin tocar nada.",
};

export default function CuentaPage() {
  const { data: user, loading, reload } = useApi<User>("/auth/me");

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Mi cuenta</h1>
        <p className="mt-1 text-sm text-ink-dim">
          Tus datos, tu contraseña y a qué tienes acceso.
        </p>
      </header>

      {loading && (
        <Card>
          <SkeletonRows rows={4} />
        </Card>
      )}

      {!loading && user && (
        <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
          <div className="flex flex-col gap-4">
            <ProfileCard user={user} onSaved={reload} />
            <AccessCard user={user} />
          </div>
          <div className="flex flex-col gap-4">
            <AppearanceCard />
            <PasswordCard />
            <SessionsCard />
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Apariencia
// ---------------------------------------------------------------------------

function AppearanceCard() {
  return (
    <Card title="Apariencia" subtitle="Claro es el modo normal. Oscuro es opcional.">
      <div className="-mx-3 -my-1">
        <ThemeToggle />
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Datos personales
// ---------------------------------------------------------------------------

function ProfileCard({ user, onSaved }: { user: User; onSaved: () => void }) {
  const [fullName, setFullName] = useState(user.full_name ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const dirty = fullName.trim() !== (user.full_name ?? "").trim();

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.patch("/auth/me", { full_name: fullName.trim() });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Mis datos" subtitle="Cómo te ve el resto del equipo">
      <div className="flex flex-col gap-3">
        <Field label="Nombre">
          <input
            value={fullName}
            onChange={(event) => {
              setFullName(event.target.value);
              setSaved(false);
            }}
            placeholder="Tu nombre"
            className={INPUT}
          />
        </Field>

        <Field
          label="Correo"
          hint="Para cambiarlo, pídeselo a un administrador: otras sociedades ya le dieron acceso a este correo."
        >
          <input value={user.email} disabled className={`${INPUT} opacity-60`} />
        </Field>

        {error && <p className="text-sm text-rose-400">{error}</p>}
        {saved && !dirty && <p className="text-sm text-emerald-400">Guardado.</p>}

        <div>
          <Button onClick={save} disabled={!dirty || saving}>
            {saving ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Mis accesos
// ---------------------------------------------------------------------------

function AccessCard({ user }: { user: User }) {
  const orgRole = user.org_role ?? (user.is_org_admin ? "admin" : null);

  return (
    <Card
      title="Mis accesos"
      subtitle="Dónde puedes entrar y qué puedes hacer en cada lugar"
    >
      {orgRole && (
        <div className="mb-3 rounded-lg border border-line-subtle p-3">
          <p className="text-sm font-semibold text-ink-2">
            {user.org_name ?? "Tu organización"}
          </p>
          <p className="mt-0.5 text-xs text-ink-dim">
            {ORG_ROLE_LABEL[orgRole] ?? orgRole} — {ORG_ROLE_DETAIL[orgRole] ?? ""}
          </p>
        </div>
      )}

      {user.workspaces.length === 0 && (
        <EmptyState
          title="Todavía no perteneces a ninguna sociedad"
          instruction="Pídele a un administrador que te dé acceso a una."
        />
      )}

      {user.workspaces.length > 0 && (
        <ul className="flex flex-col">
          {user.workspaces.map((workspace) => {
            const scoped = workspace.country_scope && workspace.country_scope.length > 0;
            const visible = scoped ? workspace.country_scope! : workspace.countries;
            return (
              <li
                key={workspace.tenant_id}
                className="flex items-start justify-between gap-3 border-b border-line-subtle py-2.5 last:border-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-base font-semibold text-ink-2">
                    {workspace.name}
                    {workspace.tenant_id === user.tenant_id && (
                      <span className="ml-2 text-xs font-normal text-ink-dim">
                        (donde estás ahora)
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-dim">
                    {ROLE_LABEL[workspace.role] ?? workspace.role}
                    {scoped ? " · solo estos países" : " · todos sus países"}
                    {workspace.share_pct !== null && workspace.share_pct !== undefined
                      ? ` · ${workspace.share_pct}% de participación`
                      : ""}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  {(visible ?? []).map((code) => (
                    <Chip key={code} tone="neutral">
                      {countryFlag(code)} {code}
                    </Chip>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Contraseña
// ---------------------------------------------------------------------------

function PasswordCard() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mismatch = repeat.length > 0 && next !== repeat;
  const ready = current.length > 0 && next.length > 0 && !mismatch;

  async function change() {
    setSaving(true);
    setError(null);
    try {
      await api.post("/auth/me/password", {
        current_password: current,
        new_password: next,
      });
      // Every session died with the change, this one included, and the proxy
      // already cleared the cookies. Going straight to the login screen is
      // honest; staying here would fail on the next request.
      router.push("/login");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cambiar");
      setSaving(false);
    }
  }

  return (
    <Card
      title="Contraseña"
      subtitle="Al cambiarla se cierran todas tus sesiones y tendrás que entrar de nuevo"
    >
      <div className="flex flex-col gap-3">
        <Field label="Contraseña actual">
          <input
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            className={INPUT}
            autoComplete="current-password"
          />
        </Field>
        <Field label="Contraseña nueva" hint="Mínimo 10 caracteres.">
          <input
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            className={INPUT}
            autoComplete="new-password"
          />
        </Field>
        <Field label="Repite la nueva">
          <input
            type="password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            className={INPUT}
            autoComplete="new-password"
          />
        </Field>

        {mismatch && <p className="text-sm text-rose-400">Las dos no coinciden.</p>}
        {error && <p className="text-sm text-rose-400">{error}</p>}

        <div>
          <Button onClick={change} disabled={!ready || saving}>
            {saving ? "Cambiando…" : "Cambiar contraseña"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sesiones
// ---------------------------------------------------------------------------

function SessionsCard() {
  const { data: sessions, loading, reload } = useApi<Session[]>("/auth/me/sessions");
  const [error, setError] = useState<string | null>(null);

  async function close(id: string) {
    setError(null);
    try {
      await api.delete(`/auth/me/sessions/${id}`);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cerrar");
    }
  }

  return (
    <Card
      title="Sesiones abiertas"
      subtitle="Cada navegador o dispositivo donde iniciaste sesión"
    >
      {loading && <SkeletonRows rows={2} />}

      {!loading && (sessions?.length ?? 0) === 0 && (
        <p className="text-sm text-ink-dim">No hay sesiones abiertas.</p>
      )}

      {error && <p className="mb-2 text-sm text-rose-400">{error}</p>}

      {!loading && sessions && sessions.length > 0 && (
        <ul className="flex flex-col">
          {sessions.map((session) => (
            <li
              key={session.id}
              className="flex items-center justify-between gap-3 border-b border-line-subtle py-2.5 last:border-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-ink-2">
                  {session.tenant_name ?? "Sin sociedad"}
                </p>
                <p className="text-xs text-ink-dim">
                  Iniciada {formatRelative(session.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => close(session.id)}
                className="shrink-0 text-sm text-ink-dim underline hover:text-ink-2"
              >
                Cerrar
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------

const INPUT =
  "w-full rounded-lg border border-line-subtle bg-transparent px-2.5 py-1.5 text-base text-ink outline-none focus:border-line";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-semibold uppercase tracking-wide text-ink-dim">
        {label}
      </span>
      {children}
      {hint && <span className="text-xs text-ink-dim">{hint}</span>}
    </label>
  );
}
