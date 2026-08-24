"use client";

/** Small data-fetching hooks. No client-state library: the server is the state. */

import { useCallback, useContext, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { RevisionContext } from "@/lib/notifications";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
}

/**
 * GET a path and keep the result. `deps` controls refetching.
 *
 * Errors are returned, never thrown: a widget that fails must degrade in place
 * rather than blank the whole dashboard.
 *
 * It also watches `RevisionContext`: when a load finishes or a worker job
 * runs, the provider bumps the number and every widget on screen refetches
 * without anyone touching it. That refetch is SILENT - the old figures stay on
 * screen until the new ones land - because eighteen cards flashing skeletons
 * at once every time a file is processed would read as the dashboard breaking.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);
  // Default 0 with no provider: a test or a stray page fetches once, as before.
  const revision = useContext(RevisionContext);
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    let cancelled = false;

    // Same path, same manual reload count, only the revision moved: refresh
    // behind the numbers already on screen.
    const key = `${path}|${nonce}`;
    const silent = lastKey.current === key;
    lastKey.current = key;
    if (!silent) {
      setLoading(true);
      setError(null);
    }

    api
      .get<T>(path)
      .then((result) => {
        if (!cancelled && mounted.current) {
          setData(result);
          if (silent) setError(null);
        }
      })
      .catch((err: Error) => {
        // A silent refresh that fails keeps the last good answer: the reader
        // is still looking at real, if slightly older, numbers.
        if (!cancelled && mounted.current && !silent) setError(err);
      })
      .finally(() => {
        if (!cancelled && mounted.current) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, revision, ...deps]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, error, loading, reload };
}

/** Read/write a value in localStorage, SSR-safe. */
export function usePersistentState<T>(
  key: string,
  initial: T,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(initial);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(key);
      if (stored !== null) setValue(JSON.parse(stored) as T);
    } catch {
      // A corrupt entry must not break the page.
    }
  }, [key]);

  const update = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // Private browsing, quota, whatever. Not worth breaking the UI.
      }
    },
    [key],
  );

  return [value, update];
}
