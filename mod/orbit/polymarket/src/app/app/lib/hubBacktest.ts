"use client";

// The hub's card numbers, in the browser.
//
// The hub asks a different question than the BACKTEST tab: not "how did this
// one strat do over its own window" but "which of these is working right now".
// So every card is replayed over the SAME window, through the SAME engine
// (lib/backtest.ts) the tab and the live session use — a card is a real
// backtest, not a remembered one.
//
// Where the number comes from, in order:
//
//   1. THE WORKER. A timer inside the Next server (lib/server/hubWorker.ts)
//      replays every strat the console has published, over the same 1-day
//      window, every 2 hours — whether or not anyone has the console open. Its
//      results land on disk and this hook loads them on mount, so opening
//      /strats paints a wall of real backtests immediately instead of firing a
//      dozen paginated /activity walks.
//   2. THE LOCAL SNAPSHOT. Runs this browser did itself, kept per window, so
//      switching 7D → 1D → 7D doesn't re-pay for a window you already ran.
//   3. A LIVE REPLAY, for anything still missing or stale (a strat edited
//      since the worker's last pass, or a window the worker doesn't cover).
//      One strat at a time, newest first, publishing as each lands.
//
// The replay itself lives in lib/hubReplay.ts — no React, no localStorage —
// precisely so the worker can run the identical code.

import { useCallback, useEffect, useRef, useState } from "react";
import { SavedIndex } from "./types";
import { DEFAULT_STRATS } from "./defaultStrats";
import {
  HUB_BACKTEST_DAYS, HUB_WINDOWS, TTL_MS, templateBacktestKey, signature,
  backtestOne, backtestTemplate, type HubBacktest, type TraderFeed,
} from "./hubReplay";
import { fetchWorkerBacktests, publishHubManifest, type WorkerStatus } from "./hubCache";

export { HUB_BACKTEST_DAYS, HUB_WINDOWS, templateBacktestKey };
export type { HubBacktest };

const SNAPSHOT_KEY = "poly_hub_backtest_v1";

/** Snapshots are stored per card AND per window: switching 7D → 1D → 7D must
    not throw away the run you already paid for. */
function snapKey(key: string, days: number): string {
  return `${key}@${days}d`;
}

function loadSnapshots(): Record<string, HubBacktest> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(SNAPSHOT_KEY);
    return raw ? (JSON.parse(raw) as Record<string, HubBacktest>) : {};
  } catch {
    return {};
  }
}

/** The stored runs for one window, re-keyed the way cards address them. */
function snapshotsForWindow(days: number): Record<string, HubBacktest> {
  const out: Record<string, HubBacktest> = {};
  const suffix = `@${days}d`;
  for (const [k, v] of Object.entries(loadSnapshots())) {
    if (k.endsWith(suffix)) out[k.slice(0, -suffix.length)] = v;
  }
  return out;
}

function saveSnapshots(all: Record<string, HubBacktest>): void {
  try {
    localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(all));
  } catch {
    // Shared-origin quota — a missing snapshot only costs a re-run.
  }
}

export interface HubBacktestState {
  results: Record<string, HubBacktest>;
  /** Strat ids currently fetching/replaying — cards render a spinner. */
  pending: Set<string>;
  /** True while the worker cache is being read — before this resolves the hub
      genuinely doesn't know yet whether it has numbers, and says so rather
      than flashing "queued" at every card. */
  loading: boolean;
  /** The background worker's last/next pass, for the hub's status line. */
  worker: WorkerStatus | null;
  /** Force a full re-run in THIS browser, ignoring the freshness window. */
  refresh: () => void;
}

export function useHubBacktests(indexes: SavedIndex[], days = HUB_BACKTEST_DAYS): HubBacktestState {
  const [results, setResults] = useState<Record<string, HubBacktest>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [nonce, setNonce] = useState(0);
  // Per-address fetch promises, shared across strats within one pass AND
  // across passes — the hourly cache makes a repeat call cheap, but not free.
  const feedRef = useRef(new Map<string, Promise<TraderFeed>>());

  const refresh = useCallback(() => {
    feedRef.current.clear();
    setNonce((n) => n + 1);
  }, []);

  // Paint the stored runs for THIS window immediately, then fold in whatever
  // the worker has — a hub you've already scanned shows its numbers on the
  // first frame instead of flashing "queued", and a hub you've NEVER opened
  // still does, because the worker ran it for you at 2-hourly cadence.
  useEffect(() => {
    let cancelled = false;
    setResults(snapshotsForWindow(days));
    setLoading(true);
    void (async () => {
      const cache = await fetchWorkerBacktests(days);
      if (cancelled) return;
      setWorker(cache?.status ?? null);
      if (cache?.results) {
        setResults((prev) => {
          const next = { ...prev };
          for (const [k, v] of Object.entries(cache.results)) {
            // Newest wins: a run this browser did after the worker's pass
            // (because the user just edited the strat) is the better number.
            if (!next[k] || next[k].at < v.at) next[k] = v;
          }
          return next;
        });
      }
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [days, nonce]);

  // Hand the worker the roster to replay. Published on every roster/params
  // change, debounced by the signature list below — the worker can only back
  // test strats it knows about, and they live in this browser's localStorage.
  const sigs = indexes.map((i) => `${i.id}:${signature(i, days)}`).join("|");
  useEffect(() => {
    void publishHubManifest(indexes, days);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sigs]);

  // Fill the gaps the worker hasn't covered: strats it has never seen, strats
  // edited since its last pass, or a window it doesn't run.
  useEffect(() => {
    let cancelled = false;
    if (loading) return; // wait for the worker cache — don't duplicate its work

    const run = async () => {
      const publish = (key: string, bt: HubBacktest) => {
        const stamped = { ...bt, by: "live" as const };
        setResults((prev) => ({ ...prev, [key]: stamped }));
        saveSnapshots({ ...loadSnapshots(), [snapKey(key, days)]: stamped });
      };
      const finish = (key: string) => {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      };
      const fresh = (key: string, sig?: string) => {
        const snap = results[key];
        if (!snap) return false;
        if (sig !== undefined && snap.sig !== sig) return false;
        return Date.now() - snap.at < TTL_MS;
      };

      // Newest-touched first — the same order the hub renders, so the card
      // you were just editing resolves first. Templates come after: they cost
      // a leaderboard query each, and the strats you own matter more.
      const queue = [...indexes]
        .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
        .filter((idx) => !fresh(idx.id, signature(idx, days)));
      // A template's signature depends on a roster only a fetch can resolve,
      // so its freshness is the TTL alone — which is also what you want: the
      // recommendation is "top N as of now", and it should age like one.
      const templateQueue = DEFAULT_STRATS.filter((t) => !fresh(templateBacktestKey(t.slug)));
      if (queue.length === 0 && templateQueue.length === 0) return;
      setPending(new Set([
        ...queue.map((i) => i.id),
        ...templateQueue.map((t) => templateBacktestKey(t.slug)),
      ]));

      for (const idx of queue) {
        if (cancelled) return;
        const bt = await backtestOne(idx, days, feedRef.current);
        if (cancelled) return;
        finish(idx.id);
        if (bt) publish(idx.id, bt);
      }

      for (const t of templateQueue) {
        if (cancelled) return;
        const key = templateBacktestKey(t.slug);
        const bt = await backtestTemplate(t, days, feedRef.current);
        if (cancelled) return;
        finish(key);
        if (bt) publish(key, bt);
      }
    };

    void run();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sigs, days, nonce, loading]);

  return { results, pending, loading, worker, refresh };
}
