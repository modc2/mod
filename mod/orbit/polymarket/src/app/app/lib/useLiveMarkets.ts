"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PolymarketMarket } from "./types";
import { fetchMarkets } from "./polymarket";
import { useClobStream } from "./useClobStream";

interface Options {
  limit?: number;
  order?: string;
  /** How often to refresh the market *list* (membership/metadata) via REST.
   *  Live prices stream over the websocket, so this no longer needs to be
   *  fast — it only catches new/removed markets. Default 5 min. */
  refreshMs?: number;
  pauseWhenHidden?: boolean;
}

/**
 * Top markets with LIVE streaming prices.
 *
 * - Fetches the market list once (plus a slow REST refresh for membership /
 *   metadata changes) instead of polling prices every few seconds.
 * - Subscribes to the CLOB websocket for each market's YES token and merges
 *   the streamed midpoint into `outcomePrices[0]`.
 * - Tracks the previous YES price per market so consumers can render
 *   price-delta flashes.
 * - Streaming + list-refresh both pause while the tab is hidden.
 */
export function useLiveMarkets({
  limit = 24,
  order = "volume",
  refreshMs = 5 * 60_000,
  pauseWhenHidden = true,
}: Options = {}) {
  const [base, setBase] = useState<PolymarketMarket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── Slow REST refresh of the market list (NOT prices) ──
  useEffect(() => {
    let alive = true;

    const tick = async () => {
      try {
        const next = await fetchMarkets(limit, order);
        if (!alive) return;
        setBase(next);
        setLoading(false);
        setError(null);
      } catch (e) {
        if (!alive) return;
        setError((e as Error).message);
        setLoading(false);
      }
    };

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer) return;
      timer = setInterval(tick, refreshMs);
    };
    const stop = () => {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    };

    void tick();
    start();

    const onVisibility = () => {
      if (!pauseWhenHidden) return;
      if (document.hidden) stop();
      else {
        void tick();
        start();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [limit, order, refreshMs, pauseWhenHidden]);

  // ── Live prices over the CLOB websocket ──
  // YES token = clobTokenIds[0]; markets without it just keep their REST price.
  const assetIds = useMemo(
    () => base.map((m) => m.clobTokenIds?.[0]).filter(Boolean) as string[],
    [base],
  );
  const { prices: streamPrices } = useClobStream(assetIds, { pauseWhenHidden });

  // Merge streamed YES prices into the market list.
  const markets = useMemo<PolymarketMarket[]>(() => {
    if (streamPrices.size === 0) return base;
    return base.map((m) => {
      const tid = m.clobTokenIds?.[0];
      const live = tid ? streamPrices.get(tid) : undefined;
      if (live == null) return m;
      return { ...m, outcomePrices: [live, 1 - live] };
    });
  }, [base, streamPrices]);

  // Track the previous YES price per market for delta flashes. We snapshot the
  // value *before* each change so consumers can color the move green/red.
  const lastRef = useRef<Map<string, number>>(new Map());
  const [prevPrices, setPrevPrices] = useState<Map<string, number>>(new Map());
  useEffect(() => {
    const updates: [string, number][] = [];
    for (const m of markets) {
      const yes = m.outcomePrices?.[0] ?? 0;
      const prev = lastRef.current.get(m.conditionId);
      lastRef.current.set(m.conditionId, yes);
      if (prev != null && prev !== yes) updates.push([m.conditionId, prev]);
    }
    if (updates.length === 0) return;
    setPrevPrices((curr) => {
      const next = new Map(curr);
      for (const [cid, p] of updates) next.set(cid, p);
      return next;
    });
  }, [markets]);

  return { markets, loading, error, prevPrices };
}
