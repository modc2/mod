// The editable surface of a strat — what an agent is allowed to change, and
// what "changed" means.
//
// STRAT CHAT lets you say "stop buying longshots" and have an agent turn that
// into `tradeFilters.minPrice = 0.6`. The agent is a language model, so the
// only safe contract is a narrow one: it may emit a patch of KNOWN keys with
// KNOWN types inside KNOWN ranges, and everything else is dropped with a
// reason. This file is that contract, and it is deliberately pure and shared:
//
//   • the server route (api/strat-chat) validates the model's patch with it,
//     so a bad patch never reaches the strat store;
//   • the browser validates again before applying, so a compromised route
//     can't write a field the UI doesn't know how to show;
//   • the same specs generate the prompt's parameter reference, so the agent
//     is told about exactly the fields it is allowed to touch — one list, not
//     a prompt that drifts from the validator.
//
// The watchlist is NOT here. Adding or removing traders is a different, larger
// action (it changes what the strat IS, not how it behaves), and it belongs to
// the roster UI where you can see who you're copying.

import type { SavedIndex, SizingModel, TradeFilters, TraderFilter, MomentumParams } from "./types";

/** One editable parameter: where it lives, what it accepts, and — for the
    agent — what it actually does. `describe` is prompt text, so it reads like
    documentation rather than a type. */
export interface ParamSpec {
  /** Dotted path into SavedIndex — "maxTrade", "tradeFilters.minPrice",
      "momentum.minRiseCents". Depth 2 max; only the containers below exist. */
  path: string;
  kind: "number" | "int" | "string" | "boolean" | "enum" | "stringArray";
  /** Inclusive bounds for numbers. */
  min?: number;
  max?: number;
  values?: readonly string[];
  /** Value that means "unset this" — null clears the field entirely. */
  nullable?: boolean;
  describe: string;
}

/** Containers a path may address. Anything else is rejected outright, which is
    what keeps a patch from reaching, say, `traders` or `owner`. */
const CONTAINERS = ["tradeFilters", "filter", "momentum"] as const;
type Container = (typeof CONTAINERS)[number];

