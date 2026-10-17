"use client";

// Trader history for the COPY STUDIO, loaded incrementally.
//
// Unlike BasketSim's loader (which refetches the set when the set changes),
// the studio's address list churns constantly — every hovered row adds one —
// so feeds ACCUMULATE: an address is fetched once per session and stays.
// Bankrolls and market resolutions are topped up for each new batch.
// Small batches keep a burst of hovers from becoming a 429.

import { useEffect, useRef, useState } from "react";

import type { BasketFeeds } from "./basketSim";
import { traderFeed, type TraderFeed } from "./hubReplay";
import { fetchTraderBankrolls } from "./liveSessions";
import { fetchResolvedLegs } from "./hubCache";

const BATCH = 3;
const RESOLVE_LOOKBACK_MS = 30 * 86400_000;

export interface TraderFeedsState {
  feeds: BasketFeeds;
  /** Addresses whose history has landed (possibly empty). */
  loaded: Set<string>;
  loading: Set<string>;
}

function emptyFeeds(): BasketFeeds {
  return { trades: new Map(), positions: new Map(), bankrolls: new Map(), resolved: new Map() };
}

export function useTraderFeeds(addresses: string[]): TraderFeedsState {
  const cache = useRef(new Map<string, Promise<TraderFeed>>());
  const requested = useRef(new Set<string>());
  const [state, setState] = useState<TraderFeedsState>({
    feeds: emptyFeeds(), loaded: new Set(), loading: new Set(),
  });

  const want = [...new Set(addresses.map((a) => a.toLowerCase()))].sort().join(",");

  useEffect(() => {
    const fresh = (want ? want.split(",") : []).filter((a) => !requested.current.has(a));
    if (fresh.length === 0) return;
    fresh.forEach((a) => requested.current.add(a));
    setState((s) => ({ ...s, loading: new Set([...s.loading, ...fresh]) }));

    // No cancellation: a fetched feed is useful to every later render, and
    // `requested` guarantees each address is walked once.
    void (async () => {
      for (let i = 0; i < fresh.length; i += BATCH) {
        const slice = fresh.slice(i, i + BATCH);
        const got = await Promise.all(
          slice.map((a) => traderFeed(a, cache.current).catch(() => ({ trades: [], positions: [] }) as TraderFeed)),
        );
        const bankrolls = await fetchTraderBankrolls(slice).catch(() => new Map<string, number>());
        const cutoff = Date.now() - RESOLVE_LOOKBACK_MS;
        const ids = new Set<string>();
        got.forEach((f) => f.trades.forEach((t) => {
          if (t.timestamp >= cutoff && t.conditionId) ids.add(t.conditionId);
        }));
        const resolved = ids.size > 0
          ? await fetchResolvedLegs([...ids]).catch(() => new Map<string, number>())
          : new Map<string, number>();

        setState((s) => {
          const feeds: BasketFeeds = {
            trades: new Map(s.feeds.trades),
            positions: new Map(s.feeds.positions),
            bankrolls: new Map([...s.feeds.bankrolls, ...bankrolls]),
            resolved: new Map([...(s.feeds.resolved ?? new Map()), ...resolved]),
          };
          slice.forEach((a, j) => {
            feeds.trades.set(a, got[j].trades);
            feeds.positions.set(a, got[j].positions);
          });
          const loading = new Set(s.loading);
          slice.forEach((a) => loading.delete(a));
          return { feeds, loaded: new Set([...s.loaded, ...slice]), loading };
        });
      }
    })();
  }, [want]);

  return state;
}
