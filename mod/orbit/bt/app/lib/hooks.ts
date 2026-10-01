'use client';
import { useCallback, useEffect, useRef, useState } from 'react';

/* Poll an async loader: runs now, then every `ms`, and again when the tab
 * comes back into view. Keeps the last good value on error.
 *
 * `cacheKey` makes it local-first: the last good value is kept in
 * localStorage and painted the moment the page mounts, so a reload never
 * shows an empty table while the node answers. The cache is a paint, not a
 * truth — the live value always replaces it. */
const CACHE_PREFIX = 'bt.cache.';

export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = [],
                           cacheKey?: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [cachedAt, setCachedAt] = useState<number | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const fresh = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const v = await loadRef.current();
      fresh.current = true;
      setData(v); setError(null); setCachedAt(null);
      if (cacheKey) {
        try { localStorage.setItem(CACHE_PREFIX + cacheKey, JSON.stringify({ t: Date.now(), v })); }
        catch { /* quota — the paint cache is optional */ }
      }
    }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, [cacheKey]);

  useEffect(() => {
    fresh.current = false;
    if (cacheKey) {
      try {
        const raw = localStorage.getItem(CACHE_PREFIX + cacheKey);
        if (raw) {
          const { t, v } = JSON.parse(raw);
          if (!fresh.current) { setData(v); setCachedAt(t); }
        }
      } catch { /* corrupt entry — the live load replaces it */ }
    }
    setLoading(true);
    refresh();
    const id = ms > 0 ? setInterval(refresh, ms) : null;
    const vis = () => { if (document.visibilityState === 'visible') refresh(); };
    document.addEventListener('visibilitychange', vis);
    return () => { if (id) clearInterval(id); document.removeEventListener('visibilitychange', vis); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refresh, setData, cachedAt };
}

/* localStorage-backed state that is safe during static prerender */
export function useStored<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(init);
  useEffect(() => {
    try { const raw = localStorage.getItem(key); if (raw != null) setV(JSON.parse(raw)); } catch { /* ignore */ }
  }, [key]);
  const set = useCallback((nv: T) => {
    setV(nv);
    try { nv == null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(nv)); } catch { /* ignore */ }
  }, [key]);
  return [v, set];
}

/* ticks every `ms` so "3m ago" labels stay honest */
export function useNow(ms = 1000) {
  const [t, setT] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const id = setInterval(() => setT(Math.floor(Date.now() / 1000)), ms);
    return () => clearInterval(id);
  }, [ms]);
  return t;
}

export function useCopy(): [boolean, (s: string) => void] {
  const [done, setDone] = useState(false);
  const copy = useCallback((s: string) => {
    navigator.clipboard?.writeText(s).then(() => { setDone(true); setTimeout(() => setDone(false), 1100); });
  }, []);
  return [done, copy];
}
