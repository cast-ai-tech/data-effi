"use client";

/**
 * The notification centre: a slide-over with three sections.
 *
 * Hoy holds the morning digest, Urgentes what landed since a load finished,
 * Anteriores everything already read, oldest reachable by paging. Opening a
 * notification marks it read and follows its deep link; it is an inbox that
 * empties itself by being used, not by being cleared.
 *
 * "Umbrales" is a sub-screen rather than a settings page because the numbers
 * it edits - typical delivery rate, typical freight - are the same numbers the
 * alerts above are measured against. Changing one where you read the other
 * keeps the cause next to the effect.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AlertCard } from "@/components/AlertCard";
import { Button, Chip, CloseButton, Drawer, Skeleton, cx } from "@/components/ui";
import { ApiError, api, qs } from "@/lib/api";
import { FALLBACK_COUNTRY, formatMoney, formatRelative } from "@/lib/format";
import { useNotifications } from "@/lib/notifications";
import type {
  Notification,
  NotificationsResponse,
  Threshold,
  ThresholdsResponse,
} from "@/lib/types";

const PAGE_SIZE = 30;

// ---------------------------------------------------------------------------
// Thresholds vocabulary. Four keys, the same four `ai/memory.py` learns.
// ---------------------------------------------------------------------------

export const THRESHOLD_FIELDS: ReadonlyArray<{
  key: string;
  label: string;
  hint: string;
  suffix: string;
}> = [
  {
    key: "efectividad_tipica_pct",
    label: "Efectividad típica",
    hint: "Por debajo de este % de entrega una transportadora o producto se marca.",
    suffix: "%",
  },
  {
    key: "flete_tipico",
    label: "Flete típico",
    hint: "Flete promedio por guía contra el que se comparan las zonas.",
    suffix: "",
  },
  {
    key: "dias_a_caja_tipicos",
    label: "Días a caja típicos",
    hint: "Cuántos días suele tardar la transportadora en liquidarte.",
    suffix: "días",
  },
  {
    key: "alistamiento_tipico_dias",
    label: "Alistamiento típico",
    hint: "Días entre la orden y el despacho que se consideran normales.",
    suffix: "días",
  },
];

/** `YYYY-MM-DD` of an ISO timestamp, in the reader's local time. */
function localDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return (
    `${date.getFullYear()}-` +
    `${String(date.getMonth() + 1).padStart(2, "0")}-` +
    `${String(date.getDate()).padStart(2, "0")}`
  );
}

// ---------------------------------------------------------------------------
// The slide-over
// ---------------------------------------------------------------------------

export function NotificationCenter({
  open,
  onClose,
  countryCode,
}: {
  open: boolean;
  onClose: () => void;
  countryCode: string | null;
}) {
  const [view, setView] = useState<"list" | "thresholds">("list");

  // Back to the inbox each time it opens: the thresholds are a detour.
  useEffect(() => {
    if (open) setView("list");
  }, [open]);

  if (!open) return null;

  return (
    <>
      <Drawer
        data-notification-center
        onClose={onClose}
        label={view === "list" ? "Notificaciones" : "Umbrales"}
        bodyClassName="flex flex-col p-0 sm:p-0"
        header={
            <header className="flex items-center justify-between gap-2 border-b border-line-subtle px-4 py-3 sm:px-5">
              <div className="flex items-center gap-2">
                {view === "thresholds" && (
                  <button
                    type="button"
                    onClick={() => setView("list")}
                    className="flex size-11 items-center justify-center rounded-control text-lg text-ink-muted hover:bg-hover-strong"
                    aria-label="Volver a notificaciones"
                  >
                    ←
                  </button>
                )}
                <h2 className="text-lg font-bold">
                  {view === "list" ? "Notificaciones" : "Umbrales"}
                </h2>
              </div>
              <div className="flex items-center gap-1">
                {view === "list" && (
                  <button
                    type="button"
                    onClick={() => setView("thresholds")}
                    className="min-h-11 rounded-control px-3 text-base font-medium text-ink-muted hover:bg-hover-strong hover:text-accent-ink"
                  >
                    Umbrales
                  </button>
                )}
                <CloseButton onClose={onClose} />
              </div>
            </header>
        }
      >

        {view === "list" ? (
          <NotificationList countryCode={countryCode} onNavigate={onClose} />
        ) : (
          <ThresholdsView countryCode={countryCode} />
        )}
      </Drawer>
    </>
  );
}

