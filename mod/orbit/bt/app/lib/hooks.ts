'use client';
import { useCallback, useEffect, useRef, useState } from 'react';

/* Poll an async loader: runs now, then every `ms`, and again when the tab
 * comes back into view. Keeps the last good value on error. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try { setData(await loadRef.current()); setError(null); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh();
    const id = ms > 0 ? setInterval(refresh, ms) : null;
    const vis = () => { if (document.visibilityState === 'visible') refresh(); };
    document.addEventListener('visibilitychange', vis);
    return () => { if (id) clearInterval(id); document.removeEventListener('visibilitychange', vis); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refresh, setData };
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
