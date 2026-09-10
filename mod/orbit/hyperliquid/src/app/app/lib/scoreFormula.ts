// The custom SCORE — a user-written ranking over the top-traders board.
//
// The score box accepts three shapes, detected from the text itself:
//   expr — plain arithmetic over the variables ("100 * pnl / volume")
//   js   — a real JS function: a body that `return`s, an arrow, or a named
//          `function score(t) {…}`. Multi-line, if/else, the lot.
//   py   — a real Python function (`def score(pnl, volume): …`), run in the
//          browser via Pyodide (see pyScore.ts) — nothing is sent anywhere.
// A FUNCTION does double duty: return a NUMBER and that's the trader's score;
// return null / None / False and the trader is FILTERED off the board.
//
// This is the same contract as polymarket's score box (lib/scoreFormula.ts
// there) with the variable set swapped for Hyperliquid's TopTrader row.

import { MIN_SHARPE_DAYS, type TopTrader } from "./api";

/** The variables a formula can use, in the order they're passed in. */
export const FORMULA_VARS = [
  "roi", "pnl", "volume", "equity",
  "winRate", "winRateLo", "closes", "wins", "losses", "trades",
  "sharpe", "sharpeDays", "profitFactor", "worstClose", "fees",
  "avgTrade", "hoursSince", "measured", "days",
] as const;

/** What each variable IS, in one line — rendered beside the formula box.
    A formula language with no vocabulary printed next to it is a guessing
    game. The leaderboard-priced four (roi/pnl/volume/equity) exist on EVERY
    row; the rest are fill-measured and only real when `measured` is 1. */
export const SCORE_VAR_HINTS: Record<(typeof FORMULA_VARS)[number], string> = {
  roi: "Window return on equity, PERCENT — Hyperliquid's own leaderboard figure. Priced for every row.",
  pnl: "Window PnL in USD, net of fees. Priced for every row.",
  volume: "Notional traded in the window, USD. Priced for every row.",
  equity: "Current account value, USD — what roi is a return on. Priced for every row.",
  winRate: "Net-of-fee win rate over realised closes, 0–100. -1 = not measured.",
  winRateLo: "Wilson 95% lower bound on winRate — the rate the sample size can defend. Rank on this, not winRate. -1 = not measured.",
  closes: "Fills that realised PnL — the actual win-rate denominator. 0 on unmeasured rows.",
  wins: "Winning closes in the window (net of fees).",
  losses: "Losing closes in the window (net of fees).",
  trades: "Every fill in the window, opens included — NOT the win-rate denominator.",
  sharpe: "Annualised daily-PnL Sharpe. Noise under 7 days of history — gate on sharpeDays.",
  sharpeDays: "Days of history behind sharpe. Under " + MIN_SHARPE_DAYS + " the ratio is noise.",
  profitFactor: "Σ wins ÷ |Σ losses|, net of fees. -1 = no losing close (undefined, not bad).",
  worstClose: "Worst single realised close in the window, USD (negative).",
  fees: "Σ fees paid in the window, USD — what the venue took from the gross edge.",
  avgTrade: "Average fill size, USD. 0 on unmeasured rows.",
  hoursSince: "Hours since the last fill we saw. -1 = unknown (row not measured; the board's liveness gate still means ≤24h).",
  measured: "1 when this row's fill stats were actually fetched, 0 when only leaderboard pricing exists. Gate fill-derived math on it.",
  days: "The board's window length in days — normalise with it (pnl / days).",
};

/** Named formulas the score can start from — chips above the box. A preset
    is nothing more than a named formula, so picking one and hand-editing it
    are the same mechanism. */
export const SCORE_PRESETS = [
  {
    key: "roi",
    label: "ROI",
    formula: "roi",
    hint: "Hyperliquid's own window return on equity — the board's default order, as a score.",
  },
  {
    key: "turnover",
    label: "PNL/VOL",
    formula: "100 * pnl / volume",
    hint: "PnL per $100 traded — return on turnover. Ranks a real edge on $5k above a whale grinding 3% on millions. — = no volume.",
  },
  {
    key: "daily",
    label: "PNL/DAY",
    formula: "pnl / days",
    hint: "Dollars made per day of the window — comparable across window lengths.",
  },
  {
    key: "winRate",
    label: "WIN RATE",
    formula: "winRateLo",
    hint: "The win rate each row can DEFEND at its sample size (Wilson 95% lower bound) — a 3-for-3 wallet stops outranking a 180-of-200 one. -1 = not measured.",
  },
  {
    key: "sharpe",
    label: "SHARPE",
    formula: "sharpe",
    hint: "Annualised daily-PnL Sharpe — consistency, not size. Only meaningful with ≥" + MIN_SHARPE_DAYS + " days of history.",
  },
] as const;

/** The preset a formula IS, or null when it's a hand-written score. */
export function matchScorePreset(formula: string): (typeof SCORE_PRESETS)[number] | null {
  const f = formula.trim();
  return SCORE_PRESETS.find((p) => p.formula === f) ?? null;
}

