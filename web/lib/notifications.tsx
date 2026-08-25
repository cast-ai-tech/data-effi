"use client";

/**
 * Live events and the unread counters behind the bell.
 *
 * The screen updates itself. There is no WebSocket and no SSE - the proxy in
 * front of the API buffers responses and the hosting kills anything longer
 * than ten seconds - so this is a long-poll: `GET /events?since=<id>&wait=6`
 * answers as soon as something happens, or with nothing after six seconds,
 * and the loop asks again. One request in flight per tab, ever.
 *
 * Two things come out of it:
 *
 * - `RevisionContext`, a bare number that `useApi` watches. A finished load,
 *   a worker job or a fresh exchange rate bumps it, and every widget on screen
 *   refetches on its own. It is a separate context on purpose: eighteen
 *   widgets must not re-render each time the unread counter moves.
 * - `useNotifications()`, for the bell and the ingest screen: counters,
 *   `subscribe(type, cb)` for a component that wants the raw event, and the
 *   read/unread actions.
 *
 * Degrades, never breaks: an API that is asleep, missing the endpoint or
 * plain down puts the loop into backoff (2 s doubling to 30 s) and turns
 * `connected` off. Nothing on screen changes colour for it - a quiet bell is
 * not a broken connector.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { ApiError, api } from "@/lib/api";
import type { EventsResponse, LiveEvent, UnreadCount } from "@/lib/types";

// ---------------------------------------------------------------------------
// Tuning
// ---------------------------------------------------------------------------

/** What the server is asked to hold the request for. Under the proxy's 10 s. */
export const WAIT_SECONDS = 6;
/** Client-side abort, comfortably above `WAIT_SECONDS` plus network. */
export const ABORT_MS = 9_000;
export const BACKOFF_MIN_MS = 2_000;
export const BACKOFF_MAX_MS = 30_000;
/** Several events from one load collapse into a single refetch. */
export const REVISION_DEBOUNCE_MS = 500;
/** Survives a reload, not a new tab: events missed while reloading still arrive. */
export const CURSOR_STORAGE_KEY = "masterdata.events.cursor";

/** The events that mean "the numbers changed": every widget refetches. */
const REVISION_EVENTS = new Set(["batch.finished", "job_run.finished", "fx.refreshed"]);

// ---------------------------------------------------------------------------
// Contexts
// ---------------------------------------------------------------------------

/** Bumped when the data behind the dashboards changed. Read by `useApi`. */
export const RevisionContext = createContext<number>(0);

export type EventListener = (event: LiveEvent) => void;

export interface NotificationsContextValue {
  /** Last event id seen. Null until the first `/events` answer. */
  cursor: number | null;
  unreadCount: number;
  criticalUnread: number;
  /** True while the loop is getting answers. Informational, never coloured. */
  connected: boolean;
  /** Listen for one event type (`"*"` for all). Returns the unsubscribe. */
  subscribe: (type: string, listener: EventListener) => () => void;
  /** Re-read the counters from the server. */
  refreshCounts: () => void;
  markRead: (id: number, severity?: string) => Promise<void>;
  markAllRead: (country?: string | null) => Promise<void>;
  /** The shell turns the loop on when it mounts; the login screen never does. */
  setActive: (active: boolean) => void;
}

const NotificationsContext = createContext<NotificationsContextValue | null>(null);

/** With no provider (tests, stray pages) everything is a no-op. */
const INERT: NotificationsContextValue = {
  cursor: null,
  unreadCount: 0,
  criticalUnread: 0,
  connected: false,
  subscribe: () => () => {},
  refreshCounts: () => {},
  markRead: async () => {},
  markAllRead: async () => {},
  setActive: () => {},
};

export function useNotifications(): NotificationsContextValue {
  return useContext(NotificationsContext) ?? INERT;
}

// ---------------------------------------------------------------------------
// Storage helpers. sessionStorage can throw (private mode, quota); never let
// that reach a render.
// ---------------------------------------------------------------------------

function readStoredCursor(): number | null {
  try {
    const raw = window.sessionStorage.getItem(CURSOR_STORAGE_KEY);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isInteger(value) && value >= 0 ? value : null;
  } catch {
    return null;
  }
}

