"use client";

// Per-TRADER backtest scores, merged from the two places the worker already
// posts them:
//
//   1. AUTO COPY (`/api/hub/autocopy`) — the top-PnL roster, each trader
//      replayed over a train window and then a disjoint TEST window. The test
//      number is the honest one (out-of-sample), and the verdict compares the
//      two. Covers the roster only (top N).
//   2. THE HUB (`/api/hub?days=1`) — every `copy-<addr>` identity strat the
//      worker replays for the COPY DESK's leaders. Single window, walk-forward
//      verdict when present.
//
// Nothing here computes anything: both endpoints serve the worker's disk
// cache, so a read costs one JSON file each. Auto-copy wins a collision —
// its train/test split is the stronger claim about the same trader.
//
// Both routes are owner-gated; a viewer without the token gets nulls and the
// cards quietly show "—", same as the money tiles do.

import { getAccessToken } from "./access";
import type { ForwardVerdict, HoldoutCheck, HubBacktest } from "./hubReplay";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export interface TraderBacktestScore {
  /** Net PnL of the replay ($), costs modeled as the live engine's. */
  pnl: number;
  /** pnl as % of the paper capital the replay started with. */
  roi: number;
  trades: number;
  /** Paper capital the replay ran on — the denominator behind `roi`. */
  capital: number;
  /** Window replayed, in days. For auto-copy this is the TEST window. */
  days: number;
  at: number;
  verdict?: ForwardVerdict;
  holdout?: HoldoutCheck;
  /** Roster traders whose history hadn't synced when this ran — the replay
      treated them as idle, so the number is incomplete, not flat. */
  warming?: number;
  source: "autocopy" | "hub";
}

/** One compact style per walk-forward verdict — shared by the board cards and
    the strat rows so HELD is the same green everywhere. */
export const VERDICT_TEXT: Record<ForwardVerdict, { label: string; cls: string; hint: string }> = {
  held: { label: "HELD", cls: "text-green-400", hint: "Profitable in the earlier window AND the later one — confirmed out-of-sample." },
  faded: { label: "FADED", cls: "text-red-400", hint: "Made money earlier, lost it in the later window — the look of a one-good-run trader." },
  recovered: { label: "RECOVERED", cls: "text-cyan-400", hint: "Lost earlier, made money in the later window." },
  "no-edge": { label: "NO EDGE", cls: "text-red-400/80", hint: "Lost in both windows." },
  untested: { label: "UNTESTED", cls: "text-pixel-gray", hint: "No trades in the earlier window — nothing to confirm." },
  stalled: { label: "STALLED", cls: "text-amber-300", hint: "Profitable earlier, then stopped trading." },
  idle: { label: "IDLE", cls: "text-pixel-gray", hint: "No trades in either window." },
};

function authHeaders(): Record<string, string> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

interface AutoCopyCard {
  address: string;
  train: HubBacktest;
  test: HubBacktest;
  verdict: ForwardVerdict;
  at: number;
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { headers: authHeaders(), cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function fromHub(bt: HubBacktest, source: TraderBacktestScore["source"]): TraderBacktestScore {
  return {
    pnl: bt.pnl,
    roi: bt.roi,
    trades: bt.trades,
    capital: bt.capital,
    days: bt.days,
    at: bt.at,
    verdict: bt.forward?.verdict,
    holdout: bt.holdout,
    warming: bt.warming,
    source,
  };
}

/** The worker's latest backtest per trader address (lowercased key).
    null when BOTH sources failed — callers keep whatever map they had, the
    same rule the money tiles use for a transient error. */
export async function fetchTraderBacktestScores(): Promise<Map<string, TraderBacktestScore> | null> {
  const [auto, hub] = await Promise.all([
    getJson<{ cards?: AutoCopyCard[] }>(`${BASE}/api/hub/autocopy`),
    getJson<{ results?: Record<string, HubBacktest> }>(`${BASE}/api/hub?days=1`),
  ]);
  if (!auto && !hub) return null;

  const out = new Map<string, TraderBacktestScore>();

  // Hub identity strats first — the copy-desk leaders the worker replays as
  // `copy-<addr without 0x>`. Anything else in the results (saved strats,
  // tpl:* templates) is keyed by strat, not trader, and is skipped here.
  for (const [key, bt] of Object.entries(hub?.results ?? {})) {
    const hex = key.startsWith("copy-") ? key.slice(5) : null;
    if (!hex || !/^[0-9a-f]{40}$/.test(hex)) continue;
    out.set(`0x${hex}`, fromHub(bt, "hub"));
  }

  // Auto-copy cards override: same trader, but a real train/test split. The
  // TEST window is the number a card should post — it's the out-of-sample one.
  for (const card of auto?.cards ?? []) {
    const addr = card.address.toLowerCase();
    out.set(addr, { ...fromHub(card.test, "autocopy"), verdict: card.verdict, at: card.at });
  }

  return out;
}
