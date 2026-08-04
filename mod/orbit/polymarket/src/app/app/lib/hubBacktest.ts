"use client";

// N-day backtest for EVERY strat on the hub — saved AND recommended.
//
// The hub asks a different question than the BACKTEST tab: not "how did this
// one strat do over its own window" but "which of these is working right now".
// So every card is replayed over the SAME window, through the SAME engine
// (lib/backtest.ts) the tab and the live session use — a card is a real
// backtest, not a remembered one. The old cards showed `lastPnl`, a snapshot
// left behind by whatever window the strat was last opened with, which made
// two cards' numbers incomparable.
//
// Recommended strats (DEFAULT_STRATS) are scored the same way, which is the
// only honest way to recommend one: a template is materialized into exactly
// the SavedIndex forking it would create — today's leaderboard roster and all
// (`templateIndex`) — and that is what gets replayed. The number on a template
// card is the strat you'd actually get, not a hand-written claim about it.
//
// Cost control, because a hub can hold a dozen strats plus every template:
//   • traders are fetched ONCE across all strats (they overlap heavily) and
//     ride the same hourly trade cache the console already fills;
//   • results are persisted per window, so a revisit — or switching back to a
//     window you already ran — paints instantly, and only strats whose params
//     changed (or whose snapshot went stale) re-run;
//   • strats replay one at a time, newest first, and each publishes as soon
//     as it lands — the grid fills in rather than blocking on the slowest.

import { useCallback, useEffect, useRef, useState } from "react";
import { PolymarketPosition, PolymarketTrade, SavedIndex } from "./types";
import { fetchPositions, fetchWalletTradesUntil, MAX_LOOKBACK_DAYS } from "./polymarket";
import { fetchTraderBankrolls } from "./liveSessions";
import { Strat } from "./strats/strat";
import { runBacktest, stratBacktestParams } from "./backtest";
import { DEFAULT_STRATS, templateIndex, templateRoster, type StratTemplate } from "./defaultStrats";

/** The window every card is measured over, unless the user picks another. */
export const HUB_BACKTEST_DAYS = 1;
/** Windows the hub offers. 30 is the data-api's lookback ceiling
    (MAX_LOOKBACK_DAYS) — asking for more silently returns less. */
export const HUB_WINDOWS = [1, 3, 7, 14, 30];

/** Cards address their backtest by this key: saved strats by id, templates by
    slug. One namespace, so `backtests[hubKey]` works for both card types. */
export function templateBacktestKey(slug: string): string {
  return `tpl:${slug}`;
}
/** How long a card's replay stays good before the hub re-runs it. */
const TTL_MS = 5 * 60_000;
/** Sparkline resolution — enough to read the shape, small enough to persist. */
const CURVE_POINTS = 48;
const SNAPSHOT_KEY = "poly_hub_backtest_v1";
/** Traders fetched in parallel. Deliberately small: each is a paginated
    /activity walk, and the console shares its rate gate with the live engine. */
const FETCH_CONCURRENCY = 4;

export interface HubBacktest {
  /** Net PnL of the replay ($) — costs modeled exactly as the live engine. */
  pnl: number;
  /** pnl as a % of the paper capital the replay started with. */
  roi: number;
  trades: number;
  skipped: number;
  capital: number;
  /** Window replayed, in days — the N the card labels itself with. */
  days: number;
  /** Traders replayed. */
  traders: number;
  /** Equity through the window, thinned for the card sparkline. */
  curve: number[];
  /** Why this replay is empty, when it is: a strat with no watchlist, or one
      that originates its own trades, has nothing to copy — that's an answer,
      not a $0 result, and the card must not print it as breaking even. */
  note?: string;
  /** When this replay ran, and what it ran on (params fingerprint). */
  at: number;
  sig: string;
}

/** Everything about a strat that changes its backtest. A card whose signature
    still matches its snapshot doesn't need replaying. */
