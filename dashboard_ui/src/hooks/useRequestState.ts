import { useCallback, useEffect, useRef, useState } from "react";

export type RequestState<T> = {
  loading: boolean;
  error: string | null;
  data: T | null;
  lastUpdatedAt: number | null;
};

export function useRequestState<T>(opts: {
  load: () => Promise<T>;
  enabled?: boolean;
  initialData?: T | null;
  key?: unknown;
  onError?: (err: string) => void;
  ignoreError?: (err: string) => boolean;
}): { state: RequestState<T>; refresh: () => Promise<void>; setData: (data: T | null) => void } {
  const enabled = opts.enabled ?? true;
  const [state, setState] = useState<RequestState<T>>({
    loading: Boolean(enabled),
    error: null,
    data: opts.initialData ?? null,
    lastUpdatedAt: null,
  });
  const reqIdRef = useRef(0);

  const setData = useCallback((data: T | null) => {
    setState((s) => ({ ...s, data }));
  }, []);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    const reqId = ++reqIdRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await opts.load();
      if (reqId !== reqIdRef.current) return;
      setState({ loading: false, error: null, data, lastUpdatedAt: Date.now() });
    } catch (e) {
      if (reqId !== reqIdRef.current) return;
      const msg = String(e);
      try {
        opts.onError?.(msg);
      } catch {
        // ignore
      }
      try {
        if (opts.ignoreError?.(msg)) {
          setState((s) => ({ ...s, loading: false, lastUpdatedAt: Date.now() }));
          return;
        }
      } catch {
        // ignore
      }
      setState((s) => ({ ...s, loading: false, error: msg, lastUpdatedAt: Date.now() }));
    }
  }, [enabled, opts]);

  useEffect(() => {
    if (!enabled) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, opts.key]);

  return { state, refresh, setData };
}
