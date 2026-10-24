// THE COPY STUDIO — pick traders, put dollars behind each, see what it would
// have done. Pure: no React, no network.
//
// The console's one job is copy trading, and the question a person actually
// asks while scrolling a leaderboard is "what if I'd copied THIS one with $X?"
// — before committing anything. So the studio replays a trader the moment the
// row is highlighted, and replays the whole allocation every time a dollar
// amount changes.
//
// No new engine: every number here goes through `lib/basketSim.ts`
// (`replaySleeve` → `summarizeSleeve` → `assemblePortfolio`), which is the
// identity strat the desk row, the live engine and the background worker all
// run. One leg = one trader = one session on its OWN capital (sleeves, never
// a pool) — exactly what COPY ALL writes to the copy book.

import {
  assemblePortfolio, equalSplit, replaySleeve, summarizeSleeve, weightedSplit,
  type BasketFeeds, type BasketLeg, type BasketPortfolio, type Sleeve,
} from "./basketSim";
import type { AllocationParams } from "./identityStrat";

/** What a freshly added trader gets when you haven't typed a number. */
export const DEFAULT_LEG_USD = 100;

/** Replay windows the studio offers. Bounded by the 30-day feed the console
    caches for every trader. */
export const STUDIO_WINDOWS = [1, 3, 7, 14, 30] as const;

/** localStorage key for the allocation — it's a plan you build over several
    visits, so it survives a reload (unlike the finder's per-tab picks). */
export const STUDIO_STORAGE_KEY = "poly:copyStudio:v1";

export interface StudioLeg {
  address: string;
  usd: number;
  label?: string | null;
  /** Topic gate the trader was found under ("bitcoin", "nba, nfl"). Carried
      into the replay AND into the copy book on commit, so what you simulated
      is what runs. Empty = every market they trade. */
  marketQuery?: string;
}

export interface StudioState {
  legs: StudioLeg[];
  days: number;
  /** Target bankroll for EQUAL / FIT. Advisory — each leg trades its own $. */
  bankroll: number;
}

export const EMPTY_STUDIO: StudioState = { legs: [], days: 7, bankroll: 1000 };

function money(v: number): number {
  return Math.round(v * 100) / 100;
}

/** A sane dollar amount: finite, ≥0, cents. Anything else is 0. */
export function cleanUsd(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) && n > 0 ? money(n) : 0;
}

export function isAddress(a: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(a.trim());
}

// ── Editing the allocation (immutable) ─────────────────────────────────────

/** Add a trader, or — if already there — leave their amount alone. Adding
    twice must never double a position. */
export function addLeg(legs: StudioLeg[], leg: StudioLeg): StudioLeg[] {
  const address = leg.address.trim().toLowerCase();
  if (!isAddress(address)) return legs;
  if (legs.some((l) => l.address === address)) return legs;
  const usd = cleanUsd(leg.usd) || DEFAULT_LEG_USD;
  return [...legs, { ...leg, address, usd }];
}

export function removeLeg(legs: StudioLeg[], address: string): StudioLeg[] {
  const a = address.toLowerCase();
  return legs.filter((l) => l.address !== a);
}

export function toggleLeg(legs: StudioLeg[], leg: StudioLeg): StudioLeg[] {
  const a = leg.address.toLowerCase();
  return legs.some((l) => l.address === a) ? removeLeg(legs, a) : addLeg(legs, leg);
}

export function setLegUsd(legs: StudioLeg[], address: string, usd: number): StudioLeg[] {
  const a = address.toLowerCase();
  return legs.map((l) => (l.address === a ? { ...l, usd: cleanUsd(usd) } : l));
}

export function totalUsd(legs: StudioLeg[]): number {
  return money(legs.reduce((s, l) => s + cleanUsd(l.usd), 0));
}

/** Each leg's share of the allocated total, 0–1. All-zero → all zero. */
export function weights(legs: StudioLeg[]): Map<string, number> {
  const total = totalUsd(legs);
  return new Map(legs.map((l) => [l.address, total > 0 ? cleanUsd(l.usd) / total : 0]));
}

function toBasket(legs: StudioLeg[]): BasketLeg[] {
  return legs.map((l) => ({
    address: l.address,
    allocationUsd: cleanUsd(l.usd),
    label: l.label ?? null,
    ...(l.marketQuery ? { params: { marketQuery: l.marketQuery } } : {}),
  }));
}

function fromBasket(legs: StudioLeg[], basket: BasketLeg[]): StudioLeg[] {
  return legs.map((l, i) => ({ ...l, usd: basket[i].allocationUsd }));
}

/** Same amount behind every trader, summing to `bankroll`. */
export function splitEqual(legs: StudioLeg[], bankroll: number): StudioLeg[] {
  return fromBasket(legs, equalSplit(toBasket(legs), cleanUsd(bankroll)));
}

/** Keep the proportions you chose, rescale them to `bankroll` (conviction
    survives a deposit). Falls back to equal when nothing is set. */
