"use client";

/**
 * Las capturas que llegaron, y el botón para pedir una nueva.
 *
 * EL HUECO QUE ESTO CIERRA
 * ------------------------
 * El buzón ya recibía capturas y ya avisaba en la campana, pero la notificación
 * llevaba a esta pantalla y aquí no había nada. Se sabía que había llegado algo
 * y no había forma de verlo salvo llamando la API a mano.
 *
 * QUÉ TIENE QUE RESOLVER ESTA PANTALLA
 * ------------------------------------
 * Dos cosas, y en este orden:
 *
 *   1. INVITAR. Generar el código y decir, literalmente, qué comando ejecutar
 *      con él. Sin esto el código sale por API y hay que armar el paquete a
 *      ciegas.
 *   2. LEER LO QUE LLEGÓ. Y sobre todo: distinguir de un vistazo una captura
 *      que sirve de una que no. Una sin login es un mensaje que hay que
 *      mandarle a esa persona hoy, no un dato que archivar.
 *
 * LO QUE NO HACE, A PROPÓSITO
 * ---------------------------
 * No aplica nada. Muestra las líneas del .env para que un humano las copie,
 * revise y decida. Que esta pantalla pudiera reconfigurar el conector con un
 * clic convertiría un POST de fuera en un cambio de producción a un clic de
 * distancia.
 */

import { useCallback, useEffect, useState } from "react";

