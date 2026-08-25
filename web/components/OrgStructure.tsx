"use client";

/**
 * The org chart: organización → empresas → países.
 *
 * THREE LEVELS, ON PURPOSE
 * The holding at the top, one card per company under it, and inside each card
 * the countries that company operates in. There is no fourth level: a branch
 * or warehouse model existed once (migration 035) and was removed (043)
 * because the operator thinks in "which company, which country" and nothing
 * below that changes a number on the dashboard.
 *
 * WHO MAY CHANGE IT
 * Only an org admin creates a company or edits its countries: the API refuses
 * everyone else, and the buttons are simply not rendered for them. Everyone
 * with a role over the holding - or a membership in at least one company -
 * sees the chart, because "where does my partnership sit" is a fair question.
 *
 * COUNTRIES ARE SAVED AS A WHOLE LIST
 * The form shows a checklist and saves the picture as one call. A country
 * unticked is deactivated, never deleted: its shipments and history stay, and
 * ticking it again brings everything back.
 */

import { useState } from "react";

import { Button, Card, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag, pluralize } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { SupportedCountry, TenantRow, User } from "@/lib/types";

/** `null` = nothing open; `"new"` = creating; otherwise the company being edited. */
type Editing = null | "new" | string;

export function OrgStructure({ user, orgName }: { user: User | null; orgName: string | null }) {
  const { data: tenants, loading, reload } = useApi<TenantRow[]>("/org/tenants");
  const { data: catalogue } = useApi<SupportedCountry[]>("/org/countries");
  const [editing, setEditing] = useState<Editing>(null);
  const [error, setError] = useState<string | null>(null);

  const canEdit = Boolean(user && (user.org_role === "admin" || user.is_org_admin));
  const companies = tenants ?? [];

  function countryName(code: string): string {
    return catalogue?.find((country) => country.code === code)?.name ?? code;
  }

  function saved() {
    setEditing(null);
    setError(null);
    reload();
  }

  return (
    <Card
      title="Estructura"
      subtitle="Organización → empresas → países en los que opera cada una"
      actions={
        canEdit && editing !== "new" ? (
          <Button size="sm" onClick={() => setEditing("new")}>
            Nueva empresa
          </Button>
        ) : undefined
      }
    >
      {error && (
        <p className="mb-3 rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
          {error}
        </p>
      )}

      {loading && <SkeletonRows rows={3} />}

      {!loading && (
        <div className="flex flex-col items-center">
          {/* Level 1: the holding. */}
          <div
            data-testid="org-root"
            className="flex items-center gap-2.5 rounded-control border border-accent/40 bg-accent/[0.06] px-4 py-2.5"
          >
            <span aria-hidden className="text-lg">
              🏢
            </span>
            <div>
              <p className="text-base font-bold tracking-tight text-ink">
                {orgName ?? "Organización"}
              </p>
              <p className="text-xs uppercase tracking-[0.06em] text-ink-faint">
                Organización ·{" "}
                {pluralize(companies.length, "empresa", "empresas")}
              </p>
            </div>
          </div>

          {(companies.length > 0 || editing === "new") && (
            <div aria-hidden className="h-5 w-px bg-line-strong" />
          )}

          {editing === "new" && (
            <div className="mb-4 w-full max-w-md">
              <CompanyForm
                catalogue={catalogue ?? []}
                onSaved={saved}
                onCancel={() => setEditing(null)}
                onError={setError}
              />
            </div>
          )}

          {companies.length === 0 && editing !== "new" && (
            <p className="mt-3 text-sm text-ink-dim">
              {canEdit
                ? "Todavía no hay empresas. Crea una por cada operación o socio."
                : "Todavía no hay empresas en esta organización."}
            </p>
          )}

          {companies.length > 0 && (
            <div className="relative w-full">
              {/* The bar the company cards hang from. */}
              {companies.length > 1 && (
                <div
                  aria-hidden
                  className="absolute left-[16.6%] right-[16.6%] top-0 h-px bg-line-strong"
                />
              )}
              <ul className="grid gap-3 pt-4 sm:grid-cols-2 xl:grid-cols-3">
                {companies.map((company) => (
                  <li key={company.tenant_id} className="relative">
                    <div
                      aria-hidden
                      className="absolute -top-4 left-1/2 h-4 w-px -translate-x-1/2 bg-line-strong"
                    />
                    {editing === company.tenant_id ? (
                      <CompanyForm
                        initial={company}
                        catalogue={catalogue ?? []}
                        onSaved={saved}
                        onCancel={() => setEditing(null)}
                        onError={setError}
                      />
                    ) : (
                      <CompanyNode
                        company={company}
                        countryName={countryName}
                        isMine={(user?.workspaces ?? []).some(
                          (ws) => ws.tenant_id === company.tenant_id,
                        )}
                        onEdit={canEdit ? () => setEditing(company.tenant_id) : undefined}
                      />
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/** Level 2: one company, with its countries (level 3) listed inside. */
function CompanyNode({
  company,
  countryName,
  isMine,
  onEdit,
}: {
  company: TenantRow;
  countryName: (code: string) => string;
  isMine: boolean;
  onEdit?: () => void;
}) {
  return (
    <article className="h-full rounded-control border border-line bg-surface p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-base font-bold tracking-tight text-ink">{company.name}</h3>
          <p className="mt-0.5 text-xs text-ink-dim">
            {pluralize(company.member_count, "persona", "personas")}
            {isMine && " · tienes acceso"}
          </p>
        </div>
        {onEdit && (
          <button
            type="button"
            onClick={onEdit}
            className="shrink-0 text-sm text-ink-dim underline hover:text-ink-2"
          >
            Editar
          </button>
        )}
      </div>

      {company.notes && (
        <p className="mt-2 text-sm leading-relaxed text-ink-2">{company.notes}</p>
      )}

      <ul className="mt-3 ml-2 flex flex-col gap-1.5 border-l border-line-strong pl-3">
        {company.countries.length === 0 && (
          <li className="text-sm text-ink-dim">Sin países activos</li>
        )}
        {company.countries.map((code) => (
          <li key={code} className="flex items-center gap-2 text-sm text-ink-2">
            <span aria-hidden>{countryFlag(code)}</span>
            <span className="font-medium">{countryName(code)}</span>
            <span className="text-xs text-ink-faint">{code}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

/** Create or edit a company. `initial` present = edit. */
function CompanyForm({
  initial,
  catalogue,
  onSaved,
  onCancel,
  onError,
}: {
  initial?: TenantRow;
  catalogue: SupportedCountry[];
  onSaved: () => void;
  onCancel: () => void;
  onError: (message: string | null) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [countries, setCountries] = useState<string[]>(initial?.countries ?? []);
  const [busy, setBusy] = useState(false);

  function toggle(code: string) {
    setCountries((current) =>
      current.includes(code) ? current.filter((c) => c !== code) : [...current, code].sort(),
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (name.trim().length < 2) {
      onError("El nombre necesita al menos dos letras.");
      return;
    }
    setBusy(true);
    onError(null);
    const body = { name: name.trim(), notes: notes.trim() || null, countries };
    try {
      if (initial) {
        await api.patch(`/org/tenants/${initial.tenant_id}`, body);
      } else {
        await api.post("/org/tenants", body);
      }
      onSaved();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo guardar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex h-full flex-col gap-3 rounded-control border border-accent/40 bg-surface p-3.5"
      aria-label={initial ? `Editar ${initial.name}` : "Nueva empresa"}
    >
      <Field label="Nombre de la empresa">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Ej. Distrilatam Ecuador"
          className={inputClass}
          maxLength={120}
          autoFocus
        />
      </Field>

      <Field label="Países en los que opera" hint="Marca todos los que apliquen">
        {catalogue.length === 0 && (
          <p className="text-sm text-ink-dim">Cargando países…</p>
        )}
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          {catalogue.map((country) => (
            <label
              key={country.code}
              className={cx(
                "flex cursor-pointer items-center gap-2 text-sm",
                countries.includes(country.code) ? "text-ink" : "text-ink-2",
              )}
            >
              <input
                type="checkbox"
                checked={countries.includes(country.code)}
                onChange={() => toggle(country.code)}
                className="accent-accent"
              />
              <span aria-hidden>{countryFlag(country.code)}</span>
              <span className="truncate">{country.name}</span>
            </label>
          ))}
        </div>
      </Field>

      <Field label="Notas" hint="Socios, participación, lo que quieras recordar">
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          maxLength={500}
          className={cx(inputClass, "resize-none")}
        />
      </Field>

      <div className="mt-auto flex items-center justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button type="submit" size="sm" disabled={busy}>
          {busy ? "Guardando…" : initial ? "Guardar cambios" : "Crear empresa"}
        </Button>
      </div>
    </form>
  );
}

const inputClass =
  "w-full rounded-control border border-line-input bg-surface px-3 py-2 text-base text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent";

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
      <span className="text-xs font-semibold uppercase tracking-[0.06em] text-ink-faint">
        {label}
      </span>
      {hint && <span className="-mt-1 text-xs text-ink-dim">{hint}</span>}
      {children}
    </div>
  );
}

