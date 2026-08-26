"use client";

/**
 * "Gestionar conexión": connect a platform account, and see what it can read.
 *
 * THE ORDER ON THIS SCREEN IS THE POINT.
 *
 * The permission list comes FIRST, before the username and password fields, and
 * it is readable before anything is connected. That is not a layout preference:
 *
 *   permissions first   the merchant reads what we need and why, creates a
 *                       DEDICATED Effi user with exactly those permissions, and
 *                       types its password. Two minutes, no failures.
 *
 *   password first      the merchant types their OWNER account - the one that
 *                       can do everything, including cancel guides - because it
 *                       is the one they know. It works, so nobody ever fixes it.
 *
 * The second outcome is the one that happens by default, and it is the one that
 * matters most if we ever have an incident. So the checklist is the hero of the
 * screen and the form is underneath it.
 *
 * WHAT THIS COMPONENT WILL NOT DO
 * - It never displays a stored password. There is no endpoint that returns one.
 * - It never auto-tests after saving. Storing and proving are separate steps
 *   because a login attempt against a mistyped password is how accounts get
 *   locked; the merchant presses "Probar" once, when they are ready.
 * - It never softens `locked`. That state needs the merchant to go to Effi, and
 *   a reassuring message would stop them from doing the only thing that works.
 */

import { useCallback, useEffect, useState } from "react";

