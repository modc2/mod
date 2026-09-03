// The custom SCORE formula — one definition, shared by every leaderboard.
//
// The /traders board (CopyTrading) and the copy desk's ADD A TRADER panel
// (FindTraders) rank on the same user-editable expression, persisted under one
// sessionStorage key so a formula written on either surface follows the user
// to the other. The default is PARAMETERIZED as a preset row (ROI, win rate,
// exit÷entry, Sharpe) — a preset is nothing more than a named formula, so
// picking one and hand-editing it are the same mechanism. Out of the box the
// score IS the ROI: what a copied dollar would have returned, which is the
// number a copy desk actually ranks on.

import type { TopTrader } from "./polymarket";

/** The variables a formula can use, in the order they're passed in. */
export const FORMULA_VARS = ["sharpe", "pnl", "volume", "buyVolume", "sellVolume", "positions", "winRate", "markets", "exitEntry"] as const;

/** Named formulas the SCORE can be parameterized with — the first is the
    default. A preset is just a formula string, so an edited preset degrades
    gracefully into a custom score.

    `poolSort` is the SERVER sort the CUSTOM SCORE pool is pulled under —
    usually the preset's own key, but a formula the server can't rank
    (pnl/buyVolume) names its closest server-side proxy instead, and the
    client re-ranks the pool exactly. */
export const SCORE_PRESETS = [
  {
    key: "roi",
    label: "ROI",
    formula: "100 * pnl / volume",
    poolSort: "roi",
    hint: "P&L per $100 traded over the window — return on turnover. Ranks the trader compounding a real edge on $5k above the whale who ground out 3% on a million. — = no volume yet.",
  },
  {
    key: "pnlBuy",
    label: "P&L/BUY",
    formula: "100 * pnl / buyVolume",
    poolSort: "roi",
    hint: "P&L per $100 put INTO positions — return on the capital deployed. ROI's denominator counts sells too, so a trader who buys and exits looks half as sharp there; this divides by buys alone, which is closer to what copying their entries costs you. — = no buys yet.",
  },
  {
    key: "winRate",
    label: "WIN RATE",
    formula: "winRate",
    poolSort: "winRate",
    hint: "Of the positions this trader bought that have a known outcome, the % that resolved their way. -1 = nothing decided yet.",
  },
  {
    key: "exitEntry",
    label: "EXIT/ENTRY",
    formula: "exitEntry",
    poolSort: "exitEntry",
    hint: "Average exit price over entry price across closed trades — 1.00 is break-even, 1.15 means they got out 15% above what they put in. -1 = no closed trades.",
  },
  {
    key: "sharpe",
    label: "SHARPE",
    formula: "sharpe",
    poolSort: "sharpe",
    hint: "Mean per-trade return ÷ its stdev over the window. Consistency, not size: it ranks the trader compounding a real edge above the whale who risked millions for 3%.",
  },
] as const;

export const DEFAULT_FORMULA: string = SCORE_PRESETS[0].formula;

/** The preset a formula IS, or null when it's a hand-written expression. */
export function matchScorePreset(formula: string): (typeof SCORE_PRESETS)[number] | null {
  const f = formula.trim();
  return SCORE_PRESETS.find((p) => p.formula === f) ?? null;
}

/** The server sort key the CUSTOM SCORE pool should be pulled under for this
    formula — the preset's `poolSort`, or Sharpe for a hand-written one. */
export function scorePoolSortKey(formula: string): string {
  return matchScorePreset(formula)?.poolSort ?? "sharpe";
}

/** What `scorePoolSortKey` orders by, as a label for the "your formula ranks
    the top N by …" note. */
export function scorePoolSortLabel(formula: string): string {
  const key = scorePoolSortKey(formula);
  return SCORE_PRESETS.find((p) => p.key === key)?.label ?? "Sharpe";
}