export function fitToBankroll(legs: StudioLeg[], bankroll: number): StudioLeg[] {
  return fromBasket(legs, weightedSplit(toBasket(legs), cleanUsd(bankroll)));
}

/** Weight by the replay itself: legs that made money get capital in
    proportion to their return, legs that lost or never traded get $0 (they
    stay in the list so you can see them). Nothing positive → equal split. */
export function splitByResult(
  legs: StudioLeg[],
  bankroll: number,
  sleeves: Map<string, Sleeve>,
): StudioLeg[] {
  const total = cleanUsd(bankroll);
  const score = (l: StudioLeg) => {
    const s = sleeves.get(l.address);
    return s && s.trades > 0 && s.pct > 0 ? s.pct : 0;
  };
  const sum = legs.reduce((acc, l) => acc + score(l), 0);
  if (sum <= 0) return splitEqual(legs, total);
  return legs.map((l) => ({ ...l, usd: money((score(l) / sum) * total) }));
}

// ── Simulation ─────────────────────────────────────────────────────────────

export function legParams(leg: StudioLeg): AllocationParams | undefined {
  return leg.marketQuery ? { marketQuery: leg.marketQuery } : undefined;
}

/** Copy ONE trader with `leg.usd` over the last `days`. Null-safe: a trader
    whose feed hasn't landed yet comes back as an honest empty sleeve with a
    note, never a $0 that reads as break-even. */
export function simulateTrader(leg: StudioLeg, feeds: BasketFeeds, days: number): Sleeve {
  const [basket] = toBasket([leg]);
  return summarizeSleeve(basket, replaySleeve(basket, feeds, { days }), basket.allocationUsd);
}

export interface StudioRun {
  sleeves: Sleeve[];
  portfolio: BasketPortfolio;
}

/** The whole allocation: every leg on its own capital, summed. Legs at $0
    are skipped (they'd contribute nothing and dilute nothing). */
export function simulateAllocation(legs: StudioLeg[], feeds: BasketFeeds, days: number): StudioRun {
  const active = toBasket(legs).filter((l) => l.allocationUsd > 0);
  const total = active.reduce((s, l) => s + l.allocationUsd, 0);
  const sleeves = active.map((leg) => summarizeSleeve(leg, replaySleeve(leg, feeds, { days }), total));
  return { sleeves, portfolio: assemblePortfolio(sleeves) };
}

/** Cache key for one trader's preview — everything that changes the answer. */
export function previewKey(leg: StudioLeg, days: number): string {
  return `${leg.address.toLowerCase()}|${cleanUsd(leg.usd)}|${leg.marketQuery ?? ""}|${days}`;
}

// ── Words ──────────────────────────────────────────────────────────────────

export function fmtUsd(v: number, signed = false): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const body = abs >= 10_000
    ? `$${(abs / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`
    : `$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
  return `${sign}${body}`;
}

export function fmtPct(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export type Verdict = "up" | "down" | "flat" | "idle" | "loading";

/** One plain sentence on a sleeve — what a person reads before the numbers. */
export function verdict(s: Sleeve | null | undefined, days: number): { tone: Verdict; text: string } {
  if (!s) return { tone: "loading", text: "replaying…" };
  if (s.trades === 0) {
    return { tone: "idle", text: s.note ? `never traded — ${s.note}` : `no copyable trades in ${days}d` };
  }
  const tone: Verdict = s.net > 0.005 ? "up" : s.net < -0.005 ? "down" : "flat";
  const marked = s.confidence < 0.6 ? " · mostly marked, not settled" : "";
  return {
    tone,
    text: `${fmtUsd(s.net, true)} (${fmtPct(s.pct)}) on ${fmtUsd(s.allocationUsd)} · ${s.trades} copied trade${s.trades === 1 ? "" : "s"} in ${days}d${marked}`,
  };
}

// ── Persistence ────────────────────────────────────────────────────────────

/** Parse whatever was stored — tolerant of junk, old shapes and hand edits. */
export function parseStudio(raw: string | null | undefined): StudioState {
  if (!raw) return EMPTY_STUDIO;
  try {
    const v = JSON.parse(raw) as Partial<StudioState>;
    let legs: StudioLeg[] = [];
    for (const l of Array.isArray(v.legs) ? v.legs : []) {
      if (l && typeof l.address === "string") {
        legs = addLeg(legs, {
          address: l.address,
          usd: cleanUsd(l.usd) || DEFAULT_LEG_USD,
          label: typeof l.label === "string" ? l.label : null,
          marketQuery: typeof l.marketQuery === "string" ? l.marketQuery : "",
        });
      }
    }
    const days = (STUDIO_WINDOWS as readonly number[]).includes(Number(v.days)) ? Number(v.days) : EMPTY_STUDIO.days;
    const bankroll = cleanUsd(v.bankroll) || EMPTY_STUDIO.bankroll;
    return { legs, days, bankroll };
  } catch {
    return EMPTY_STUDIO;
  }
}
