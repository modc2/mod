// The custom SCORE formula — one definition, shared by every leaderboard.
//
// The /traders board (CopyTrading) and the copy desk's ADD A TRADER panel
// (FindTraders) rank on the same user-editable expression, persisted under one
// sessionStorage key so a formula written on either surface follows the user
// to the other. The default is PARAMETERIZED as a preset row (win rate,
// exit÷entry, Sharpe) — a preset is nothing more than a named formula, so
// picking one and hand-editing it are the same mechanism. Out of the box the
// score IS the win rate.

import type { TopTrader } from "./polymarket";

/** The variables a formula can use, in the order they're passed in. */
export const FORMULA_VARS = ["sharpe", "pnl", "volume", "positions", "winRate", "markets", "exitEntry"] as const;

/** Named formulas the SCORE can be parameterized with — the first is the
    default. A preset is just a formula string, so an edited preset degrades
    gracefully into a custom score. */
export const SCORE_PRESETS = [
  {
    key: "winRate",
    label: "WIN RATE",
    formula: "winRate",
    hint: "Of the positions this trader bought that have a known outcome, the % that resolved their way. -1 = nothing decided yet.",
  },
  {
    key: "exitEntry",
    label: "EXIT/ENTRY",
    formula: "exitEntry",
    hint: "Average exit price over entry price across closed trades — 1.00 is break-even, 1.15 means they got out 15% above what they put in. -1 = no closed trades.",
  },
  {
    key: "sharpe",
    label: "SHARPE",
    formula: "sharpe",
    hint: "Mean per-trade return ÷ its stdev over the window. Consistency, not size: it ranks the trader compounding a real edge above the whale who risked millions for 3%.",
  },
] as const;

export const DEFAULT_FORMULA: string = SCORE_PRESETS[0].formula;

/** The preset a formula IS, or null when it's a hand-written expression. */
export function matchScorePreset(formula: string): (typeof SCORE_PRESETS)[number] | null {
  const f = formula.trim();
  return SCORE_PRESETS.find((p) => p.formula === f) ?? null;
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

export interface ScoreInputs {
  sharpe: number;
  pnl: number;
  volume: number;
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
    if (saved && saved.trim()) return saved;
    const legacy = sessionStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy && legacy.trim() && !LEGACY_IMPLICIT_DEFAULTS.includes(legacy.trim())) return legacy;
  } catch {}
  return DEFAULT_FORMULA;
}

export function saveFormula(formula: string): void {
  try { sessionStorage.setItem(FORMULA_STORAGE_KEY, formula); } catch {}
}
