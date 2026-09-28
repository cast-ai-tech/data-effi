"use client";

/**
 * "Conectar con la extensión": the way Effi actually connects (migration 070).
 *
 * Effi's login has an invisible reCAPTCHA, so the server cannot log in with a
 * username and password. The merchant logs in in their own browser and the
 * extension `extension/effi-connector` sends the session with a one-time code
 * generated here. This panel:
 *
 *   1. asks for consent and generates the code (owner only, 10 minutes);
 *   2. shows it with a countdown;
 *   3. polls the code's status until the extension redeems it, and says how it
 *      went in the same words the extension showed.
 *
 * It never sees the session cookie: no endpoint returns it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type {
  Connection,
  EffiPairing,
  EffiPairingState,
  EffiPairingStatus,
} from "@/lib/types";

const DEFAULT_POLL_MS = 3000;

const FINAL_STATES: ReadonlySet<EffiPairingState> = new Set([
  "connected",
  "insufficient_permissions",
  "session_rejected",
  "unverified",
  "expired",
  "revoked",
]);

const STATE_TEXT: Record<Exclude<EffiPairingState, "pending">, string> = {
  connected: "Effi quedó conectado. Master Data ya puede descargar tus reportes.",
  insufficient_permissions:
    "La sesión llegó, pero al usuario de Effi le falta un permiso. Revisa la lista de permisos.",
  session_rejected:
    "Effi no aceptó la sesión enviada. Entra de nuevo a Effi en tu navegador y genera otro código.",
  unverified:
    "La sesión quedó guardada, pero no se pudo comprobar ahora. Pulsa «Probar conexión» en un momento.",
  expired: "El código caducó sin usarse. Genera uno nuevo.",
  revoked: "Ese código se reemplazó por uno más nuevo.",
};

/**
 * The code on screen survives leaving the page.
 *
 * Pairing means going to Effi, logging in, opening the extension: plenty of
 * reasons to click away or reload here in the meantime. The code used to
 * vanish with the component, and generating another one revokes the first -
 * so a code already typed into the extension then failed with "revocado".
 * Kept per tab (sessionStorage) and only until it expires.
 */
export function pairingStorageKey(connectionId: string): string {
  return `masterdata.effi.pairing.${connectionId}`;
}

interface StoredPairing {
  pairing: EffiPairing;
  deadline: number;
}