function writeStoredCursor(cursor: number): void {
  try {
    window.sessionStorage.setItem(CURSOR_STORAGE_KEY, String(cursor));
  } catch {
    // Not worth breaking the loop over.
  }
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false);
  // `document.hidden` is read only after mount: the server has no document.
  const [hidden, setHidden] = useState(false);
  const [cursor, setCursorState] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [counts, setCounts] = useState<UnreadCount>({
    unread_count: 0,
    critical_unread_count: 0,
  });
  const [revision, setRevision] = useState(0);

  // Refs, because the loop lives in one long-running effect and must see the
  // current value without being restarted for it.
  const cursorRef = useRef<number | null>(null);
  const listeners = useRef(new Map<string, Set<EventListener>>());
  const revisionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setCursor = useCallback((next: number) => {
    cursorRef.current = next;
    setCursorState(next);
    writeStoredCursor(next);
  }, []);

  useEffect(() => {
    const stored = readStoredCursor();
    if (stored !== null) {
      cursorRef.current = stored;
      setCursorState(stored);
    }
  }, []);

  const refreshCounts = useCallback(() => {
    api
      .get<UnreadCount>("/notifications/unread-count", { auth: false })
      .then((next) => setCounts(next))
      .catch(() => {
        // The counter keeps its last value. A bell that cannot count is still
        // a bell; it is not a reason to draw an error anywhere.
      });
  }, []);

  const bumpRevision = useCallback(() => {
    if (revisionTimer.current) clearTimeout(revisionTimer.current);
    revisionTimer.current = setTimeout(() => {
      revisionTimer.current = null;
      setRevision((n) => n + 1);
    }, REVISION_DEBOUNCE_MS);
  }, []);

  /** Route one event to the counters, the revision and any subscribers. */
  const dispatch = useCallback(
    (event: LiveEvent) => {
      if (event.type === "notification.created") {
        const critical = event.payload.severity === "critical";
        setCounts((current) => ({
          unread_count: current.unread_count + 1,
          critical_unread_count: current.critical_unread_count + (critical ? 1 : 0),
        }));
      } else if (REVISION_EVENTS.has(event.type)) {
        bumpRevision();
      }

      for (const key of [event.type, "*"]) {
        const set = listeners.current.get(key);
        if (!set) continue;
        for (const listener of set) {
          try {
            listener(event);
          } catch {
            // One broken subscriber must not stop the others, or the loop.
          }
        }
      }
    },
    [bumpRevision],
  );

  const subscribe = useCallback((type: string, listener: EventListener) => {
    let set = listeners.current.get(type);
    if (!set) {
      set = new Set();
      listeners.current.set(type, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
    };
  }, []);

  // Pause while the tab is hidden: a background tab polling every six seconds
  // for hours is wasted server time, and the counter is re-read on return.
  useEffect(() => {
    setHidden(document.hidden);
    function onVisibility() {
      setHidden(document.hidden);
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // The loop. One instance per (active, hidden) combination; the cleanup
  // aborts whatever is in flight so a resumed tab never has two.
  useEffect(() => {
    if (!active || hidden) return;

    let stopped = false;
    let controller: AbortController | null = null;
    let wake: (() => void) | null = null;
    let backoff = BACKOFF_MIN_MS;

    refreshCounts();

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          wake = null;
          resolve();
        }, ms);
        wake = () => {
          clearTimeout(timer);
          resolve();
        };
      });

    const loop = async () => {
      while (!stopped) {
        const since = cursorRef.current;
        controller = new AbortController();
        const abortTimer = setTimeout(() => controller?.abort(), ABORT_MS);
        const query =
          since === null
            ? `?wait=${WAIT_SECONDS}`
            : `?since=${since}&wait=${WAIT_SECONDS}`;

        try {
          // `auth: false`: a 401 here ends the loop quietly instead of
          // bouncing the whole tab to the login screen from a background poll.
          const response = await api.get<EventsResponse>(`/events${query}`, {
            auth: false,
            signal: controller.signal,
          });
          clearTimeout(abortTimer);
          if (stopped) return;

          backoff = BACKOFF_MIN_MS;
          setConnected(true);
          // The first call, without `since`, only establishes where "now" is.
          if (since !== null) {
            for (const event of response.events) dispatch(event);
          }
          setCursor(response.cursor);
        } catch (error) {
          clearTimeout(abortTimer);
          if (stopped) return;
          if (error instanceof ApiError && error.status === 401) {
            setConnected(false);
            setActive(false);
            return;
          }
          setConnected(false);
          await sleep(backoff);
          backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
        }
      }
    };

    void loop();

    return () => {
      stopped = true;
      controller?.abort();
      wake?.();
    };
  }, [active, hidden, dispatch, refreshCounts, setCursor]);

  useEffect(
    () => () => {
      if (revisionTimer.current) clearTimeout(revisionTimer.current);
    },
    [],
  );

  const markRead = useCallback(
    async (id: number, severity?: string) => {
      await api.post<void>(`/notifications/${id}/read`);
      setCounts((current) => ({
        unread_count: Math.max(0, current.unread_count - 1),
        critical_unread_count: Math.max(
          0,
          current.critical_unread_count - (severity === "critical" ? 1 : 0),
        ),
      }));
      // The server is the truth; the decrement above only keeps the badge
      // from lagging behind the click.
      refreshCounts();
    },
    [refreshCounts],
  );

  const markAllRead = useCallback(
    async (country?: string | null) => {
      const query = country ? `?country=${encodeURIComponent(country)}` : "";
      await api.post<{ marked: number }>(`/notifications/read-all${query}`);
      if (!country) setCounts({ unread_count: 0, critical_unread_count: 0 });
      refreshCounts();
    },
    [refreshCounts],
  );

  const value = useMemo<NotificationsContextValue>(
    () => ({
      cursor,
      unreadCount: counts.unread_count,
      criticalUnread: counts.critical_unread_count,
      connected,
      subscribe,
      refreshCounts,
      markRead,
      markAllRead,
      setActive,
    }),
    [cursor, counts, connected, subscribe, refreshCounts, markRead, markAllRead],
  );

  return (
    <RevisionContext.Provider value={revision}>
      <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>
    </RevisionContext.Provider>
  );
}
