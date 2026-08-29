// MY COPY TRADES — the leaders' trades and my fills, joined into one feed.
//
// The console could always show two lists: what the leaders did (the engine's
// in-memory log) and what I did (on-chain fills from the data-api). What it
// could not show anywhere outside the LIVE desk is the ONE thing copy trading
// is judged on: for each trade a leader made, did I get it, how late, and at
// what price — and for each fill of mine, whose trade it was.
//
// Nothing links the two upstream. A fill carries no leader tag; it is a row in
// my wallet's activity. So the join is inferred, and the rules are deliberately
// tight enough that an unrelated re-entry hours later cannot claim a fill:
//
//   same market (conditionId, else the normalized title)
//   same side
//   my fill at or AFTER the leader's, within `matchMs` (30 min by default)
//   nearest in time wins, and one leader trade is claimed at most once
//
// A fill with no leader behind it is NOT an error and is never hidden: the
// engine's own stop-loss and take-profit exits have no leader trade, and so
// does anything traded by hand from the same wallet. It is reported as
// `unattributed`, which is a number worth seeing rather than a discrepancy to
// paper over.
//
// PURE — no network, no React, no clock except the `now` passed in. The route
// (app/api/copytrades/route.ts) fetches, this decides, lib/__test__.ts pins it.
// Same split as lib/basketSim.ts.

import type { PolymarketTrade } from "./types";
import type { SemanticTrade } from "./semanticFilter";
import { shortAddress } from "./identityStrat";

/** One row of the feed. Satisfies `SemanticTrade`, so the semantic filter runs
    over both halves without a second shape. */
export interface CopyTradeRow extends SemanticTrade {
  id: string;
  /** "leader" = a trade one of the traders I copy made. "mine" = an on-chain
      fill of my own. */
  kind: "mine" | "leader";
  conditionId?: string;
  notional: number;
  /** Fills merged into this row (see `mergeFills`). 1 ⇒ a single fill. */
  count: number;
  /** Oldest fill in the merge; equals `timestamp` when count is 1. */
  firstTs: number;

  // ── mine ──
  /** Seconds between the leader's trade and my fill. Null ⇒ unattributed. */
  lagSec?: number | null;
  /** What the lag cost, in cents of entry price: mine − theirs on a BUY,
      theirs − mine on a SELL. Positive = worse than the leader. */
  slipCents?: number | null;

  // ── leader ──
  /** Leader rows only: a fill of mine was matched to it. */
  copied?: boolean;
  /** When that fill landed. */
  copiedAt?: number | null;
  /** The fill's id, so the row can point at its twin. */
  mirrorId?: string | null;
}

export interface CopySummary {
  /** Rows in each half, after the window. */
  mine: number;
  leader: number;
  /** Distinct leaders with at least one trade in the window. */
  leaders: number;
  /** Leader trades I have a fill behind. */
  copied: number;
  /** Leader trades I don't. */
  missed: number;
  /** copied / leader, 0–1. The headline: how much of the flow I actually got. */
  coverage: number;
  /** USD I moved, and USD they moved, in the window. */
  myNotional: number;
  leaderNotional: number;
  /** Fills of mine with no leader trade behind them — engine exits, or hand
      trades from the same wallet. */
  unattributed: number;
  /** Median seconds between a leader's trade and my fill of it. */
  medianLagSec: number | null;
  /** Mean signed slippage in cents against the leader's price. */
  avgSlipCents: number | null;
}

export interface BuildOptions {
  /** ms epoch "now" — the window is measured back from it. */
  now?: number;
  /** Only rows at or after `now - windowMs`. Default 7 days. */
  windowMs?: number;
  /** How long after a leader's trade a fill may still be counted as mirroring
      it. Default 30 minutes — the same constant the LIVE desk's board uses. */
  matchMs?: number;
  /** address → display label, for the rows. */
  labels?: Record<string, string | null | undefined>;
}

export const DEFAULT_MATCH_MS = 30 * 60_000;
const DEFAULT_WINDOW_MS = 7 * 86_400_000;
/** Consecutive fills this close together are one trade split by the book. */
const MERGE_MS = 120_000;

/** Money a trade actually moved. `usdcSize` is what the data-api reports and
    it is not price × size on a SELL (Polymarket charges a sell-side fee), so
    prefer it and fall back only when it's absent. */
export function tradeNotional(t: PolymarketTrade): number {
  return Number.isFinite(t.usdcSize) && (t.usdcSize as number) > 0
    ? (t.usdcSize as number)
    : t.price * t.size;
}

