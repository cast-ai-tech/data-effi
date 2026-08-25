"use client";

/**
 * Who may enter THIS company, as what, on which side of the business, and
 * limited to which countries.
 *
 * The screen is written around the operator's own sentence: "a cada socio y a
 * cada empleado le creamos usuario con permisos específicos - este solo ve
 * Guatemala y Honduras, este solo carga y no ve resultados, este es de
 * proveeduría". So the invite form asks four things in that order (quién, qué
 * puede hacer, de qué lado está, qué países) and the list answers them back
 * for everyone already inside - including WHICH COMPANY each access belongs
 * to, because a person may hold access in several and a row read without its
 * company is a row that can be misread.
 *
 * COUNTRIES ARE FLAGS YOU CLICK. One click adds a country, a second click
 * removes it. Selecting none - or all - means "every country of the company",
 * which the API expresses as clearing the scope (see lib/members.ts).
 *
 * TWO OUTCOMES OF AN INVITATION, AND WHY THE UI SAYS WHICH
 * If the email is new, the API returns a one-time link the person uses to set
 * their own password - shown once, never recoverable. If the email already
 * belongs to someone (a partner already inside another of your companies),
 * access is granted instantly and there is no link at all. Handing over a link
 * that does nothing, and waiting for one that will never arrive, are both
 * support calls, so the result says plainly which happened.
 */

import { useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  BusinessModelPicker,
  CountryFlagPicker,
  type CountryOption,
} from "@/components/MemberPickers";
import { Card, Chip, EmptyState, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import {
  type BusinessModel,
  businessModelLabel,
  scopePayload,
  visibleCountries,
} from "@/lib/members";
import type { Country, InviteResult, Member, Role, User } from "@/lib/types";

const ROLES: { value: Role; label: string; detail: string }[] = [
  { value: "owner", label: "Propietario", detail: "Ve todo y administra usuarios" },
  { value: "analyst", label: "Analista", detail: "Ve todo, carga datos y ajusta configuración" },
  { value: "viewer", label: "Solo lectura", detail: "Ve los resultados, no toca nada" },
  { value: "uploader", label: "Solo carga", detail: "Sube archivos y NO ve los resultados" },
];

export default function UsuariosPage() {
  const { data: user } = useApi<User>("/auth/me");
  const { data: members, loading, reload } = useApi<Member[]>("/config/users");
  const { data: countries } = useApi<Country[]>("/config/countries");

  const activeCountries = useMemo(
    () =>
      (countries ?? [])
        .filter((country) => country.is_active)
        .map((c) => ({ code: c.code, name: c.name })),
    [countries],
  );

  const tenantId = user?.tenant_id ?? null;
  const canAdminister = (user?.capabilities ?? []).includes("manage");

  return (
    <AppShell>
      <header className="mb-5">
        <h1 className="text-[22px] font-bold tracking-tight">Usuarios</h1>
        <p className="mt-1 text-[12px] text-ink-dim">
          Quién entra a <strong>{user?.tenant_name ?? "esta sociedad"}</strong>, qué
          puede hacer y qué países ve. Cada sociedad tiene su propia lista.
        </p>
      </header>

      {!canAdminister && (
        <Card>
          <EmptyState
            title="Solo el propietario administra usuarios"
            instruction="Pídele acceso a quien creó la sociedad, o cambia a una sociedad donde seas propietario."
          />
        </Card>
      )}

      {canAdminister && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Card title="Personas con acceso" subtitle={`${members?.length ?? 0} en total`}>
            {loading && <SkeletonRows rows={3} />}
            {!loading && (members?.length ?? 0) === 0 && (
              <p className="text-[12px] text-ink-dim">Todavía no has invitado a nadie.</p>
            )}
            {!loading && members && members.length > 0 && (
              <ul className="flex flex-col">
                {members.map((member) => (
                  <MemberItem
                    key={member.user_id}
                    member={member}
                    tenantId={tenantId}
                    tenantName={user?.tenant_name ?? null}
                    countries={activeCountries}
                    isMe={member.user_id === user?.id}
                    onChanged={reload}
                  />
                ))}
              </ul>
            )}
          </Card>

          <InviteForm countries={activeCountries} onInvited={reload} />
        </div>
      )}
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Invite
// ---------------------------------------------------------------------------

function InviteForm({
  countries,
  onInvited,
}: {
  countries: readonly CountryOption[];
  onInvited: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [businessModel, setBusinessModel] = useState<BusinessModel | null>(null);
  const [scope, setScope] = useState<string[]>([]);
  const [share, setShare] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InviteResult | null>(null);

  // An uploader never reads a number, so limiting them to a country would
  // restrict nothing. Asking anyway is a question with no consequence.
  const scopeApplies = role !== "uploader";
  const codes = countries.map((c) => c.code);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body: Record<string, unknown> = { email: email.trim(), role };
      if (businessModel) body.business_model = businessModel;
      if (scopeApplies && scope.length > 0 && !codes.every((c) => scope.includes(c))) {
        body.country_scope = scope;
      }
      if (scopeApplies && share.trim()) body.share_pct = Number(share);
      setResult(await api.post<InviteResult>("/auth/invite", body));
      setEmail("");
      setScope([]);
      setShare("");
      setBusinessModel(null);
      onInvited();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo crear el acceso");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Dar acceso a alguien" subtitle="Se agrega solo a esta sociedad">
      <form onSubmit={submit} className="flex flex-col gap-3.5">
        <Field label="Correo">
          <input
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="socio@empresa.com"
            className="w-full rounded-[8px] border border-line-strong bg-page px-2.5 py-2 text-[12.5px] outline-none focus:border-accent"
          />
        </Field>

        <Field label="Qué puede hacer">
          <div className="flex flex-col gap-1.5">
            {ROLES.map((option) => (
              <label
                key={option.value}
                className={cx(
                  "flex cursor-pointer items-start gap-2 rounded-[8px] border px-2.5 py-2",
                  role === option.value
                    ? "border-accent/50 bg-white/[0.04]"
                    : "border-line-strong",
                )}
              >
                <input
                  type="radio"
                  name="role"
                  value={option.value}
                  checked={role === option.value}
                  onChange={() => setRole(option.value)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-semibold">{option.label}</span>
                  <span className="block text-[11px] text-ink-dim">{option.detail}</span>
                </span>
              </label>
            ))}
          </div>
        </Field>

        <Field
          label="Modelo de negocio"
          hint="De qué lado está esta persona. Opcional; no cambia lo que ve."
        >
          <BusinessModelPicker value={businessModel} onChange={setBusinessModel} disabled={busy} />
        </Field>

        {scopeApplies && countries.length > 1 && (
          <Field label="Países que puede ver">
            <CountryFlagPicker
              countries={countries}
              selected={scope}
              onChange={setScope}
              disabled={busy}
            />
          </Field>
        )}

        {scopeApplies && (
          <Field label="Participación %" hint="Opcional. Solo para mostrar 'su parte'">
            <input
              type="number"
              min="0.01"
              max="100"
              step="0.01"
              value={share}
              onChange={(event) => setShare(event.target.value)}
              placeholder="50"
              className="w-full rounded-[8px] border border-line-strong bg-page px-2.5 py-2 text-[12.5px] outline-none focus:border-accent"
            />
          </Field>
        )}

        <button
          type="submit"
          disabled={busy || !email.trim()}
          className="rounded-[8px] bg-accent px-3.5 py-2 text-[12.5px] font-semibold text-on-accent disabled:opacity-50"
        >
          {busy ? "Creando…" : "Dar acceso"}
        </button>

        {error && <p className="text-[12px] text-negative">{error}</p>}
        {result && <InviteOutcome result={result} />}
      </form>
    </Card>
  );
}

function InviteOutcome({ result }: { result: InviteResult }) {
  const link =
    result.invitation_token && typeof window !== "undefined"
      ? `${window.location.origin}/login?invite=${result.invitation_token}`
      : null;

  if (result.already_registered) {
    return (
      <div className="rounded-[8px] border border-line-strong bg-page px-3 py-2.5 text-[12px]">
        <p className="font-semibold">{result.email} ya puede entrar</p>
        <p className="mt-0.5 text-ink-dim">
          Ya tenía cuenta en otra de tus sociedades: entra con su misma contraseña y
          elige esta sociedad en el selector de arriba a la izquierda.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-[8px] border border-accent/40 bg-page px-3 py-2.5 text-[12px]">
      <p className="font-semibold">Enlace de invitación para {result.email}</p>
      <p className="mt-0.5 text-ink-dim">
        Cópialo y mándaselo. <strong>Se muestra una sola vez</strong> y vence en 7
        días; ahí elige su propia contraseña.
      </p>
      <code className="mt-2 block break-all rounded-[6px] bg-surface px-2 py-1.5 text-[11px]">
        {link ?? result.invitation_token}
      </code>
      {link && (
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(link)}
          className="mt-2 rounded-[7px] border border-line-strong px-2.5 py-1 text-[11.5px]"
        >
          Copiar enlace
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One member
// ---------------------------------------------------------------------------

function MemberItem({
  member,
  tenantId,
  tenantName,
  countries,
  isMe,
  onChanged,
}: {
  member: Member;
  tenantId: string | null;
  tenantName: string | null;
  countries: readonly CountryOption[];
  isMe: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const roleLabel =
    ROLES.find((option) => option.value === member.role)?.label ?? member.role;
  const codes = countries.map((c) => c.code);
  const shown = visibleCountries(member.country_scope, codes);
  const companyName = member.tenant_name ?? tenantName ?? "esta sociedad";

  async function patch(body: Record<string, unknown>) {
    if (!tenantId) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/org/tenants/${tenantId}/members/${member.user_id}`, body);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar");
    } finally {
      setBusy(false);
    }
  }

  // `window.confirm` is deliberately not used here: see ConfirmDialog.tsx.
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);

  async function revoke() {
    if (!tenantId) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/org/tenants/${tenantId}/members/${member.user_id}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo quitar el acceso");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="border-b border-line-subtle/60 py-3 last:border-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[12.5px] font-semibold">
            {member.full_name ?? member.email}
            {isMe && <span className="ml-1.5 text-[10.5px] text-ink-dim">(tú)</span>}
          </p>
          <p className="truncate text-[11px] text-ink-dim">{member.email}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Chip tone="accent">{companyName}</Chip>
            <Chip tone={member.role === "uploader" ? "warning" : "neutral"}>
              {roleLabel}
            </Chip>
            <Chip tone={member.business_model ? "positive" : "neutral"}>
              {businessModelLabel(member.business_model)}
            </Chip>
            {member.share_pct != null && <Chip tone="neutral">{member.share_pct}%</Chip>}
            <span className="text-[10.5px] text-ink-faint">
              {member.last_login_at
                ? `entró ${formatRelative(member.last_login_at)}`
                : "nunca ha entrado"}
            </span>
          </div>
          {member.role !== "uploader" && (
            <p
              className="mt-1.5 flex flex-wrap items-center gap-1 text-[12px]"
              aria-label={
                member.country_scope
                  ? `Solo ve ${member.country_scope.join(", ")}`
                  : "Ve todos los países"
              }
            >
              <span className="text-[10.5px] uppercase tracking-[0.06em] text-ink-faint">
                {member.country_scope ? "Solo" : "Todos los países"}
              </span>
              {shown.map((code) => (
                <span
                  key={code}
                  title={countries.find((c) => c.code === code)?.name ?? code}
                  className="inline-flex items-center gap-0.5 rounded-full bg-sunken px-1.5 py-0.5 text-[11px]"
                >
                  <span aria-hidden className="text-[14px] leading-none">
                    {countryFlag(code)}
                  </span>
                  {code}
                </span>
              ))}
            </p>
          )}
        </div>

        <div className="flex shrink-0 gap-1.5">
          <button
            type="button"
            onClick={() => setEditing(!editing)}
            className="rounded-[7px] border border-line-strong px-2.5 py-1 text-[11.5px]"
          >
            {editing ? "Cerrar" : "Cambiar"}
          </button>
          {!isMe && (
            <button
              type="button"
              onClick={() => setConfirmingRevoke(true)}
              disabled={busy}
              className="rounded-[7px] border border-line-strong px-2.5 py-1 text-[11.5px] text-negative disabled:opacity-50"
            >
              Quitar
            </button>
          )}
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-col gap-3 rounded-[8px] border border-line-strong bg-page p-3">
          <EditRow label="Rol">
            <div className="flex flex-wrap gap-1.5">
              {ROLES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  disabled={busy || option.value === member.role}
                  onClick={() => patch({ role: option.value })}
                  className={cx(
                    "rounded-full border px-2.5 py-1 text-[11.5px] disabled:opacity-40",
                    option.value === member.role
                      ? "border-accent/60 bg-accent/15"
                      : "border-line-strong",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </EditRow>

          <EditRow label="Modelo de negocio">
            <BusinessModelPicker
              value={member.business_model}
              disabled={busy}
              onChange={(next) => {
                // The API keeps an omitted field as it was, so "sin modelo" is
                // only reachable while nothing was ever set; a set model can be
                // switched, never blanked, which is the honest reading of a
                // partner who IS on one side of the business.
                if (next) patch({ business_model: next });
              }}
            />
          </EditRow>

          {countries.length > 1 && member.role !== "uploader" && (
            <EditRow label="Países">
              <CountryFlagPicker
                countries={countries}
                selected={member.country_scope ?? []}
                disabled={busy}
                onChange={(next) => patch(scopePayload(next, codes))}
              />
            </EditRow>
          )}

          {member.role !== "uploader" && (
            <SharePctEditor
              member={member}
              busy={busy}
              onSave={(value) => patch({ share_pct: value })}
            />
          )}
        </div>
      )}

      {error && <p className="mt-2 text-[11.5px] text-negative">{error}</p>}

      {confirmingRevoke && (
        <ConfirmDialog
          title="Quitar el acceso a esta sociedad"
          confirmLabel="Quitar acceso"
          pending={busy}
          details={[
            { label: "Persona", value: member.full_name ?? member.email },
            { label: "Correo", value: member.email },
            { label: "Empresa", value: companyName },
            { label: "Rol", value: roleLabel },
          ]}
          consequence="Deja de ver esta sociedad de inmediato. Sus otras sociedades y su cuenta no se tocan; se le puede volver a invitar."
          onCancel={() => setConfirmingRevoke(false)}
          onConfirm={async () => {
            await revoke();
            setConfirmingRevoke(false);
          }}
        >
          {member.email} perderá el acceso a {companyName}.
        </ConfirmDialog>
      )}
    </li>
  );
}

function EditRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:gap-3">
      <span className="w-[120px] shrink-0 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-faint">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function SharePctEditor({
  member,
  busy,
  onSave,
}: {
  member: Member;
  busy: boolean;
  onSave: (value: number) => void;
}) {
  const [value, setValue] = useState(member.share_pct?.toString() ?? "");

  return (
    <EditRow label="Participación %">
      <div className="flex items-center gap-2">
        <input
          type="number"
          aria-label="Participación en porcentaje"
          min="0.01"
          max="100"
          step="0.01"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="w-24 rounded-[7px] border border-line-strong bg-surface px-2 py-1 text-[11.5px] outline-none focus:border-accent"
        />
        <button
          type="button"
          disabled={busy || !value.trim()}
          onClick={() => onSave(Number(value))}
          className="rounded-[7px] border border-line-strong px-2.5 py-1 text-[11.5px] disabled:opacity-40"
        >
          Guardar
        </button>
      </div>
    </EditRow>
  );
}

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
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-faint">
        {label}
      </span>
      {hint && <span className="-mt-1 text-[11px] text-ink-dim">{hint}</span>}
      {children}
    </div>
  );
}
