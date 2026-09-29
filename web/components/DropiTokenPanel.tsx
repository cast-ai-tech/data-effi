"use client";

/**
 * "Gestionar" for a Dropi connection: paste the integration token, test it,
 * disconnect it. Backend: api/routers/dropi.py.
 *
 * The token goes up and never comes back down - there is no field in any
 * response that carries it - so this screen only ever shows whether one is
 * stored and whether Dropi accepted it. Saving never auto-tests: "Probar" is a
 * separate press, one request, one order.
 */

import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button, Field, Input, StatusDot } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { Connection, DropiConnectionStatus, DropiTestResult } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  none: "Token sin probar",
  ok: "Dropi aceptó el token",
  invalid: "Dropi rechazó el token",
};

function tone(status: DropiConnectionStatus | null): "positive" | "negative" | "neutral" {
  if (!status?.has_token) return "neutral";
  if (status.credential_status === "ok") return "positive";
  if (status.credential_status === "invalid") return "negative";
  return "neutral";
}

export function DropiTokenPanel({
  connection,
  isOwner,
  onChanged,
}: {
  connection: Connection;
  isOwner: boolean;
  onChanged?: () => void;
}) {
  const base = `/config/dropi/connections/${connection.connection_id}`;
  const [status, setStatus] = useState<DropiConnectionStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState<"save" | "test" | "disconnect" | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await api.get<DropiConnectionStatus>(base));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo leer la conexión");
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<string | null>, which: typeof running = null) {
    setBusy(true);
    setRunning(which);
    setError(null);
    setNotice(null);
    try {
      setNotice(await action());
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo completar la acción");
    } finally {
      setBusy(false);
      setRunning(null);
    }
  }

  const save = () =>
    run(async () => {
      const saved = await api.put<DropiConnectionStatus>(`${base}/token`, { token: token.trim() });
      // Drop the token from React state as soon as it is stored.
      setToken("");
      return saved.message ?? "Token guardado.";
    }, "save");

  const test = () =>
    run(async () => (await api.post<DropiTestResult>(`${base}/test`)).message, "test");

  // Asked first: the token cannot be read back, so undoing this means going
  // to Dropi for a new one and pasting it again.
  const disconnect = () =>
    run(async () => {
      await api.delete(`${base}/token`);
      return "API desconectada. Las guías ya cargadas se conservan y puedes seguir subiendo el reporte.";
    }, "disconnect").finally(() => setConfirmingDisconnect(false));

  const hasToken = status?.has_token ?? false;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusDot tone={tone(status)} />
        <span className="text-sm font-semibold text-ink">
          {hasToken
            ? STATUS_LABELS[status?.credential_status ?? "none"] ?? status?.credential_status
            : "Recibe datos por archivo"}
        </span>
        {status?.last_sync_at && (
          <span className="text-xs text-ink-dim">
            · última sincronización {formatRelative(status.last_sync_at)}
          </span>
        )}
      </div>

      {status?.last_login_error && (
        <p className="text-sm leading-relaxed text-negative-ink">{status.last_login_error}</p>
      )}
      {status?.last_warning && (
        <p className="text-sm leading-relaxed text-warning-ink">{status.last_warning}</p>
      )}
      {error && (
        <p role="alert" className="text-sm leading-relaxed text-negative-ink">
          {error}
        </p>
      )}
      {/* "Probar conexión" answers with the same sentence it just stored as
          last_login_error; saying it twice, once red and once grey, reads as
          two different problems. */}
      {notice && notice !== status?.last_login_error && (
        <p className="text-sm leading-relaxed text-ink-2">{notice}</p>
      )}

      <p className="text-sm leading-relaxed text-ink-muted">
        Con el token de integración, Master Data lee tus órdenes de Dropi varias veces al
        día. Solo lectura: nunca crea órdenes ni genera guías. El token se cifra y no se
        puede volver a ver desde aquí.
      </p>

      {!isOwner ? (
        <p className="text-sm text-warning-ink">
          Solo el dueño del espacio puede conectar o cambiar el token.
        </p>
      ) : (
        <section className="flex flex-col gap-4 border-t border-line-subtle pt-5">
          <Field
            label={hasToken ? "Cambiar el token" : "Token de integración"}
            required
            hint="Dropi → Integraciones. Debe ser de la cuenta de este país."
          >
            <Input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="new-password"
            />
          </Field>

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void save()} disabled={busy || token.trim().length < 8}>
              {running === "save" ? "Guardando…" : hasToken ? "Actualizar token" : "Conectar API"}
            </Button>
            <Button variant="ghost" onClick={() => void test()} disabled={busy || !hasToken}>
              {running === "test" ? "Probando…" : "Probar conexión"}
            </Button>
            {hasToken && (
              <Button
                variant="danger"
                onClick={() => setConfirmingDisconnect(true)}
                disabled={busy}
              >
                Desconectar API
              </Button>
            )}
          </div>

          {confirmingDisconnect && (
            <ConfirmDialog
              title="Desconectar la API de Dropi"
              consequence="El token no se puede volver a ver: para reconectar tendrás que sacar uno nuevo en Dropi → Integraciones y pegarlo aquí."
              details={[
                { label: "Conexión", value: connection.connection_name },
                { label: "País", value: connection.country_code },
              ]}
              confirmLabel="Sí, desconectar"
              pending={running === "disconnect"}
              onConfirm={() => void disconnect()}
              onCancel={() => setConfirmingDisconnect(false)}
            >
              Master Data deja de leer tus órdenes de Dropi. Las guías ya cargadas se
              conservan y puedes seguir subiendo el reporte a mano.
            </ConfirmDialog>
          )}

          {hasToken && (
            <p className="text-xs leading-relaxed text-ink-faint">
              Desconectar aquí detiene a Master Data. Para revocar el token de raíz,
              elimínalo también en Dropi.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