function signature(idx: SavedIndex, days: number): string {
  const p = stratBacktestParams(idx);
  return JSON.stringify([
    days,
    idx.traders.filter((t) => t.enabled !== false).map((t) => [t.address, t.weight]),
    p.capital, p.minTrade, p.maxTrade, p.maxOpenPositions, p.stopLossPct,
    p.takeProfitFrac, p.marketQuery, p.pollMinutes,
    idx.maxPerCycle ?? 3, idx.tradeFilters ?? null, idx.filter ?? null,
  ]);
}

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

/** Thin an equity history down to CURVE_POINTS evenly-spaced samples. */
function thinCurve(history: { liq: number; pos: number }[]): number[] {
  if (history.length === 0) return [];
  if (history.length <= CURVE_POINTS) return history.map((h) => h.liq + h.pos);
  const out: number[] = [];
  for (let i = 0; i < CURVE_POINTS; i++) {
    const h = history[Math.round((i * (history.length - 1)) / (CURVE_POINTS - 1))];
    out.push(h.liq + h.pos);
  }
  return out;
}

export interface HubBacktestState {
  results: Record<string, HubBacktest>;
  /** Strat ids currently fetching/replaying — cards render a spinner. */
  pending: Set<string>;
  /** Force a full re-run, ignoring the TTL. */
  refresh: () => void;
}

export function useHubBacktests(indexes: SavedIndex[], days = HUB_BACKTEST_DAYS): HubBacktestState {
  const [results, setResults] = useState<Record<string, HubBacktest>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [nonce, setNonce] = useState(0);
  // Per-address fetch promises, shared across strats within one pass AND
  // across passes — the hourly cache makes a repeat call cheap, but not free.
  const feedRef = useRef(new Map<string, Promise<TraderFeed>>());

  const refresh = useCallback(() => {
    feedRef.current.clear();
    setNonce((n) => n + 1);
  }, []);

  // Paint the stored runs for THIS window immediately — a hub you've already
  // scanned shows its numbers on the first frame instead of flashing "queued".
  useEffect(() => {
    setResults(snapshotsForWindow(days));
  }, [days]);

  // Re-run when the roster or any strat's params change — the signature list
  // is the dependency, so switching tabs or re-rendering doesn't refetch.
  const sigs = indexes.map((i) => `${i.id}:${signature(i, days)}`).join("|");

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      const snapshots = loadSnapshots();
      const publish = (key: string, bt: HubBacktest) => {
        setResults((prev) => ({ ...prev, [key]: bt }));
        saveSnapshots({ ...loadSnapshots(), [snapKey(key, days)]: bt });
      };
      const finish = (key: string) => {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      };

      // Newest-touched first — the same order the hub renders, so the card
      // you were just editing resolves first. Templates come after: they cost
      // a leaderboard query each, and the strats you own matter more.
      const queue = [...indexes]
        .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
        .filter((idx) => {
          const snap = snapshots[snapKey(idx.id, days)];
          return !(snap && snap.sig === signature(idx, days) && Date.now() - snap.at < TTL_MS);
        });
      // A template's signature depends on a roster only a fetch can resolve,
      // so its freshness is the TTL alone — which is also what you want: the
      // recommendation is "top N as of now", and it should age like one.
      const templateQueue = DEFAULT_STRATS.filter((t) => {
        const snap = snapshots[snapKey(templateBacktestKey(t.slug), days)];
        return !(snap && Date.now() - snap.at < TTL_MS);
      });
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
  }, [sigs, days, nonce]);

  return { results, pending, refresh };
}

interface TraderFeed {
  positions: PolymarketPosition[];
  trades: PolymarketTrade[];
}

/** Fetch one trader's history, memoized per address for the session. The
    30-day pull is what the console already caches hourly (and what the strat's
    scoring window needs) — asking for 1 day here would trade a smaller
    download for a cache miss on every console visit. */