// Persisted like polymarket's: the active formula follows the tab
// (sessionStorage), the named chips follow the user (localStorage — the
// origin is shared by every module, so writes stay tiny and never throw).
export const FORMULA_STORAGE_KEY = "hl.score.formula";
export const RATIOS_STORAGE_KEY = "hl.score.ratios";

export type ScoreLang = "expr" | "js" | "py";

export function detectScoreLang(src: string): ScoreLang {
  if (/(^|\n)\s*def\s+\w+\s*\(/.test(src)) return "py";
  if (/^\s*function\b/.test(src) || /\breturn\b/.test(src) || /=>/.test(src)) return "js";
  return "expr";
}

/** Starter the JS ƒ button drops in — a filter + a score in four lines.
    winRate is a PERCENT (0–100, -1 = unmeasured), not a 0–1 fraction. */
export const JS_FN_TEMPLATE = `// return a number = score · return null = hide the trader
if (equity < 10000) return null; // too small to copy
if (measured && winRateLo < 45) return null; // winRateLo is 0–100, -1 = unmeasured
return 100 * pnl / volume;`;

/** Starter the PY ƒ button drops in. Name any subset of the variables as
    parameters — or take one argument and read it as a dict. */
export const PY_FN_TEMPLATE = `def score(pnl, volume, equity, winRateLo, measured, **rest):
    # return a number = score · return None = hide the trader
    if equity < 10000:
        return None  # too small to copy
    if measured and winRateLo < 45:  # 0-100, -1 = unmeasured
        return None
    return 100 * pnl / volume if volume else None`;

export interface ScoreInputs {
  roi: number; pnl: number; volume: number; equity: number;
  winRate: number; winRateLo: number; closes: number; wins: number;
  losses: number; trades: number; sharpe: number; sharpeDays: number;
  profitFactor: number; worstClose: number; fees: number; avgTrade: number;
  hoursSince: number; measured: number; days: number;
}

export function scoreInputs(t: TopTrader, days: number): ScoreInputs {
  return {
    roi: t.roi ?? 0,
    pnl: t.pnl ?? 0,
    volume: t.volume ?? 0,
    equity: t.account_value ?? 0,
    winRate: t.win_rate ?? -1,
    winRateLo: t.win_rate_lo ?? -1,
    closes: t.closes ?? 0,
    wins: t.wins ?? 0,
    losses: t.losses ?? 0,
    trades: t.trades ?? 0,
    sharpe: t.sharpe ?? 0,
    sharpeDays: t.sharpe_days ?? 0,
    profitFactor: t.profit_factor ?? 0,
    worstClose: t.worst_close ?? 0,
    fees: t.fees ?? 0,
    avgTrade: t.avg_trade_usd ?? 0,
    hoursSince: t.last_active > 0 ? (Date.now() - t.last_active) / 3_600_000 : -1,
    measured: t.win_rate >= 0 ? 1 : 0,
    days,
  };
}

/** Per-variable probe values for the compile-time zero-probe. */
export const PROBE_INPUTS: ScoreInputs = {
  roi: 0, pnl: 0, volume: 0, equity: 0, winRate: 0, winRateLo: 0, closes: 0,
  wins: 0, losses: 0, trades: 0, sharpe: 0, sharpeDays: 0, profitFactor: 0,
  worstClose: 0, fees: 0, avgTrade: 0, hoursSince: 0, measured: 0, days: 0,
};

/** A compiled score: fn returns the trader's score, or NULL when the user's
    FUNCTION filtered the trader out (returned null/None/False). Plain
    expressions never return null — a bad value sinks as -Infinity, so the
    row stays visible with a "---" score instead of silently vanishing. */
export type ScoreFn = (t: ScoreInputs) => number | null;
export interface CompiledScore { fn: ScoreFn | null; error: string | null }

/** null/None/False/undefined → hidden; True → 1 (a pure predicate is a pure
    filter); finite number → the score; anything else → hidden. */
export function normalizeScoreReturn(v: unknown): number | null {
  if (v === null || v === undefined || v === false) return null;
  if (v === true) return 1;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

/** Compile an expr or JS score. Python compiles asynchronously in pyScore.ts —
    this returns a "runtime loading" stub for it so sync callers stay sane. */
export function compileScore(src: string): CompiledScore {
  // Blank = the score is OFF, not an empty expression — `return ();` would
  // throw "Unexpected token ')'" at the user before they've typed anything.
  if (!src.trim()) return { fn: null, error: null };
  const lang = detectScoreLang(src);
  if (lang === "py") return { fn: null, error: null };
  try {
    let raw: (...args: unknown[]) => unknown;
    const named = /^\s*function\s+([A-Za-z_$][\w$]*)/.exec(src);
    if (lang === "js" && named) {
      // `function score(t) {…}` — gets ONE object with every variable.
      const make = new Function(`"use strict"; ${src}; return ${named[1]};`)() as (t: ScoreInputs) => unknown;
      raw = (...args: unknown[]) => make(Object.fromEntries(FORMULA_VARS.map((k, i) => [k, args[i]])) as unknown as ScoreInputs);
    } else if (lang === "js" && /=>/.test(src) && !/\breturn\b/.test(src.split("=>")[0])) {
      // Arrow form: `(t) => t.pnl / t.volume` — same one-object contract.
      const make = new Function(`"use strict"; return (${src});`)() as (t: ScoreInputs) => unknown;
      raw = (...args: unknown[]) => make(Object.fromEntries(FORMULA_VARS.map((k, i) => [k, args[i]])) as unknown as ScoreInputs);
    } else if (lang === "js") {
      // Function BODY — every variable is in scope, `return` your score.
      raw = new Function(...FORMULA_VARS, "Math", `"use strict"; ${src}`) as (...args: unknown[]) => unknown;
    } else {
      raw = new Function(...FORMULA_VARS, "Math", `"use strict"; return (${src});`) as (...args: unknown[]) => unknown;
    }
    const probe = raw(...FORMULA_VARS.map((k) => PROBE_INPUTS[k]), Math);
    if (lang === "expr" && typeof probe !== "number" && !Number.isNaN(probe)) {
      return { fn: null, error: "formula must evaluate to a number" };
    }
    return {
      fn: (t) => {
        try {
          const v = raw(...FORMULA_VARS.map((k) => t[k]), Math);
          if (lang === "js") return normalizeScoreReturn(v);
          return typeof v === "number" && Number.isFinite(v) ? v : Number.NEGATIVE_INFINITY;
        } catch {
          // Expressions sink (visible, "---"); a function that THROWS on a
          // trader treats them like a null — filtered, same as its contract.
          return lang === "js" ? null : Number.NEGATIVE_INFINITY;
        }
      },
      error: null,
    };
  } catch (e) {
    return { fn: null, error: e instanceof Error ? e.message : String(e) };
  }
}

/** True when a preset score IS its metric's "unknown" sentinel for this
    trader — printed as "—" instead of a red "-1.00" that reads as a terrible
    number. Only presets are checked: a hand-written formula can legitimately
    evaluate to -1. */
export function scoreIsUnknown(formula: string, t: ScoreInputs): boolean {
  const preset = matchScorePreset(formula);
  if (!preset) return false;
  return (preset.key === "winRate" && t.winRateLo < 0)
    || (preset.key === "sharpe" && (!t.measured || t.sharpeDays < MIN_SHARPE_DAYS))
    // The turnover ratio divides by dollars — none traded means no ratio,
    // not a 0% one.
    || (preset.key === "turnover" && t.volume <= 0);
}

// Scores are unit-less numbers (a percent, a ratio, a Sharpe), so small
// magnitudes render as plain signed decimals — a Sharpe of 1.52 must read
// "+1.52", not "+152.00%".
export function formatScore(v: number): string {
  if (!Number.isFinite(v)) return "---";
  const abs = Math.abs(v);
  const prefix = v >= 0 ? "+" : "-";
  if (abs >= 1_000_000) return `${prefix}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${prefix}${(abs / 1_000).toFixed(2)}k`;
  return `${prefix}${abs.toFixed(2)}`;
}

// ── Saved ratios — user-named formulas that render as chips ──

export interface SavedRatio {
  name: string;
  formula: string;
}

const MAX_RATIOS = 24;

/** Seeded on first run only — deleting it stays deleted (the key persists
    as `[]`). One example makes the SAVE flow discoverable — and shows the
    sentinel-gating idiom presets can't. */
const SEED_RATIOS: SavedRatio[] = [
  { name: "EDGE", formula: "measured ? sharpe * winRateLo / 100 : -1e9" },
];

function sanitizeRatios(raw: unknown): SavedRatio[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: SavedRatio[] = [];
  for (const r of raw) {
    if (typeof r !== "object" || r === null) continue;
    const name = String((r as SavedRatio).name ?? "").trim().slice(0, 24);
    // Functions are formulas too — cap them so one saved chip can't eat the
    // shared localStorage origin.
    const formula = String((r as SavedRatio).formula ?? "").trim().slice(0, 4000);
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

/** "pnl / volume" → "PNL/VOL" — the suggested chip name for a formula.
    Functions don't compress into a readable token soup, so they suggest a
    language-stamped name instead. */
export function suggestRatioName(formula: string): string {
  const lang = detectScoreLang(formula);
  if (lang !== "expr") return lang === "py" ? "PY SCORE" : "JS SCORE";
  return formula
    .replace(/\s+/g, "")
    .replace(/Math\./g, "")
    .toUpperCase()
    .replace(/VOLUME/g, "VOL")
    .replace(/EQUITY/g, "EQ")
    .replace(/WINRATELO/g, "WINLO")
    .replace(/WINRATE/g, "WIN")
    .slice(0, 16);
}

/** The saved formula. Empty string = the score is OFF — the board ranks on
    its server metrics until the user writes or picks one. */
export function loadSavedFormula(): string {
  try {
    const saved = sessionStorage.getItem(FORMULA_STORAGE_KEY);
    if (saved !== null) return saved;
  } catch {}
  return "";
}

export function saveFormula(formula: string): void {
  try { sessionStorage.setItem(FORMULA_STORAGE_KEY, formula); } catch {}
}