import { Button, Chip, Field, Input, StatusDot, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type {
  Connection,
  ConnectionPermission,
  ConnectionPreflight,
  CredentialStatus,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<CredentialStatus, string> = {
  none: "Sin conectar",
  ok: "Conectada",
  invalid: "Usuario o contraseña incorrectos",
  expired: "Hay que reingresar la cuenta",
  insufficient_permissions: "Falta un permiso",
  locked: "Cuenta bloqueada en la plataforma",
};

const STATUS_TONES: Record<
  CredentialStatus,
  "positive" | "warning" | "negative" | "neutral"
> = {
  none: "neutral",
  ok: "positive",
  invalid: "negative",
  expired: "warning",
  insufficient_permissions: "warning",
  locked: "negative",
};

const PERMISSION_TONES = {
  granted: "positive",
  denied: "negative",
  unreachable: "warning",
  unknown: "neutral",
} as const;

const PERMISSION_LABELS = {
  granted: "Concedido",
  denied: "Falta",
  unreachable: "Sin respuesta",
  unknown: "Sin comprobar",
} as const;

/** The read-only actions, in the words Effi itself uses on its roles screen. */
const ACTION_LABELS: Record<string, string> = {
  consultar: "Consultar",
  ver_reportes: "Ver reportes",
};

// ---------------------------------------------------------------------------

export function ConnectionCredentialPanel({
  connection,
  isOwner,
  onChanged,
}: {
  connection: Connection;
  isOwner: boolean;
  onChanged?: () => void;
}) {
  const [preflight, setPreflight] = useState<ConnectionPreflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<ConnectionPreflight>(
        `/config/connections/${connection.connection_id}/permissions`,
      );
      setPreflight(data);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo leer la lista de permisos",
      );
    } finally {
      setLoading(false);
    }
  }, [connection.connection_id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    setNotice(null);
    setError(null);
    try {
      await api.put(
        `/config/connections/${connection.connection_id}/credential`,
        {
          username: username.trim(),
          password,
          consent_granted: consent,
        },
      );
      // Drop the password from component state the moment it is stored. It
      // cannot be scrubbed from the browser's memory, but leaving it in a React
      // state variable keeps it alive in every future render and in any
      // devtools snapshot taken from here on.
      setPassword("");
      setNotice(
        "Cuenta guardada y cifrada. Cuando el usuario ya tenga los permisos de arriba, pulsa «Probar conexión».",
      );
      await load();
      onChanged?.();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo guardar la cuenta",
      );
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setNotice(null);
    setError(null);
    try {
      const data = await api.post<ConnectionPreflight>(
        `/config/connections/${connection.connection_id}/test`,
      );
      setPreflight(data);
      onChanged?.();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo probar la conexión",
      );
    } finally {
      setTesting(false);
    }
  }

  async function disconnect() {
    setSaving(true);
    setError(null);
    try {
      await api.delete(
        `/config/connections/${connection.connection_id}/credential`,
      );
      setUsername("");
      setPassword("");
      setConsent(false);
      setNotice(
        "Cuenta desconectada. Para cortar el acceso de raíz, cierra también la sesión desde la plataforma: es el único lugar que puede revocarla de verdad.",
      );
      await load();
      onChanged?.();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo desconectar la cuenta",
      );
    } finally {
      setSaving(false);
    }
  }

  const status = preflight?.credential_status ?? "none";
  const connected = status !== "none";
  const required = (preflight?.permissions ?? []).filter(
    (p) => p.requirement === "required",
  );
  const optional = (preflight?.permissions ?? []).filter(
    (p) => p.requirement === "optional",
  );

  return (
    <div className="flex flex-col gap-6">
      {/* -- where this connection stands ------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        <StatusDot tone={STATUS_TONES[status]} />
        <span className="text-sm font-semibold text-ink">
          {STATUS_LABELS[status]}
        </span>
        {connection.last_sync_at && (
          <span className="text-xs text-ink-dim">
            · última sincronización {formatRelative(connection.last_sync_at)}
          </span>
        )}
      </div>

      {preflight?.summary && (
        <p
          className={cx(
            "rounded-card border px-3 py-2.5 text-sm leading-relaxed",
            preflight.is_usable
              ? "border-line-subtle bg-surface-2 text-ink-2"
              : "border-warning/40 bg-warning/10 text-warning-ink",
          )}
        >
          {preflight.summary}
        </p>
      )}

      {error && (
        <p role="alert" className="text-sm leading-relaxed text-negative-ink">
          {error}
        </p>
      )}
      {notice && <p className="text-sm leading-relaxed text-ink-2">{notice}</p>}

      {/* -- the checklist, first ---------------------------------------- */}
      <section className="flex flex-col gap-3">
        <div>
          <h4 className="text-md font-semibold text-ink">
            Permisos requeridos en {connection.platform_name}
          </h4>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            Crea en {connection.platform_name} un{" "}
            <strong>usuario dedicado</strong> para Master Data con estos permisos
            y conéctalo aquí. No uses tu cuenta de dueño: si algún día hay que
            cortar el acceso, quieres poder borrar un solo usuario.
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            Todos son de <strong>solo lectura</strong>. Master Data nunca crea,
            modifica ni anula nada en {connection.platform_name}.
          </p>
        </div>

        {loading ? (
          <p className="text-sm text-ink-dim">Cargando permisos…</p>
        ) : (
          <>
            <PermissionList permissions={required} heading="Obligatorios" />
            {optional.length > 0 && (
              <PermissionList
                permissions={optional}
                heading="Opcionales"
                note="Sin estos el tablero principal se calcula igual; solo quedan secciones vacías."
              />
            )}
          </>
        )}
      </section>

      {/* -- the form, second -------------------------------------------- */}
      {!isOwner ? (
        <p className="text-sm text-warning-ink">
          Solo el dueño del espacio puede conectar o cambiar la cuenta de una
          plataforma.
        </p>
      ) : (
        <section className="flex flex-col gap-4 border-t border-line-subtle pt-5">
          <div>
            <h4 className="text-md font-semibold text-ink">
              {connected ? "Cambiar la cuenta conectada" : "Conectar la cuenta"}
            </h4>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">
              La contraseña se cifra antes de guardarse y no se puede volver a
              ver desde aquí, ni por ti ni por soporte. Si la olvidas,
              recupérala en {connection.platform_name}.
            </p>
          </div>

          <Field label="Usuario" required>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="reportes@mitienda.co"
              autoComplete="off"
            />
          </Field>

          <Field
            label="Contraseña"
            required
            hint="Se envía una sola vez y se guarda cifrada."
          >
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              // Keeps a browser or password manager from filing this away as
              // the merchant's own login for THIS site, which it is not.
              autoComplete="new-password"
            />
          </Field>

          <label className="flex items-start gap-2.5 text-sm leading-relaxed text-ink-2">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
              className="mt-0.5 size-4 shrink-0 rounded border-line-input"
            />
            <span>
              Autorizo a Master Data a entrar a {connection.platform_name} con
              esta cuenta y descargar mis propios reportes. Entiendo que el
              acceso automatizado puede ir contra los términos de la plataforma
              y que la responsabilidad es mía.
            </span>
          </label>

          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => void save()}
              disabled={saving || !username.trim() || !password || !consent}
            >
              {saving
                ? "Guardando…"
                : connected
                  ? "Actualizar cuenta"
                  : "Conectar cuenta"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => void test()}
              disabled={!connected || testing}
            >
              {testing ? "Probando…" : "Probar conexión"}
            </Button>
            {connected && (
              <Button
                variant="danger"
                onClick={() => void disconnect()}
                disabled={saving}
              >
                Desconectar
              </Button>
            )}
          </div>

          {connected && (
            <p className="text-xs leading-relaxed text-ink-faint">
              Desconectar aquí detiene a Master Data. Para cortar el acceso de
              raíz, cierra también la sesión desde {connection.platform_name} —
              es el único sitio que puede revocarla de verdad.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function PermissionList({
  permissions,
  heading,
  note,
}: {
  permissions: ConnectionPermission[];
  heading: string;
  note?: string;
}) {
  if (permissions.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-dim">
        {heading}
      </p>
      {note && <p className="text-xs text-ink-muted">{note}</p>}
      <ul className="flex flex-col gap-2.5">
        {permissions.map((permission) => (
          <li
            key={permission.permission_code}
            className="rounded-card border border-line-subtle bg-surface-2 px-3 py-2.5"
          >
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-sm font-semibold text-ink">
                {permission.permission_name}:
              </span>
              <span className="text-sm text-ink-2">
                {permission.actions
                  .map((a) => ACTION_LABELS[a] ?? a)
                  .join(", ")}
              </span>
              <Chip tone={PERMISSION_TONES[permission.status]}>
                {PERMISSION_LABELS[permission.status]}
              </Chip>
              {permission.admin_only && (
                <Chip tone="accent">Solo para administrador</Chip>
              )}
            </div>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">
              {permission.why}
            </p>
            {/* Only shown when it adds something the `why` does not - which is
                exactly when the probe found a problem. */}
            {permission.status === "denied" && permission.detail && (
              <p className="mt-1 text-sm leading-relaxed text-negative-ink">
                {permission.detail}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
