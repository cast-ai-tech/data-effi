"use client";

/** Small data-fetching hooks. No client-state library: the server is the state. */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";

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
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

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
    setLoading(true);
    setError(null);

    api
      .get<T>(path)
      .then((result) => {
        if (!cancelled && mounted.current) setData(result);
      })
      .catch((err: Error) => {
        if (!cancelled && mounted.current) setError(err);
      })
      .finally(() => {
        if (!cancelled && mounted.current) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, error, loading, reload };
}

/** Poll a path until `stopWhen` says the work is finished. */
export function usePolling<T>(
  path: string | null,
  intervalMs: number,
  stopWhen: (data: T) => boolean,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const result = await api.get<T>(path);
        if (cancelled) return;
        setData(result);
        setLoading(false);
        if (!stopWhen(result)) {
          timer = setTimeout(tick, intervalMs);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
        setLoading(false);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, intervalMs, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
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