/** Titles differ in case and whitespace between the activity feed and gamma;
    the conditionId is the real key and this is only the fallback. */
function titleKey(t: { conditionId?: string; market: string }): string {
  return (t.conditionId || t.market || "").trim().toLowerCase();
}

/** Merge a run of fills that are obviously one trade: same market, same
    outcome token, same side, within MERGE_MS. Size and money add up; the price
    becomes the size-weighted average, which is what was actually paid. */
export function mergeFills(fills: PolymarketTrade[]): PolymarketTrade[] {
  const sorted = [...fills].sort((a, b) => a.timestamp - b.timestamp);
  const out: (PolymarketTrade & { count?: number; firstTs?: number })[] = [];
  for (const f of sorted) {
    const prev = out[out.length - 1];
    const same =
      prev &&
      prev.side === f.side &&
      titleKey(prev) === titleKey(f) &&
      (prev.asset ?? "") === (f.asset ?? "") &&
      f.timestamp - prev.timestamp <= MERGE_MS;
    if (!same) {
      out.push({ ...f, count: 1, firstTs: f.timestamp });
      continue;
    }
    const size = prev.size + f.size;
    prev.price = size > 0 ? (prev.price * prev.size + f.price * f.size) / size : f.price;
    prev.size = size;
    prev.usdcSize = tradeNotional(prev) + tradeNotional(f);
    prev.pnl = (prev.pnl ?? 0) + (f.pnl ?? 0);
    prev.timestamp = f.timestamp;
    prev.count = (prev.count ?? 1) + 1;
  }
  return out;
}

function label(address: string, labels?: BuildOptions["labels"]): string {
  const l = labels?.[address.toLowerCase()];
  return (l ?? "").trim() || shortAddress(address);
}

export interface BuildInput {
  /** My on-chain fills — the deposit wallet's activity. */
  mine: PolymarketTrade[];
  /** leader address → that leader's trades. */
  leaders: Record<string, PolymarketTrade[]> | Map<string, PolymarketTrade[]>;
}

/** Join my fills to the leader trades they mirror, and report both halves as
    one time-ordered feed plus the numbers that judge the copy. */
