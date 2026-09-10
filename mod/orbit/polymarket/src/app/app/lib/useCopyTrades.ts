"use client";

// The copy-trades feed, as a hook.
//
// One GET to `/polymarket/api/copytrades` (the Next route, not the Rust API —
// so the token is attached here rather than by access.ts's fetch patch, same
// as lib/hubCache.ts). The route answers with ROWS; filtering happens in the
// browser against lib/semanticFilter.ts, which is why re-typing a query never
// hits the network.
//
// Polling is opt-in per mount (`enabled`). The sidebar's block only mounts
// while it is open, and a wallet walk plus a book of leaders is not something
// to run behind a collapsed section.

import { useCallback, useEffect, useRef, useState } from "react";

import { getAccessToken } from "./access";
import type { CopySummary, CopyTradeRow, LeaderScore } from "./copyTrades";

const ROUTE = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/copytrades`;

export interface CopyTradesResponse {
  days: number;
  eoa: string | null;
  /** The wallet the fills were read from — the deposit/proxy wallet, not the
      signing EOA. Null ⇒ the leaders' half is all there is. */
  wallet: string | null;
  matchMinutes: number;
  summary: CopySummary;
  leaders: LeaderScore[];
  rows: CopyTradeRow[];
  truncated?: boolean;
  /** Leaders the feed store has never fetched — a fact about the cache. */
  warming: string[];
  fillsError?: string;
  note?: string;
}

export interface UseCopyTrades {
  data: CopyTradesResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useCopyTrades(opts: {
  days: number;
  enabled?: boolean;
  /** 0 ⇒ fetch once. */
  pollMs?: number;
}): UseCopyTrades {
  const { days, enabled = true, pollMs = 90_000 } = opts;
  const [data, setData] = useState<CopyTradesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A wallet walk can outlive the poll interval; never stack two.
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (!enabled || inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const token = getAccessToken();
      const res = await fetch(`${ROUTE}?days=${days}`, {
        cache: "no-store",
        headers: token ? { authorization: `Bearer ${token}` } : {},
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body as { error?: string })?.error || `HTTP ${res.status}`);
      setData(body as CopyTradesResponse);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [days, enabled]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    if (!pollMs) return;
    const t = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(t);
  }, [refresh, enabled, pollMs]);

  return { data, loading, error, refresh };
}
