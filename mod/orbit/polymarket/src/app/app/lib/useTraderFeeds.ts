"use client";

// Trader history for the COPY STUDIO, loaded incrementally.
//
// Unlike BasketSim's loader (which refetches the set when the set changes),
// the studio's address list churns constantly — every hovered row adds one —
// so feeds ACCUMULATE: an address is fetched once per session and stays.
// Bankrolls and market resolutions are topped up for each new batch.
// One worker, small batches: a whole board warming up never becomes a 429.

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

/** `addresses` are wanted in order; `urgent` ones (the hovered row, the
    allocation) jump the queue. ONE worker drains it, BATCH at a time, so a
    40-row board warming up in the background never fans out into parallel
    history walks — and anything that drops off the board before its turn
    is simply never fetched. */
export function useTraderFeeds(addresses: string[], urgent: string[] = []): TraderFeedsState {
  const cache = useRef(new Map<string, Promise<TraderFeed>>());
  const requested = useRef(new Set<string>());
  const queue = useRef<string[]>([]);
  const running = useRef(false);
  const [state, setState] = useState<TraderFeedsState>({
    feeds: emptyFeeds(), loaded: new Set(), loading: new Set(),
  });

  const norm = (xs: string[]) => [...new Set(xs.map((a) => a.toLowerCase()))];
  const want = norm(addresses).join(",");
  const first = norm(urgent).join(",");

  useEffect(() => {
    const hot = first ? first.split(",") : [];
    const rest = (want ? want.split(",") : []).filter((a) => !hot.includes(a));
    queue.current = [...hot, ...rest].filter((a) => !requested.current.has(a));
    setState((s) => ({ ...s, loading: new Set(queue.current) }));
    if (running.current || queue.current.length === 0) return;
    running.current = true;

    // No cancellation: a fetched feed is useful to every later render, and
    // `requested` guarantees each address is walked once.
    void (async () => {
      try {
        while (queue.current.length > 0) {
          const slice = queue.current.splice(0, BATCH);
          slice.forEach((a) => requested.current.add(a));
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
            return { feeds, loaded: new Set([...s.loaded, ...slice]), loading: new Set(queue.current) };
          });
        }
      } finally {
        running.current = false;
      }
    })();
  }, [want, first]);

  return state;
}