export function buildCopyTrades(input: BuildInput, opts: BuildOptions = {}): {
  rows: CopyTradeRow[];
  summary: CopySummary;
} {
  const now = opts.now ?? Date.now();
  const windowMs = opts.windowMs ?? DEFAULT_WINDOW_MS;
  const matchMs = opts.matchMs ?? DEFAULT_MATCH_MS;
  const cutoff = now - windowMs;
  const leaderEntries =
    input.leaders instanceof Map ? Array.from(input.leaders.entries()) : Object.entries(input.leaders);

  // ── Leader rows, bucketed by market+side so the join is a lookup, not a
  //    scan of everything each leader ever did. ──
  const leaderRows: CopyTradeRow[] = [];
  const buckets = new Map<string, CopyTradeRow[]>();
  for (const [rawAddr, trades] of leaderEntries) {
    const address = rawAddr.toLowerCase();
    const name = label(address, opts.labels);
    for (const t of mergeFills((trades ?? []).filter((t) => t.timestamp >= cutoff - matchMs))) {
      const row: CopyTradeRow = {
        id: `L:${address}:${t.id || `${t.conditionId}:${t.timestamp}`}`,
        kind: "leader",
        market: t.market,
        conditionId: t.conditionId,
        side: t.side,
        price: t.price,
        size: t.size,
        notional: tradeNotional(t),
        timestamp: t.timestamp,
        outcome: t.outcome ?? null,
        leader: address,
        leaderLabel: name,
        copied: false,
        copiedAt: null,
        mirrorId: null,
        count: (t as PolymarketTrade & { count?: number }).count ?? 1,
        firstTs: (t as PolymarketTrade & { firstTs?: number }).firstTs ?? t.timestamp,
      };
      leaderRows.push(row);
      const key = `${titleKey(t)}|${t.side}`;
      const bucket = buckets.get(key);
      if (bucket) bucket.push(row);
      else buckets.set(key, [row]);
    }
  }
  for (const bucket of buckets.values()) bucket.sort((a, b) => a.timestamp - b.timestamp);

  // ── My fills, each attributed to the nearest unclaimed leader trade that
  //    came BEFORE it in the same market and side. ──
  const myRows: CopyTradeRow[] = [];
  const lags: number[] = [];
  const slips: number[] = [];
  let unattributed = 0;
  for (const t of mergeFills(input.mine.filter((t) => t.timestamp >= cutoff))) {
    const id = `M:${t.id || `${t.conditionId}:${t.timestamp}`}`;
    const candidates = buckets.get(`${titleKey(t)}|${t.side}`) ?? [];
    let best: CopyTradeRow | null = null;
    for (const c of candidates) {
      if (c.copied) continue;
      const dt = t.timestamp - c.timestamp;
      if (dt < 0 || dt > matchMs) continue;
      if (!best || t.timestamp - best.timestamp > dt) best = c;
    }
    const lagSec = best ? Math.round((t.timestamp - best.timestamp) / 1000) : null;
    // Slippage is signed against the leader: on a BUY, paying more is worse;
    // on a SELL, receiving less is worse. One number, one direction.
    const slipCents =
      best === null
        ? null
        : Math.round((t.side === "BUY" ? t.price - best.price : best.price - t.price) * 1000) / 10;
    if (best) {
      best.copied = true;
      best.copiedAt = t.timestamp;
      best.mirrorId = id;
      lags.push(lagSec!);
      slips.push(slipCents!);
    } else {
      unattributed += 1;
    }
    myRows.push({
      id,
      kind: "mine",
      mine: true,
      market: t.market,
      conditionId: t.conditionId,
      side: t.side,
      price: t.price,
      size: t.size,
      notional: tradeNotional(t),
      timestamp: t.timestamp,
      outcome: t.outcome ?? null,
      leader: best?.leader ?? null,
      leaderLabel: best?.leaderLabel ?? null,
      lagSec,
      slipCents,
      count: (t as PolymarketTrade & { count?: number }).count ?? 1,
      firstTs: (t as PolymarketTrade & { firstTs?: number }).firstTs ?? t.timestamp,
    });
  }

  // The leader half is widened by `matchMs` above so a fill at the very start
  // of the window can still find its leader trade; the reported feed is the
  // window itself.
  const inWindow = leaderRows.filter((r) => r.timestamp >= cutoff);
  const copied = inWindow.filter((r) => r.copied).length;
  const rows = [...inWindow, ...myRows].sort((a, b) => b.timestamp - a.timestamp);

  return {
    rows,
    summary: {
      mine: myRows.length,
      leader: inWindow.length,
      leaders: new Set(inWindow.map((r) => r.leader)).size,
      copied,
      missed: inWindow.length - copied,
      coverage: inWindow.length ? copied / inWindow.length : 0,
      myNotional: myRows.reduce((s, r) => s + r.notional, 0),
      leaderNotional: inWindow.reduce((s, r) => s + r.notional, 0),
      unattributed,
      medianLagSec: median(lags),
      avgSlipCents: slips.length
        ? Math.round((slips.reduce((s, x) => s + x, 0) / slips.length) * 10) / 10
        : null,
    },
  };
}

function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : Math.round(((s[mid - 1] + s[mid]) / 2) * 10) / 10;
}

/** Per-leader roll-up of a built feed — the "who am I actually keeping up
    with" table. Coverage on its own is a desk-wide average that hides a leader
    whose flow never lands. */
export interface LeaderScore {
  address: string;
  label: string;
  trades: number;
  copied: number;
  coverage: number;
  notional: number;
  myNotional: number;
  medianLagSec: number | null;
}

export function scoreLeaders(rows: CopyTradeRow[]): LeaderScore[] {
  const by = new Map<string, LeaderScore & { lags: number[] }>();
  const get = (address: string, name: string) => {
    let e = by.get(address);
    if (!e) {
      e = { address, label: name, trades: 0, copied: 0, coverage: 0, notional: 0,
        myNotional: 0, medianLagSec: null, lags: [] };
      by.set(address, e);
    }
    return e;
  };
  for (const r of rows) {
    if (!r.leader) continue;
    const e = get(r.leader, r.leaderLabel || shortAddress(r.leader));
    if (r.kind === "leader") {
      e.trades += 1;
      e.notional += r.notional;
      if (r.copied) e.copied += 1;
    } else {
      e.myNotional += r.notional;
      if (r.lagSec != null) e.lags.push(r.lagSec);
    }
  }
  return Array.from(by.values())
    .map(({ lags, ...e }) => ({
      ...e,
      coverage: e.trades ? e.copied / e.trades : 0,
      medianLagSec: median(lags),
    }))
    .sort((a, b) => b.trades - a.trades);
}