import { Button, Chip, Field, Input, StatusDot, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { CaptureRow, CaptureToken } from "@/lib/types";

export function CaptureInbox({ isOwner }: { isOwner: boolean }) {
  const [rows, setRows] = useState<CaptureRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [label, setLabel] = useState("");
  const [inviting, setInviting] = useState(false);
  const [token, setToken] = useState<CaptureToken | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await api.get<CaptureRow[]>("/captures"));
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo leer el buzón",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function invite() {
    setInviting(true);
    setError(null);
    try {
      const created = await api.post<CaptureToken>(
        `/captures/tokens?label=${encodeURIComponent(label.trim())}`,
      );
      setToken(created);
      setLabel("");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo crear el código",
      );
    } finally {
      setInviting(false);
    }
  }

  async function markReviewed(id: number) {
    // Optimista: marcar es reversible y trivial, y esperar al servidor para
    // mover una etiqueta hace que la lista se sienta pegajosa.
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, is_new: false } : r)),
    );
    try {
      await api.post(`/captures/${id}/reviewed`);
    } catch {
      void load();
    }
  }

  const sinRevisar = rows.filter((r) => r.is_new).length;

  return (
    <section className="rounded-card border border-line bg-surface p-4">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-md font-semibold text-ink">Capturas de conexión</h3>
        {sinRevisar > 0 && <Chip tone="accent">{sinRevisar} sin revisar</Chip>}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto w-auto"
          onClick={() => void load()}
        >
          Actualizar
        </Button>
      </header>

      <p className="mb-4 text-sm leading-relaxed text-ink-muted">
        Cuando alguien con una cuenta de Effi usa la extensión, lo que capturó
        llega aquí. Son rutas y nombres de campos: nunca una contraseña.
      </p>

      {error && (
        <p role="alert" className="mb-3 text-sm text-negative-ink">
          {error}
        </p>
      )}

      {/* -- invitar ---------------------------------------------------- */}
      {isOwner && (
        <div className="mb-5 rounded-card border border-line-subtle bg-surface-2 p-3.5">
          <p className="mb-2 text-sm font-semibold text-ink">
            Invitar a alguien a capturar
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <Field
              label="¿De quién es?"
              hint="Para reconocer la captura cuando llegue."
              className="min-w-[220px] flex-1"
            >
              <Input
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="Juan, de Distrilatam"
              />
            </Field>
            <Button
              className="w-auto"
              onClick={() => void invite()}
              disabled={inviting || !label.trim()}
            >
              {inviting ? "Creando…" : "Crear código"}
            </Button>
          </div>

          {token && <TokenReveal token={token} />}
        </div>
      )}

      {/* -- lo que llegó ----------------------------------------------- */}
      {loading ? (
        <p className="text-sm text-ink-dim">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm leading-relaxed text-ink-muted">
          Todavía no ha llegado ninguna. Crea un código, arma el paquete con él
          y envíaselo a quien tenga una cuenta de Effi.
        </p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {rows.map((row) => (
            <CaptureCard
              key={row.id}
              row={row}
              onReviewed={() => void markReviewed(row.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function TokenReveal({ token }: { token: CaptureToken }) {
  const comando = `python -m scripts.empaquetar_captura --url "${token.submit_url}"`;
  return (
    <div className="mt-3 rounded-card border border-accent/30 bg-accent/[0.06] p-3">
      <p className="text-sm font-semibold text-ink">
        Código creado para «{token.label}»
      </p>
      <p className="mt-1 text-sm leading-relaxed text-ink-2">
        Se muestra una sola vez. Ejecuta esto para armar el paquete que le vas a
        enviar:
      </p>
      <CopyBlock text={comando} />
      <p className="mt-2 text-xs text-ink-faint">
        Caduca {formatRelative(token.expires_at)} · {token.max_uses} usos. Puede
        capturar varias veces con el mismo paquete.
      </p>
    </div>
  );
}

function CaptureCard({
  row,
  onReviewed,
}: {
  row: CaptureRow;
  onReviewed: () => void;
}) {
  const [abierta, setAbierta] = useState(row.is_new && row.found_login);
  const c = row.contract || {};

  // Las líneas del .env, armadas aquí para que se copien de una vez. Es
  // literalmente el producto de todo este flujo.
  const env = [
    `EFFI_BASE_URL=${c.base ?? ""}`,
    `EFFI_LOGIN_PATH=${c.ruta ?? ""}`,
    `EFFI_LOGIN_USER_FIELD=${c.campoUsuario ?? ""}`,
    `EFFI_LOGIN_PASS_FIELD=${c.campoClave ?? ""}`,
    `EFFI_LOGIN_CSRF_FIELD=${c.campoCsrf ?? ""}`,
    `EFFI_SESSION_CARRIER=${c.carrier ?? ""}`,
    c.carrier === "cookie"
      ? `EFFI_SESSION_COOKIE=${c.carrierNombre ?? ""}`
      : c.carrier === "json"
        ? `EFFI_TOKEN_JSON_KEY=${c.carrierNombre ?? ""}`
        : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <li
      className={cx(
        "rounded-card border p-3",
        row.is_new
          ? "border-accent/40 bg-accent/[0.04]"
          : "border-line-subtle bg-surface-2",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusDot tone={row.found_login ? "positive" : "warning"} />
        <span className="text-sm font-medium text-ink">
          {row.invited_label || "Sin etiqueta"}
        </span>
        <span className="text-xs text-ink-dim">
          {row.platform_name} · {formatRelative(row.created_at)}
        </span>
        {row.is_new && <Chip tone="accent">Nueva</Chip>}
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto w-auto"
          onClick={() => setAbierta((v) => !v)}
        >
          {abierta ? "Ocultar" : "Ver"}
        </Button>
      </div>

      {/* El veredicto primero. Una captura sin login no es un dato que
          archivar: es un mensaje que hay que mandarle a esa persona hoy. */}
      <p
        className={cx(
          "mt-1.5 text-sm leading-relaxed",
          row.found_login ? "text-ink-muted" : "text-warning-ink",
        )}
      >
        {row.found_login
          ? `Sirve. Trae la entrada${
              row.export_count > 0
                ? ` y ${row.export_count} ${row.export_count === 1 ? "descarga" : "descargas"} de reportes.`
                : ", sin descargas de reportes."
            }`
          : "No sirve: le falta el momento de entrar. Pídele que cierre sesión en Effi y grabe otra vez desde el principio."}
      </p>

      {abierta && (
        <div className="mt-3 flex flex-col gap-3">
          {row.found_login && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-dim">
                Para el .env del servidor
              </p>
              <CopyBlock text={env} />
            </div>
          )}

          {(c.exportaciones?.length ?? 0) > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-dim">
                Descargas encontradas
              </p>
              <ul className="flex flex-col gap-1">
                {c.exportaciones!.map((e, i) => (
                  <li
                    key={i}
                    className="font-mono text-xs leading-relaxed text-ink-2"
                  >
                    {e.metodo || "GET"} {e.ruta}
                    {e.params ? (
                      <span className="text-ink-faint"> · {e.params}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-xs leading-relaxed text-ink-faint">
                Compáralas con <code>PERMISSION_PROBES</code> en{" "}
                <code>connectors/effi/permissions.py</code> antes de dar el
                login por verificado.
              </p>
            </div>
          )}

          {row.is_new && (
            <Button
              size="sm"
              variant="ghost"
              className="w-auto"
              onClick={onReviewed}
            >
              Marcar como revisada
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function CopyBlock({ text }: { text: string }) {
  const [copiado, setCopiado] = useState(false);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(text);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 2000);
    } catch {
      // El portapapeles puede estar bloqueado. El texto sigue seleccionable a
      // mano, así que no es un callejón sin salida.
      setCopiado(false);
    }
  }

  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-control border border-line bg-surface p-2.5 font-mono text-xs leading-relaxed text-ink-2">
        {text}
      </pre>
      <Button
        size="sm"
        variant="ghost"
        className="absolute right-1.5 top-1.5 w-auto"
        onClick={() => void copiar()}
      >
        {copiado ? "Copiado" : "Copiar"}
      </Button>
    </div>
  );
}
