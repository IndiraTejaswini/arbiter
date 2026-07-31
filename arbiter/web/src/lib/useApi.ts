import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

/**
 * Async data hooks.
 *
 * Three properties every screen depends on:
 *   - a request that is superseded never overwrites a newer one (stale
 *     responses arriving out of order is the classic dashboard bug);
 *   - unmounting cancels state updates;
 *   - `loading` and `error` are always distinguishable from "loaded and
 *     empty", so a table can say "no cases" rather than showing a spinner
 *     forever.
 */

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean } = {},
): AsyncState<T> {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);

  // Monotonic request id: only the newest in-flight request may write state.
  const latest = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const id = ++latest.current;
    setLoading(true);
    setError(null);

    fetcher()
      .then((result) => {
        if (!mounted.current || id !== latest.current) return;
        setData(result);
        setError(null);
      })
      .catch((caught) => {
        if (!mounted.current || id !== latest.current) return;
        setData(null);
        setError(caught instanceof Error ? caught : new Error(String(caught)));
      })
      .finally(() => {
        if (mounted.current && id === latest.current) setLoading(false);
      });
    // `fetcher` is intentionally not a dependency: callers pass an inline
    // closure, and depending on its identity would refetch every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

/** An action with its own pending/error state — used for every mutation so
 *  a button can disable itself and report failure in place. */
export function useAction<Args extends unknown[], R>(
  action: (...args: Args) => Promise<R>,
): {
  run: (...args: Args) => Promise<R | undefined>;
  pending: boolean;
  error: Error | null;
  result: R | null;
  reset: () => void;
} {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<R | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const run = useCallback(
    async (...args: Args) => {
      setPending(true);
      setError(null);
      try {
        const value = await action(...args);
        if (mounted.current) setResult(value);
        return value;
      } catch (caught) {
        if (mounted.current) setError(caught instanceof Error ? caught : new Error(String(caught)));
        return undefined;
      } finally {
        if (mounted.current) setPending(false);
      }
    },
    [action],
  );

  const reset = useCallback(() => { setError(null); setResult(null); }, []);
  return { run, pending, error, result, reset };
}

/** Poll while `active`. Used only where the backend has no push channel —
 *  case progress uses SSE instead. */
export function useInterval(callback: () => void, ms: number | null): void {
  const saved = useRef(callback);
  useEffect(() => { saved.current = callback; }, [callback]);
  useEffect(() => {
    if (ms === null) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}
