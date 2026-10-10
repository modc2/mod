'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { get } from './api';
import { useNet } from './net';

export type Load<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

/** GET an API path; refetches when the path or network changes, and every
 *  `every` ms if given. A null path means "not yet" (missing param). */
export function useApi<T = any>(path: string | null, every?: number): Load<T> {
  const { net } = useNet();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);

  const run = useCallback((quiet = false) => {
    if (!path) return;
    const n = ++seq.current;
    if (!quiet) { setLoading(true); setError(null); }
    get<T>(path, net)
      .then(d => { if (n === seq.current) { setData(d); setError(null); } })
      .catch(e => { if (n === seq.current && !quiet) { setError(e.message); setData(null); } })
      .finally(() => { if (n === seq.current) setLoading(false); });
  }, [path, net]);

  useEffect(() => {
    setData(null);
    run();
    if (!every) return;
    const t = setInterval(() => run(true), every);
    return () => clearInterval(t);
  }, [run, every]);

  return { data, error, loading, reload: () => run() };
}

/** A URL query param. Pages using it wrap their body in <Suspense> — the
 *  static export has no server to read the query at build time. */
export function useParam(name: string): string | null {
  return useSearchParams().get(name);
}
