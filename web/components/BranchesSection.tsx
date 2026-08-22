"use client";

/**
 * Sucursales: los lugares físicos desde donde opera una sociedad.
 *
 * WHY IT SITS UNDER THE COUNTRIES SECTION
 * A branch cannot exist in a country the company has not activated - the API
 * refuses it and the database refuses it underneath. Putting this right below
 * the country switches makes the order obvious without a paragraph explaining
 * it: activate the country, then say what you have there.
 *
 * A BRANCH WITH STORES IS NEVER DELETED FROM HERE
 * The API only deletes an empty one, so the button says "Desactivar" for the
 * rest. Offering "Eliminar" and then explaining why it failed is a worse screen
 * than one that never offered it.
 */

import { useMemo, useState } from "react";

import { Button, Card, Chip, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Branch, Country, User } from "@/lib/types";

export function BranchesSection({ onError }: { onError: (message: string) => void }) {
  const { data: user } = useApi<User>("/auth/me");
  const { data: countries } = useApi<Country[]>("/config/countries");
  const { data: branches, loading, reload } = useApi<Branch[]>(
    "/config/branches?include_inactive=true",
  );
  const [adding, setAdding] = useState(false);

  const canEdit = (user?.capabilities ?? []).includes("config");
  const activeCountries = useMemo(
    () => (countries ?? []).filter((country) => country.is_active),
    [countries],
  );

  async function toggle(branch: Branch) {
    try {
      await api.patch(`/config/branches/${branch.id}`, { is_active: !branch.is_active });
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo guardar");
    }
  }

  async function remove(branch: Branch) {
    try {
      await api.delete(`/config/branches/${branch.id}`);
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo eliminar");
    }
  }

  return (
    <Card
      title="Sucursales"
      subtitle="Bodegas, oficinas y puntos de venta, dentro de los países que ya activaste"
    >
      {loading && <SkeletonRows rows={2} />}

      {!loading && activeCountries.length === 0 && (
        <p className="text-[12px] text-ink-dim">
          Primero activa un país arriba. Una sucursal siempre vive en un país donde
          la sociedad opera.
        </p>
      )}

      {!loading && activeCountries.length > 0 && (branches?.length ?? 0) === 0 && !adding && (
        <p className="text-[12px] text-ink-dim">
          Todavía no registraste ninguna sucursal.
        </p>
      )}

      {!loading && branches && branches.length > 0 && (
        <ul className="mb-3 flex flex-col">
          {branches.map((branch) => (
            <li
              key={branch.id}
              className="flex items-start justify-between gap-3 border-b border-line-subtle py-2.5 last:border-0"
            >
              <div className="min-w-0">
                <p
                  className={cx(
                    "truncate text-[12.5px] font-semibold",
                    branch.is_active ? "text-ink-2" : "text-ink-dim line-through",
                  )}
                >
                  {countryFlag(branch.country_code)} {branch.name}
                </p>
                <p className="mt-0.5 text-[11px] text-ink-dim">
                  {[
                    branch.city,
                    branch.address,
                    branch.manager_name,
                    branch.phone,
                    branch.cost_center ? `Centro de costo ${branch.cost_center}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "Sin datos adicionales"}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                {branch.is_warehouse && <Chip tone="neutral">Bodega</Chip>}
                {branch.store_count > 0 && (
                  <Chip tone="neutral">
                    {branch.store_count} tienda{branch.store_count === 1 ? "" : "s"}
                  </Chip>
                )}
                {canEdit && (
                  <button
                    type="button"
                    onClick={() => (branch.store_count > 0 ? toggle(branch) : remove(branch))}
                    className="text-[11.5px] text-ink-dim underline hover:text-ink-2"
                  >
                    {branch.store_count > 0
                      ? branch.is_active
                        ? "Desactivar"
                        : "Reactivar"
                      : "Eliminar"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {canEdit && activeCountries.length > 0 && !adding && (
        <Button onClick={() => setAdding(true)}>Agregar sucursal</Button>
      )}

      {canEdit && adding && (
        <BranchForm
          countries={activeCountries.map((country) => country.code)}
          onCancel={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            reload();
          }}
          onError={onError}
        />
      )}
    </Card>
  );
}

function BranchForm({
  countries,
  onCancel,
  onCreated,
  onError,
}: {
  countries: string[];
  onCancel: () => void;
  onCreated: () => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState({
    country_code: countries[0] ?? "",
    name: "",
    city: "",
    address: "",
    manager_name: "",
    phone: "",
    cost_center: "",
    is_warehouse: false,
  });
  const [saving, setSaving] = useState(false);

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  async function create() {
    setSaving(true);
    try {
      await api.post("/config/branches", {
        ...form,
        // El backend guarda NULL, no cadenas vacías: "" y "sin dato" no son lo mismo.
        city: form.city || null,
        address: form.address || null,
        manager_name: form.manager_name || null,
        phone: form.phone || null,
        cost_center: form.cost_center || null,
      });
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo crear");
      setSaving(false);
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-line-subtle p-3">
      <div className="grid gap-2.5 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className={LABEL}>País</span>
          <select
            value={form.country_code}
            onChange={(event) => set("country_code", event.target.value)}
            className={INPUT}
          >
            {countries.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>

        <TextField label="Nombre" value={form.name} onChange={(v) => set("name", v)} />
        <TextField label="Ciudad" value={form.city} onChange={(v) => set("city", v)} />
        <TextField label="Dirección" value={form.address} onChange={(v) => set("address", v)} />
        <TextField
          label="Responsable"
          value={form.manager_name}
          onChange={(v) => set("manager_name", v)}
        />
        <TextField label="Teléfono" value={form.phone} onChange={(v) => set("phone", v)} />
        <TextField
          label="Centro de costo"
          value={form.cost_center}
          onChange={(v) => set("cost_center", v)}
        />

        <label className="flex items-center gap-2 pt-5">
          <input
            type="checkbox"
            checked={form.is_warehouse}
            onChange={(event) => set("is_warehouse", event.target.checked)}
          />
          <span className="text-[12px] text-ink-2">Aquí se guarda mercancía</span>
        </label>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <Button onClick={create} disabled={!form.name.trim() || saving}>
          {saving ? "Creando…" : "Crear sucursal"}
        </Button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[11.5px] text-ink-dim underline hover:text-ink-2"
        >
          Cancelar
        </button>
      </div>
    </div>
  );
}

const LABEL = "text-[11px] font-semibold uppercase tracking-wide text-ink-dim";
const INPUT =
  "w-full rounded-lg border border-line-subtle bg-transparent px-2.5 py-1.5 text-[12.5px] text-ink outline-none focus:border-line";

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className={LABEL}>{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={INPUT}
      />
    </label>
  );
}