function traderFeed(addr: string, cache: Map<string, Promise<TraderFeed>>): Promise<TraderFeed> {
  const key = addr.toLowerCase();
  const hit = cache.get(key);
  if (hit) return hit;
  const cutoffSec = Math.floor((Date.now() - MAX_LOOKBACK_DAYS * 86400_000) / 1000);
  const p = (async (): Promise<TraderFeed> => {
    try {
      const [positions, trades] = await Promise.all([
        fetchPositions(addr),
        fetchWalletTradesUntil(addr, cutoffSec, undefined, 2000),
      ]);
      return { positions, trades };
    } catch {
      return { positions: [], trades: [] };
    }
  })();
  cache.set(key, p);
  return p;
}

/** Replay a RECOMMENDED strat: materialize the template into the SavedIndex a
    fork would produce — seeded from the same cached leaderboard roster the
    fork will use — and replay that. The card's number is the strat you'd get. */
async function backtestTemplate(
  t: StratTemplate,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
): Promise<HubBacktest | null> {
  try {
    const roster = await templateRoster(t);
    // Pinned `now`: id/name never reach the replay, and letting them move
    // would churn the signature for nothing.
    return await backtestOne(templateIndex(t, roster, 0), days, cache);
  } catch {
    return null;
  }
}

/** Replay ONE strat over the window. A strat with nothing to copy still
    returns a result — carrying the REASON, so its card says "originates its
    own trades" instead of printing a $0 that reads as breaking even. */
async function backtestOne(
  idx: SavedIndex,
  days: number,
  cache: Map<string, Promise<TraderFeed>>,
): Promise<HubBacktest | null> {
  const watchlist = idx.traders.filter((t) => t.enabled !== false).map((t) => t.address);
  if (watchlist.length === 0) {
    const p = stratBacktestParams(idx);
    return {
      pnl: 0, roi: 0, trades: 0, skipped: 0, capital: p.capital, days, traders: 0,
      curve: [],
      note: idx.momentum
        ? "originates its own trades — no copied flow to replay"
        : "no traders to copy",
      at: Date.now(),
      sig: signature(idx, days),
    };
  }

  const traderTrades = new Map<string, PolymarketTrade[]>();
  const traderPositions = new Map<string, PolymarketPosition[]>();
  for (let i = 0; i < watchlist.length; i += FETCH_CONCURRENCY) {
    const slice = watchlist.slice(i, i + FETCH_CONCURRENCY);
    const feeds = await Promise.all(slice.map((a) => traderFeed(a, cache)));
    slice.forEach((addr, j) => {
      traderTrades.set(addr, feeds[j].trades);
      traderPositions.set(addr, feeds[j].positions);
    });
  }

  const traderWeights: Record<string, number> = {};
  for (const t of idx.traders) traderWeights[t.address] = Math.round(t.weight * 100);
  const traderBankrolls = await fetchTraderBankrolls(watchlist);
  const p = stratBacktestParams(idx);

  const { sim } = runBacktest({
    watchlist,
    traderTrades,
    traderPositions,
    traderWeights,
    traderBankrolls,
    strat: new Strat({
      maxPerCycle: idx.maxPerCycle ?? 3,
      marketQuery: p.marketQuery,
      tradeFilters: idx.tradeFilters ?? {},
      filter: idx.filter ?? undefined,
    }),
    days,
    ...p,
  });

  return {
    pnl: sim.netPnl,
    roi: p.capital > 0 ? Math.round((sim.netPnl / p.capital) * 10_000) / 100 : 0,
    trades: sim.rows.length,
    skipped: sim.skipped,
    capital: p.capital,
    days,
    traders: watchlist.length,
    curve: thinCurve(sim.equityHistory),
    // Observed flow but zero executions is a real answer too — every
    // candidate was gated. Say that rather than implying a flat result.
    note: sim.rows.length === 0 ? "every candidate was filtered out" : undefined,
    at: Date.now(),
    sig: signature(idx, days),
  };
}