// Formulas persisted under the OLD key can be the old implicit defaults
// ("pnl / volume", then "sharpe") — the save-effect writes on mount, so those
// are not a user choice; treat them as unset and adopt the new default. Real
// custom expressions migrate. The new key exists so that explicitly picking
// the SHARPE preset today survives a reload instead of being mistaken for
// the old implicit default.
export const FORMULA_STORAGE_KEY = "poly8bit_score_formula_v2";
const LEGACY_STORAGE_KEY = "poly8bit_score_formula";
const LEGACY_IMPLICIT_DEFAULTS = ["pnl / volume", "sharpe"];
// "winRate" was the v2 default until ROI took over — and the save-effect
// wrote it on mount, so a stored "winRate" is overwhelmingly the old implicit
// default rather than a deliberate pick. Same treatment as the legacy ones.
const V2_IMPLICIT_DEFAULT = "winRate";

export interface ScoreInputs {
  sharpe: number;
  pnl: number;
  volume: number;
  buyVolume: number;
  sellVolume: number;
  positions: number;
  winRate: number;
  markets: number;
  exitEntry: number;
}

export function scoreInputs(t: TopTrader): ScoreInputs {
  return {
    sharpe: t.sharpe,
    pnl: t.pnl,
    volume: t.volume,
    buyVolume: t.buyVolume,
    sellVolume: t.sellVolume,
    positions: t.positions,
    winRate: t.winRate,
    markets: t.marketTitles.length,
    exitEntry: t.exitEntry,
  };
}

export function compileFormula(expr: string): {
  fn: (t: ScoreInputs) => number;
  error: null;
} | { fn: null; error: string } {
  try {
    const raw = new Function(
      ...FORMULA_VARS, "Math",
      `"use strict"; return (${expr});`,
    ) as (...args: unknown[]) => unknown;
    const probe = raw(...FORMULA_VARS.map(() => 0), Math);
    if (typeof probe !== "number" && !Number.isNaN(probe)) {
      return { fn: null, error: "formula must evaluate to a number" };
    }
    return {
      fn: (t) => {
        try {
          const v = raw(...FORMULA_VARS.map((k) => t[k]), Math) as number;
          return Number.isFinite(v) ? v : Number.NEGATIVE_INFINITY;
        } catch {
          return Number.NEGATIVE_INFINITY;
        }
      },
      error: null,
    };
  } catch (e) {
    return { fn: null, error: e instanceof Error ? e.message : String(e) };
  }
}

/** True when a preset score IS its metric's -1 "unknown" sentinel for this
    trader. winRate and exitEntry use -1 for "nothing decided / no closed
    trades"; fed straight through formatScore that prints as a red "-1.00",
    which reads as a terrible number instead of no data. Only presets are
    checked — a hand-written formula can legitimately evaluate to -1. */
export function scoreIsUnknown(formula: string, t: ScoreInputs): boolean {
  const preset = matchScorePreset(formula);
  if (!preset) return false;
  return (preset.key === "winRate" && t.winRate < 0)
    || (preset.key === "exitEntry" && t.exitEntry < 0)
    // The ratio presets divide by dollars — none traded means no ratio,
    // not a 0% one.
    || (preset.key === "roi" && t.volume <= 0)
    || (preset.key === "pnlBuy" && t.buyVolume <= 0);
}

// Scores are unit-less numbers (a win-rate percentage, an exit/entry ratio,
// a Sharpe), so small magnitudes render as plain signed decimals — a Sharpe
// of 1.52 must read "+1.52", not "+152.00%".
export function formatScore(v: number): string {
  if (!Number.isFinite(v)) return "---";
  const abs = Math.abs(v);
  const prefix = v >= 0 ? "+" : "-";
  if (abs >= 1_000_000) return `${prefix}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${prefix}${(abs / 1_000).toFixed(2)}k`;
  return `${prefix}${abs.toFixed(2)}`;
}