export const PARAM_SPECS: readonly ParamSpec[] = [
  // ── Sizing and capital ──
  { path: "capital", kind: "number", min: 1, max: 1_000_000,
    describe: "Allocated capital in USD. Position sizes are derived from this, so halving it halves every order." },
  { path: "minTrade", kind: "number", min: 1, max: 10_000,
    describe: "Smallest order in USD. Below Polymarket's floor (max($1, 5 shares × price)) the order can't be placed at all." },
  { path: "maxTrade", kind: "number", min: 1, max: 100_000,
    describe: "Largest order in USD — the per-trade ceiling." },
  { path: "maxOpenPositions", kind: "int", min: 1, max: 100,
    describe: "How many DIFFERENT markets may be held at once. A BUY that would open one more than this is skipped; topping up a hold always passes." },
  { path: "maxPerCycle", kind: "int", min: 1, max: 20,
    describe: "Orders per engine cycle. The per-cycle candidates are ranked by expected edge and only this many win." },
  { path: "sizing", kind: "enum", values: ["bankroll", "flow"],
    describe: "What a mirror is sized proportionally TO. 'bankroll' copies the leader's RISK (their stake as a share of net worth) and needs capital in their league; 'flow' copies their CONVICTION (our allocation split across the capital they deployed) and places real orders at small sizes." },
  { path: "turnover", kind: "number", min: 0.1, max: 20,
    describe: "'flow' sizing only: how many times the allocation may be deployed across one window of leader flow." },
  { path: "maxUpscale", kind: "number", min: 0, max: 50, nullable: true,
    describe: "How far a mirror may be upsized past its proportional notional to clear the order floor. 2 = never place more than 2× what proportionality asked for; smaller intents are skipped as SUB_SCALE." },

  // ── Protective exits ──
  { path: "stopLoss", kind: "number", min: 0, max: 1,
    describe: "Fraction of entry price to defend, 0-1. 0.8 = sell the whole position once the market decays to 80% of entry (a 20% loss). 0 turns the stop off — the position then rides to resolution." },
  { path: "takeProfit", kind: "number", min: 0, max: 1,
    describe: "Absolute price level, 0-1, at which a hold is fully liquidated. 0.99 = sell anything that runs to the top tick. 0 turns it off." },

  // ── Gates ──
  { path: "marketQuery", kind: "string",
    describe: "Free-text market-topic filter, e.g. 'bitcoin'. Only markets whose title matches are traded. Empty string = every market." },
  { path: "minMinutesToClose", kind: "number", min: 0, max: 20_160,
    describe: "Refuse to ENTER a market resolving sooner than this many minutes. 60 excludes the sub-hour candle games; 0 turns the gate off. Exits are never gated." },
  { path: "maxTradeAgeSec", kind: "number", min: 0, max: 86_400, nullable: true,
    describe: "Refuse to mirror a leader trade older than this many seconds — after a fetch outage a backlog would otherwise enter at prices the leader never paid. 0 or null = off." },
  { path: "livePollMinutes", kind: "number", min: 0.5, max: 60,
    describe: "Live engine scan cadence in minutes. The engine clamps anything under 0.5 up to the 30s rate-limit floor." },

  // ── Per-trade filters ──
  { path: "tradeFilters.sides", kind: "enum", values: ["both", "buy", "sell"],
    describe: "Which sides of the leader's flow to mirror. 'buy' = entries only." },
  { path: "tradeFilters.minPrice", kind: "number", min: 0, max: 1,
    describe: "Skip leader trades priced below this (0-1). 0.6 keeps likely winners and drops longshots." },
  { path: "tradeFilters.maxPrice", kind: "number", min: 0, max: 1,
    describe: "Skip leader trades priced above this (0-1)." },
  { path: "tradeFilters.minNotional", kind: "number", min: 0, max: 1_000_000,
    describe: "Skip leader trades smaller than this many dollars — a size floor on THEIR trade, not ours." },
  { path: "tradeFilters.maxNotional", kind: "number", min: 0, max: 10_000_000,
    describe: "Skip leader trades larger than this many dollars." },
  { path: "tradeFilters.categories", kind: "stringArray",
    describe: "Market categories to keep: politics, sports, crypto, btc, pop-culture, business, science, tech, ai. 'btc' is bitcoin-only (including the 5-minute Up/Down candles); 'crypto' is every coin. Empty = all." },

  // ── Trader-quality gate ──
  { path: "filter.topN", kind: "int", min: 1, max: 100,
    describe: "Rank the watchlist every cycle and copy only the top N traders by the chosen metric." },
  // The four TraderMetric values and nothing else — "pnl"/"volume" were
  // accepted here but silently fall through to "score" in `traderScore`,
  // so the agent could report a rank change that never happened.
  { path: "filter.metric", kind: "enum", values: ["score", "sharpe", "roi", "winRate"],
    describe: "What the trader FILTER ranks by. 'score' = P(win)×ROI, 'sharpe' = ROI/stdev (consistency)." },
  { path: "filter.maxStaleHours", kind: "number", min: 0.25, max: 720,
    describe: "Freshness gate: drop any watched trader whose last trade in this strat's markets is older than this many hours. Stale traders sort below every active one, so they never hold a top-N slot." },

  // ── Momentum origination ──
  { path: "momentum.lookbackMinutes", kind: "number", min: 1, max: 1440,
    describe: "Window the price move is measured over. The entry needs a price point at or before the window's start, so a short lookback on a young market simply refuses to fire." },
  { path: "momentum.minRiseCents", kind: "number", min: 1, max: 50,
    describe: "Cents the outcome must have risen over the lookback to be bought. Higher = fewer, stronger signals." },
  { path: "momentum.exitDropCents", kind: "number", min: 1, max: 50,
    describe: "Cents a held outcome must FALL over the lookback before it's sold. Defaults to minRiseCents." },
  { path: "momentum.confirmMinutes", kind: "number", min: 0, max: 1440,
    describe: "ENTRIES only: the rise must still be intact over the last this-many minutes — the outcome may not have given ground back inside that window. Catches the move that already peaked, which the two-point lookback can't see. 0 = off. Keep it well under lookbackMinutes; at or above it the gate stops meaning anything." },
  { path: "momentum.query", kind: "string",
    describe: "What markets momentum tracks. Comma-separated groups are SEARCHED SEPARATELY and merged, so 'bitcoin, ethereum, solana' covers three assets. Always comma-separate a multi-asset query: one string with all three words is ranked as a phrase and returns markets that mention all three coins instead of each coin's own markets. Up to 6 groups. Empty = fall back to the strat's marketQuery, else 'bitcoin'." },
  { path: "momentum.minPrice", kind: "number", min: 0.01, max: 0.99,
    describe: "Entry price floor (0-1). The default 0.5 means momentum only rides favorites." },
  { path: "momentum.maxPrice", kind: "number", min: 0.01, max: 0.99,
    describe: "Entry price ceiling (0-1). Above it there's no move left to ride." },
  { path: "momentum.maxPositions", kind: "int", min: 1, max: 20,
    describe: "Simultaneous positions momentum may hold. Also the sizing divisor: each entry is capital/maxPositions, clamped into the trade band." },
  { path: "momentum.minMinutesToClose", kind: "number", min: 0, max: 1440,
    describe: "Momentum's own time-to-close floor for ENTRIES. A candle strat must set this low (1) or every sub-hour entry is vetoed." },
  { path: "momentum.maxMarkets", kind: "int", min: 1, max: 60,
    describe: "Search-mode only: how many top-volume matching markets to track." },
];

