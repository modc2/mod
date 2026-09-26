// A DRAFTED STRAT, and what the console does with one.
//
// Two screens produce a param set nobody has saved yet — the lab's agent
// verdict (ADOPT) and the VIBE box (SAVE) — and both mean the same human act:
// take exactly what the bench replayed and write it down, paused. One write
// path, one set of result types, so the two can never drift into disagreeing
// about what "saving a candidate" does.

import { saveIndex } from "./indexStore";
import type { IndexTrader, SavedIndex } from "./types";

/** One replay window off the lab bench — the fields the console reads. */
export interface BenchWindow {
  days: number;
  pnl: number;
  roi: number;
  trades: number;
  fees?: number;
  note?: string;
  forward?: { verdict?: string };
  settlement?: { unverified_usd?: number };
  /** Why the leaders' flow did or didn't become trades in this window. */
  funnel?: {
    observed?: number;
    copied?: number;
    blocked_by_filters?: number;
    outranked?: number;
    unplaceable?: number;
    reasons?: Record<string, number>;
  };
}

export interface BenchResult {
  candidate: { name: string; traders: number; capital: number };
  windows: BenchWindow[];
  /** Traders whose feed wasn't cached yet — these numbers are a floor and a
      retest in a few minutes sees real ones. */
  warming: string[];
}

/** The answer to one VIBE press: what the words became, and how it did. */
export interface VibeResult {
  note?: string;
  params?: Record<string, unknown>;
  dropped?: string[];
  bench?: BenchResult;
  benchError?: string;
  error?: string;
}

export const money = (n: number) => `${n < 0 ? "-" : "+"}$${Math.abs(n).toFixed(0)}`;

/** The box holds either plain words (→ describe) or a params object (→ test
    it as written). JSON is the disambiguator, same spirit as the top bar. */
export function parseParams(text: string): Record<string, unknown> | null {
  const t = text.trim();
  if (!t.startsWith("{")) return null;
  try {
    const o = JSON.parse(t) as unknown;
    return o && typeof o === "object" && !Array.isArray(o) ? (o as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** A param set, materialized as a saved strat. Only the shape is enforced
    here — the numbers already survived the bench. */
export function saveParamsAsStrat(p: Record<string, unknown>, name?: string): SavedIndex {
  const traders: IndexTrader[] = (Array.isArray(p.traders) ? p.traders : [])
    .map((t: unknown) => {
      const address = typeof t === "string" ? t : (t as { address?: string })?.address;
      if (typeof address !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(address)) return null;
      const weight = typeof t === "object" && t !== null ? Number((t as { weight?: number }).weight) : 1;
      return { address: address.toLowerCase(), weight: weight > 0 ? weight : 1 };
    })
    .filter((t): t is IndexTrader => t !== null);
  const now = Date.now();
  const idx: SavedIndex = {
    ...(p as Partial<SavedIndex>),
    id: now.toString(36),
    name: (name || (typeof p.name === "string" ? p.name : "") || "Lab strat").slice(0, 64),
    traders,
    liveEnabled: false, // saving is never starting
    createdAt: now,
    updatedAt: now,
  } as SavedIndex;
  saveIndex(idx);
  window.dispatchEvent(new Event("strat-updated"));
  return idx;
}