// ── Saved ratios — user-named formulas that render as preset chips ──
//
// A builtin preset is a named formula the console ships; a saved ratio is a
// named formula the USER shipped. Both are chips, both are just strings you
// can keep editing. Saved ratios persist in localStorage (the origin is
// shared by every module — writes stay tiny and never throw) and follow the
// user across the /traders board and the copy desk like the formula does.

export interface SavedRatio {
  name: string;
  formula: string;
}

export const RATIOS_STORAGE_KEY = "poly8bit_score_ratios";
const MAX_RATIOS = 24;

/** Seeded on first run only — deleting it stays deleted (the key persists
    as `[]`). One example makes the SAVE flow discoverable. */
const SEED_RATIOS: SavedRatio[] = [{ name: "PNL/VOL", formula: "pnl / volume" }];

function sanitizeRatios(raw: unknown): SavedRatio[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: SavedRatio[] = [];
  for (const r of raw) {
    if (typeof r !== "object" || r === null) continue;
    const name = String((r as SavedRatio).name ?? "").trim().slice(0, 24);
    const formula = String((r as SavedRatio).formula ?? "").trim();
    if (!name || !formula || seen.has(name.toUpperCase())) continue;
    seen.add(name.toUpperCase());
    out.push({ name, formula });
    if (out.length >= MAX_RATIOS) break;
  }
  return out;
}

/** Every ratio the user has saved. Absent key → the seed (and nothing else
    is written until the user acts). */
export function loadSavedRatios(): SavedRatio[] {
  try {
    const raw = localStorage.getItem(RATIOS_STORAGE_KEY);
    if (raw === null) return SEED_RATIOS;
    return sanitizeRatios(JSON.parse(raw));
  } catch {
    return SEED_RATIOS;
  }
}

function persistRatios(list: SavedRatio[]): void {
  // Best-effort: the shared modc2 origin can be quota-full — the chips still
  // work for this session, they just won't survive a reload.
  try { localStorage.setItem(RATIOS_STORAGE_KEY, JSON.stringify(list)); } catch {}
}

/** Add (or rename-over) a ratio; returns the new list. */
export function addSavedRatio(list: SavedRatio[], name: string, formula: string): SavedRatio[] {
  const next = sanitizeRatios([
    { name, formula },
    ...list.filter((r) => r.name.toUpperCase() !== name.trim().toUpperCase()),
  ]);
  persistRatios(next);
  return next;
}

/** Drop a ratio by name; returns the new list. */
export function removeSavedRatio(list: SavedRatio[], name: string): SavedRatio[] {
  const next = list.filter((r) => r.name !== name);
  persistRatios(next);
  return next;
}

/** The saved ratio a formula IS, or null. */
export function matchSavedRatio(formula: string, list: SavedRatio[]): SavedRatio | null {
  const f = formula.trim();
  return list.find((r) => r.formula === f) ?? null;
}

/** "pnl / volume" → "PNL/VOL" — the suggested chip name for a formula. */
export function suggestRatioName(formula: string): string {
  return formula
    .replace(/\s+/g, "")
    .replace(/Math\./g, "")
    .toUpperCase()
    .replace(/VOLUME/g, "VOL")
    .replace(/POSITIONS/g, "POS")
    .replace(/WINRATE/g, "WIN")
    .slice(0, 16);
}

/** The saved formula, or the default when unset/legacy. */
export function loadSavedFormula(): string {
  try {
    const saved = sessionStorage.getItem(FORMULA_STORAGE_KEY);
    if (saved && saved.trim() && saved.trim() !== V2_IMPLICIT_DEFAULT) return saved;
    const legacy = sessionStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy && legacy.trim() && !LEGACY_IMPLICIT_DEFAULTS.includes(legacy.trim())) return legacy;
  } catch {}
  return DEFAULT_FORMULA;
}

export function saveFormula(formula: string): void {
  try { sessionStorage.setItem(FORMULA_STORAGE_KEY, formula); } catch {}
}