export function readStoredPairing(connectionId: string, now: number): StoredPairing | null {
  try {
    const raw = window.sessionStorage.getItem(pairingStorageKey(connectionId));
    if (!raw) return null;
    const value = JSON.parse(raw) as StoredPairing | null;
    if (
      !value ||
      typeof value.deadline !== "number" ||
      typeof value.pairing?.code !== "string" ||
      value.pairing.connection_id !== connectionId ||
      value.deadline <= now
    ) {
      window.sessionStorage.removeItem(pairingStorageKey(connectionId));
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function storePairing(connectionId: string, value: StoredPairing | null): void {
  try {
    if (value) {
      window.sessionStorage.setItem(pairingStorageKey(connectionId), JSON.stringify(value));
    } else {
      window.sessionStorage.removeItem(pairingStorageKey(connectionId));
    }
  } catch {
    // Without storage the code simply does not survive a reload.
  }
}

export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function EffiExtensionPairingPanel({
  connection,
  onConnected,
  pollMs = DEFAULT_POLL_MS,
}: {
  connection: Connection;
  onConnected?: () => void;
  pollMs?: number;
}) {
  const [consent, setConsent] = useState(false);
  const [creating, setCreating] = useState(false);
  const [pairing, setPairing] = useState<EffiPairing | null>(null);
  const [status, setStatus] = useState<EffiPairingStatus | null>(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // A code generated before a reload or a trip to another screen is still
  // the one to use: put it back, countdown and all.
  useEffect(() => {
    const stored = readStoredPairing(connection.connection_id, Date.now());
    if (!stored) return;
    setPairing(stored.pairing);
    setDeadline(stored.deadline);
    setNow(Date.now());
  }, [connection.connection_id]);

  // Kept in a ref so the poller calls the latest callback without restarting.
  const onConnectedRef = useRef(onConnected);
  useEffect(() => {
    onConnectedRef.current = onConnected;
  }, [onConnected]);

  const state: EffiPairingState | null = status?.state ?? (pairing ? "pending" : null);
  const remaining = deadline === null ? 0 : deadline - now;
  const waiting = state === "pending" && remaining > 0;

  const generate = useCallback(async () => {
    setCreating(true);
    setError(null);
    setStatus(null);
    try {
      const created = await api.post<EffiPairing>(
        `/config/effi/connections/${connection.connection_id}/pairing`,
        { consent_granted: consent },
      );
      setPairing(created);
      setCopied(false);
      // The server's TTL, not its clock: a laptop five minutes off would
      // otherwise show a code as expired the moment it appears.
      const until = Date.now() + created.ttl_seconds * 1000;
      setDeadline(until);
      setNow(Date.now());
      storePairing(connection.connection_id, { pairing: created, deadline: until });
    } catch (err) {
      setPairing(null);
      storePairing(connection.connection_id, null);
      setError(
        err instanceof ApiError ? err.message : "No se pudo generar el código",
      );
    } finally {
      setCreating(false);
    }
  }, [connection.connection_id, consent]);

  // Countdown.
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [waiting]);

  // Poll until the extension redeems the code (or it dies).
  useEffect(() => {
    if (!pairing || !waiting) return;
    // One request at a time (a timeout chain, not an interval): with an
    // interval, a slow "pending" answer could land AFTER the "connected" one
    // and put the code back on screen.
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await api.get<EffiPairingStatus>(
          `/config/effi/connections/${pairing.connection_id}/pairing/${pairing.pairing_id}`,
        );
        if (cancelled) return;
        if (next.state !== "pending") {
          cancelled = true;
          storePairing(pairing.connection_id, null);
          setStatus(next);
          if (next.state === "connected") onConnectedRef.current?.();
          return;
        }
      } catch {
        // A missed poll is not news; the next one will tell.
      }
      if (!cancelled) timer = window.setTimeout(() => void tick(), pollMs);
    };
    timer = window.setTimeout(() => void tick(), pollMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pairing, waiting, pollMs]);

  const expiredLocally = state === "pending" && remaining <= 0;
  const finalState: EffiPairingState | null = expiredLocally
    ? "expired"
    : state && FINAL_STATES.has(state)
      ? state
      : null;

  return (
    <section className="flex flex-col gap-4 border-t border-line-subtle pt-5">
      <div>
        <h4 className="text-md font-semibold text-ink">
          Conectar con la extensión
        </h4>
        <p className="mt-1 text-sm leading-relaxed text-ink-muted">
          Effi pide un captcha al entrar, así que Master Data no puede iniciar
          sesión por ti. Entras tú en tu navegador y la extensión de Effi nos
          envía esa sesión. Tu contraseña nunca sale de Effi.
        </p>
      </div>

      {error && (
        <p role="alert" className="text-sm leading-relaxed text-negative-ink">
          {error}
        </p>
      )}

      {pairing && waiting && (
        <div className="flex flex-col gap-3 rounded-card border border-line-subtle bg-surface-2 px-3 py-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-dim">
            Tu código
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <p
              className="font-mono text-2xl font-semibold tracking-widest text-ink"
              aria-label={`Código ${pairing.code}`}
            >
              {pairing.code}
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(pairing.code)
                  .then(() => setCopied(true))
                  .catch(() => setCopied(false));
              }}
            >
              {copied ? "Copiado" : "Copiar código"}
            </Button>
          </div>
          <p className="text-sm text-ink-2" aria-live="polite">
            Caduca en{" "}
            <span data-testid="pairing-countdown">
              {formatCountdown(remaining)}
            </span>{" "}
            · esperando a la extensión…
          </p>
          <ol className="list-decimal pl-5 text-sm leading-relaxed text-ink-muted">
            <li>Entra a Effi en este navegador y abre tu panel.</li>
            <li>Abre la extensión de Effi.</li>
            <li>Pega el código y pulsa «Enviar sesión».</li>
          </ol>
        </div>
      )}

      {finalState && (
        <p
          role="status"
          className={cx(
            "rounded-card border px-3 py-2.5 text-sm leading-relaxed",
            finalState === "connected"
              ? "border-line-subtle bg-surface-2 text-ink-2"
              : "border-warning/40 bg-warning/10 text-warning-ink",
          )}
        >
          {status?.summary && finalState !== "expired" && finalState !== "revoked"
            ? status.summary
            : STATE_TEXT[finalState as Exclude<EffiPairingState, "pending">]}
        </p>
      )}

      {!waiting && (
        <>
          <label className="flex items-start gap-2.5 text-sm leading-relaxed text-ink-2">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
              className="mt-0.5 size-4 shrink-0 rounded border-line-input"
            />
            <span>
              Autorizo a Master Data a usar mi sesión de {connection.platform_name}{" "}
              para descargar mis propios reportes. Entiendo que el acceso
              automatizado puede ir contra los términos de la plataforma y que
              la responsabilidad es mía.
            </span>
          </label>
          <div>
            <Button
              onClick={() => void generate()}
              disabled={creating || !consent}
            >
              {creating
                ? "Generando…"
                : pairing
                  ? "Generar otro código"
                  : "Generar código"}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