// ---------------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------------

function NotificationList({
  countryCode,
  onNavigate,
}: {
  countryCode: string | null;
  onNavigate: () => void;
}) {
  const router = useRouter();
  const { subscribe, markRead, markAllRead } = useNotifications();

  const [items, setItems] = useState<Notification[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(
    async (before: number | null) => {
      const path = `/notifications${qs({
        limit: PAGE_SIZE,
        before,
        country: countryCode,
      })}`;
      try {
        const page = await api.get<NotificationsResponse>(path);
        setItems((current) => (before === null ? page.items : [...current, ...page.items]));
        setNextBefore(page.next_before);
        setFailed(false);
      } catch {
        // The API is asleep or the endpoint is not there yet. An empty inbox
        // that says so is the whole degraded state; nothing else changes.
        setFailed(true);
      }
    },
    [countryCode],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void load(null).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  // A notification that lands while the inbox is open goes to the top of it.
  useEffect(
    () => subscribe("notification.created", () => void load(null)),
    [subscribe, load],
  );

  async function openItem(item: Notification) {
    if (item.read_at === null) {
      setItems((current) =>
        current.map((row) =>
          row.id === item.id ? { ...row, read_at: new Date().toISOString() } : row,
        ),
      );
      try {
        await markRead(item.id, item.severity);
      } catch {
        // Marking read is a courtesy; the navigation still happens.
      }
    }
    if (item.deep_link) {
      onNavigate();
      router.push(item.deep_link);
    }
  }

  async function readAll() {
    setItems((current) =>
      current.map((row) =>
        row.read_at === null ? { ...row, read_at: new Date().toISOString() } : row,
      ),
    );
    try {
      await markAllRead(countryCode);
    } catch {
      // Same as above: the list already reads as done; the server catches up.
    }
  }

  const today = localDay(new Date().toISOString());
  const digestToday = items.filter(
    (item) => item.kind === "digest" && localDay(item.created_at) === today,
  );
  const urgent = items.filter((item) => item.kind !== "digest" && item.read_at === null);
  const earlier = items.filter(
    (item) => !digestToday.includes(item) && !urgent.includes(item),
  );
  const unreadHere = items.some((item) => item.read_at === null);

  return (
    <div className="flex-1 space-y-5 overflow-y-auto p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-ink-dim">
          {countryCode ? `Solo ${countryCode}` : "Todos los países"}
        </p>
        <button
          type="button"
          onClick={() => void readAll()}
          disabled={!unreadHere}
          className="text-sm font-medium text-accent-ink disabled:text-ink-dim"
        >
          Marcar todo leído
        </button>
      </div>

      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}

      {!loading && failed && (
        <p className="rounded-control border border-line bg-surface p-3.5 text-sm text-ink-dim">
          No se pudieron leer las notificaciones ahora. Los tableros siguen
          funcionando.
        </p>
      )}

      {!loading && !failed && items.length === 0 && (
        <p className="rounded-control border border-line bg-surface p-3.5 text-sm text-ink-dim">
          Nada todavía. Cuando una carga cruce un umbral o llegue el resumen de
          la mañana, aparece aquí.
        </p>
      )}

      {digestToday.length > 0 && (
        <Section title="Hoy">
          {digestToday.map((item) => (
            <DigestItem key={item.id} item={item} onOpen={openItem} onNavigate={onNavigate} />
          ))}
        </Section>
      )}

      {urgent.length > 0 && (
        <Section title="Urgentes">
          {urgent.map((item) => (
            <NotificationItem key={item.id} item={item} onOpen={openItem} />
          ))}
        </Section>
      )}

      {earlier.length > 0 && (
        <Section title="Anteriores">
          {earlier.map((item) =>
            item.kind === "digest" ? (
              <DigestItem key={item.id} item={item} onOpen={openItem} onNavigate={onNavigate} />
            ) : (
              <NotificationItem key={item.id} item={item} onOpen={openItem} />
            ),
          )}
        </Section>
      )}

      {nextBefore !== null && !loading && (
        <Button
          size="sm"
          variant="ghost"
          className="w-full"
          disabled={loadingMore}
          onClick={() => {
            setLoadingMore(true);
            void load(nextBefore).finally(() => setLoadingMore(false));
          }}
        >
          {loadingMore ? "Cargando…" : "Ver anteriores"}
        </Button>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </h3>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

function NotificationItem({
  item,
  onOpen,
}: {
  item: Notification;
  onOpen: (item: Notification) => void;
}) {
  const unread = item.read_at === null;
  const critical = item.severity === "critical";

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className={cx(
        "block w-full rounded-control border bg-surface p-3.5 text-left transition-colors hover:border-accent/40",
        critical ? "border-negative/30" : "border-line",
        !unread && "opacity-70",
      )}
    >
      <div className="mb-1.5 flex items-center gap-2">
        {unread && <span aria-hidden className="size-[7px] rounded-full bg-accent" />}
        <Chip tone={critical ? "negative" : "neutral"}>
          {critical ? "CRÍTICA" : item.kind === "system" ? "SISTEMA" : "AVISO"}
        </Chip>
        {item.country_code && (
          <span className="text-xs font-semibold text-ink-dim">{item.country_code}</span>
        )}
        <span className="ml-auto text-xs text-ink-dim">
          {formatRelative(item.created_at)}
        </span>
      </div>
      <h4 className="text-base font-semibold text-ink">{item.title}</h4>
      <p className="mt-1 text-sm leading-[1.55] text-ink-2">{item.finding}</p>
      {item.impact_amount !== null && (
        <p className="mt-1.5 text-xs font-semibold text-negative-ink">
          {formatMoney(item.impact_amount, {
            ...FALLBACK_COUNTRY,
            currency_symbol: "",
            currency_code: item.impact_currency ?? "",
            decimal_places: 0,
          })}{" "}
          {item.impact_currency}
        </p>
      )}
      <p className="mt-2 text-sm leading-[1.5] text-ink-muted">{item.action}</p>
      {item.deep_link && (
        <span className="mt-2.5 inline-block text-sm font-semibold text-accent-ink">
          Ver detalle →
        </span>
      )}
    </button>
  );
}

/** The digest: the brief, then its recommendations as the cards the panel uses. */
function DigestItem({
  item,
  onOpen,
  onNavigate,
}: {
  item: Notification;
  onOpen: (item: Notification) => void;
  onNavigate: () => void;
}) {
  const [expanded, setExpanded] = useState(item.read_at === null);
  const brief = item.payload.brief ?? item.finding;
  const cards = [
    ...(item.payload.recommendations ?? []),
    ...(item.payload.alerts ?? []),
  ];

  return (
    <article
      className={cx(
        "rounded-control border border-line bg-surface p-3.5",
        item.read_at !== null && "opacity-80",
      )}
    >
      <div className="mb-1.5 flex items-center gap-2">
        {item.read_at === null && (
          <span aria-hidden className="size-[7px] rounded-full bg-accent" />
        )}
        <Chip tone="accent">RESUMEN</Chip>
        {item.country_code && (
          <span className="text-xs font-semibold text-ink-dim">{item.country_code}</span>
        )}
        <span className="ml-auto text-xs text-ink-dim">
          {formatRelative(item.created_at)}
        </span>
      </div>
      <h4 className="text-base font-semibold text-ink">{item.title}</h4>
      <p className="mt-1 whitespace-pre-line text-base leading-[1.65] text-ink-body">
        {brief}
      </p>

      {cards.length > 0 && (
        <button
          type="button"
          onClick={() => {
            setExpanded((current) => !current);
            if (item.read_at === null) onOpen({ ...item, deep_link: null });
          }}
          className="mt-2 text-sm font-medium text-accent-ink"
        >
          {expanded ? "Ocultar detalle" : `Ver ${cards.length} ${cards.length === 1 ? "punto" : "puntos"}`}
        </button>
      )}

      {expanded && cards.length > 0 && (
        <div className="mt-2.5 space-y-2">
          {cards.map((card, index) => (
            <AlertCard key={`${card.code}-${index}`} alert={card} onNavigate={onNavigate} />
          ))}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Thresholds
// ---------------------------------------------------------------------------

function ThresholdsView({ countryCode }: { countryCode: string | null }) {
  const [rows, setRows] = useState<Threshold[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const path = countryCode ? `/notifications/thresholds?country=${countryCode}` : null;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const response = await api.get<ThresholdsResponse>(path);
      setRows(response.thresholds);
      const next: Record<string, string> = {};
      for (const row of response.thresholds) next[row.key] = row.value;
      setDraft(next);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [path]);

  useEffect(() => {
    setRows(null);
    void load();
  }, [load]);

  async function save() {
    if (!path) return;
    setBusy(true);
    setMessage(null);
    try {
      // Only the four known keys, only the ones the operator actually typed.
      const thresholds: Record<string, string> = {};
      for (const field of THRESHOLD_FIELDS) {
        const value = draft[field.key];
        if (value !== undefined && value.trim() !== "") thresholds[field.key] = value.trim();
      }
      await api.put<ThresholdsResponse>(path, { thresholds });
      await load();
      setMessage("Guardado. Las próximas alertas usan estos valores.");
    } catch (error) {
      setMessage(
        error instanceof ApiError ? error.message : "No se pudo guardar. Inténtalo de nuevo.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (!path) return;
    setBusy(true);
    setMessage(null);
    try {
      // An empty string means "forget what I typed, learn it again".
      const thresholds: Record<string, string> = {};
      for (const field of THRESHOLD_FIELDS) thresholds[field.key] = "";
      await api.put<ThresholdsResponse>(path, { thresholds });
      await load();
      setMessage("Restablecidos a los valores aprendidos.");
    } catch (error) {
      setMessage(
        error instanceof ApiError ? error.message : "No se pudo restablecer. Inténtalo de nuevo.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!countryCode) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <p className="text-sm text-ink-dim">
          Abre un país para ajustar sus umbrales: cada uno aprende los suyos.
        </p>
      </div>
    );
  }

  const byKey = new Map((rows ?? []).map((row) => [row.key, row]));

  return (
    <div className="flex-1 space-y-4 overflow-y-auto p-4">
      <p className="text-sm leading-relaxed text-ink-dim">
        Master Data aprende estos valores de tus propias guías. Escribe uno para
        fijarlo; déjalo vacío y se sigue aprendiendo solo.
      </p>

      {rows === null && !failed && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {failed && (
        <p className="rounded-control border border-line bg-surface p-3.5 text-sm text-ink-dim">
          No se pudieron leer los umbrales ahora.
        </p>
      )}

      {rows !== null && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
          className="space-y-3"
        >
          {THRESHOLD_FIELDS.map((field) => {
            const current = byKey.get(field.key);
            const learned = current ? current.source !== "user" : true;
            return (
              <label key={field.key} className="block">
                <span className="mb-1 flex items-center justify-between text-sm font-medium text-ink-muted">
                  {field.label}
                  <span className="text-xs font-normal text-ink-dim">
                    {current
                      ? learned
                        ? current.confidence !== null
                          ? `aprendido · confianza ${Math.round(current.confidence * 100)}%`
                          : "aprendido"
                        : "fijado por ti"
                      : "sin valor aún"}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={draft[field.key] ?? ""}
                    onChange={(event) =>
                      setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))
                    }
                    aria-label={field.label}
                    className="min-w-0 flex-1 rounded-control border border-line-input bg-surface px-3 py-2 text-base text-ink focus:border-accent focus:outline-none"
                  />
                  {field.suffix && (
                    <span className="w-8 text-xs text-ink-dim">{field.suffix}</span>
                  )}
                </span>
                <span className="mt-1 block text-xs leading-snug text-ink-dim">
                  {field.hint}
                </span>
              </label>
            );
          })}

          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" size="sm" disabled={busy}>
              Guardar
            </Button>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void reset()}>
              Restablecer
            </Button>
          </div>

          {message && <p className="text-sm text-ink-muted">{message}</p>}
        </form>
      )}
    </div>
  );
}