const SPEC_BY_PATH = new Map(PARAM_SPECS.map((s) => [s.path, s]));

/** A validated change: what to set, and what it was. */
export interface PatchEntry {
  path: string;
  from: unknown;
  to: unknown;
}

export interface PatchResult {
  /** Changes that passed validation AND actually change something. */
  entries: PatchEntry[];
  /** Human-readable reasons for everything dropped — shown in the chat so a
      rejected suggestion is visible rather than silently swallowed. */
  rejected: string[];
}

function readPath(idx: SavedIndex, path: string): unknown {
  const [head, tail] = path.split(".");
  const root = (idx as unknown as Record<string, unknown>)[head];
  if (tail === undefined) return root;
  if (!root || typeof root !== "object") return undefined;
  return (root as Record<string, unknown>)[tail];
}

/** Coerce + range-check one value against its spec. Returns the value to
    store, or a rejection string. */
function coerce(spec: ParamSpec, raw: unknown): { value: unknown } | { reject: string } {
  if (raw === null) {
    if (spec.nullable) return { value: null };
    return { reject: `${spec.path}: null is not allowed for this parameter` };
  }
  switch (spec.kind) {
    case "number":
    case "int": {
      const n = typeof raw === "string" ? Number(raw) : raw;
      if (typeof n !== "number" || !Number.isFinite(n)) {
        return { reject: `${spec.path}: expected a number, got ${JSON.stringify(raw)}` };
      }
      const v = spec.kind === "int" ? Math.round(n) : n;
      if (spec.min !== undefined && v < spec.min) {
        return { reject: `${spec.path}: ${v} is below the ${spec.min} minimum` };
      }
      if (spec.max !== undefined && v > spec.max) {
        return { reject: `${spec.path}: ${v} is above the ${spec.max} maximum` };
      }
      return { value: v };
    }
    case "boolean":
      if (typeof raw !== "boolean") return { reject: `${spec.path}: expected true or false` };
      return { value: raw };
    case "string":
      if (typeof raw !== "string") return { reject: `${spec.path}: expected a string` };
      if (raw.length > 200) return { reject: `${spec.path}: string too long (max 200 chars)` };
      return { value: raw };
    case "enum": {
      if (typeof raw !== "string" || !spec.values?.includes(raw)) {
        return { reject: `${spec.path}: must be one of ${spec.values?.join(", ")}` };
      }
      return { value: raw };
    }
    case "stringArray": {
      if (!Array.isArray(raw) || raw.some((x) => typeof x !== "string")) {
        return { reject: `${spec.path}: expected an array of strings` };
      }
      if (raw.length > 20) return { reject: `${spec.path}: too many entries (max 20)` };
      return { value: raw as string[] };
    }
  }
}

/** Same value? Arrays compare by content — a patch that re-sends the current
    categories list shouldn't read as a change. */
function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => x === b[i]);
  }
  return a === b;
}

/** Validate a model-proposed patch against the current strat.
 *
 *  Everything unknown, mistyped, out of range, or unchanged is dropped with a
 *  stated reason. The caller applies `entries` and shows `rejected` — an agent
 *  that hallucinates a parameter should be visibly corrected, not obeyed. */
export function validatePatch(idx: SavedIndex, patch: unknown): PatchResult {
  const entries: PatchEntry[] = [];
  const rejected: string[] = [];
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return { entries, rejected };

  for (const [path, raw] of Object.entries(patch as Record<string, unknown>)) {
    const spec = SPEC_BY_PATH.get(path);
    if (!spec) {
      rejected.push(`${path}: not an editable strat parameter`);
      continue;
    }
    const head = path.split(".")[0];
    if (path.includes(".") && !CONTAINERS.includes(head as Container)) {
      rejected.push(`${path}: unknown parameter group`);
      continue;
    }
    const got = coerce(spec, raw);
    if ("reject" in got) { rejected.push(got.reject); continue; }
    const from = readPath(idx, path);
    if (same(from, got.value)) continue; // no-op, not a change
    entries.push({ path, from, to: got.value });
  }
  return { entries, rejected };
}

/** Apply validated entries, returning a NEW strat (never mutates the input —
    the caller decides whether to persist). */
export function applyPatch(idx: SavedIndex, entries: PatchEntry[]): SavedIndex {
  const next: SavedIndex = {
    ...idx,
    tradeFilters: idx.tradeFilters ? { ...idx.tradeFilters } : undefined,
    filter: idx.filter ? { ...idx.filter } : undefined,
    momentum: idx.momentum ? { ...idx.momentum } : undefined,
  };
  for (const e of entries) {
    const [head, tail] = e.path.split(".");
    if (tail === undefined) {
      if (e.to === null) delete (next as unknown as Record<string, unknown>)[head];
      else (next as unknown as Record<string, unknown>)[head] = e.to;
      continue;
    }
    const bag = ((next as unknown as Record<string, unknown>)[head] ?? {}) as Record<string, unknown>;
    if (e.to === null) delete bag[tail];
    else bag[tail] = e.to;
    (next as unknown as Record<string, unknown>)[head] = bag;
  }
  next.updatedAt = Date.now();
  return next;
}

/** "maxTrade: 25 → 10" — one line per change, for the diff card. */
export function describeEntry(e: PatchEntry): string {
  const show = (v: unknown) =>
    v === undefined ? "unset" : v === null ? "cleared" : Array.isArray(v) ? `[${v.join(", ")}]` : String(v);
  return `${e.path}: ${show(e.from)} → ${show(e.to)}`;
}

// ── Prompt material ────────────────────────────────────────────────
// Both of these are read by api/strat-chat to build the agent's context. They
// live here so the reference the agent gets and the validator it's checked
// against can never describe different parameters.

/** The parameter reference, as the agent sees it. */
export function paramReference(): string {
  return PARAM_SPECS.map((s) => {
    const range = s.kind === "enum"
      ? `one of ${s.values?.join(" | ")}`
      : s.kind === "stringArray"
        ? "array of strings"
        : s.min !== undefined || s.max !== undefined
          ? `${s.kind}, ${s.min ?? "-∞"}..${s.max ?? "∞"}`
          : s.kind;
    return `- ${s.path} (${range})${s.nullable ? " [nullable]" : ""}: ${s.describe}`;
  }).join("\n");
}

/** The strat's current settings, in the same paths the patch uses. */
export function currentSettings(idx: SavedIndex): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const s of PARAM_SPECS) {
    const v = readPath(idx, s.path);
    if (v !== undefined) out[s.path] = v;
  }
  return out;
}
