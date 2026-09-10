// AUTO STRAT — the console's strat FACTORY: a headless `claude` agent that
// invents a copy-index recipe, proves it on the lab bench, and REGISTERS it —
// both as a SavedIndex the sidebar adopts and as a runnable Python Strat class
// (MCP server included) in the user-strats store.
//
// Two triggers, one pipeline:
//
//   the loop     every `intervalSecs` (default 60s) while `enabled` — the
//                same settings-file-owns-the-toggle pattern as AUTO COPY, so
//                the switch survives restarts and the console and MCP flip
//                the SAME switch.
//   the click    RANDOM NEW STRAT in the sidebar → one run right now.
//
// One run = one no-tool model turn (the agent reads a live leaderboard
// snapshot embedded in the prompt — it never invents addresses) → the lab
// bench over [1,3,7] days → a pass/fail verdict → registration. The strat the
// agent designs is DATA, not code: the params JSON is compiled into a
// generated subclass of the canonical Python Strat by a fixed template, so a
// mis-prompted model can name bad traders but can never write code this
// module executes.
//
// Runs that PASS the bar are auto-adopted by the sidebar; a clicked run is
// adopted either way (the human asked for it). Everything lands paused —
// liveEnabled is never set here, same rule as the lab's ADOPT.

import { mkdirSync, readFileSync } from "fs";
import { join } from "path";

import { API_BASE } from "../polymarket";
import { paramReference } from "../stratPatch";
import type { IndexTrader, SavedIndex } from "../types";
import { digJson, runClaude } from "./agentCli";
import { writeAtomic } from "./feedStore";
import { candidateBacktest } from "./lab";
import { mintOwnerToken, ownerAddress, stateDir } from "./ownerToken";

/** Drafting a recipe is translation work, not the lab's judgment work — and
    this can fire every minute. Sonnet by default; the deployment can raise it. */
const MODEL = process.env.POLYMARKET_AUTOSTRAT_MODEL || "claude-sonnet-5";
const DRAFT_TIMEOUT_MS = 90_000;
const WINDOWS = [1, 3, 7];
const MAX_RUNS_KEPT = 50;
const MIN_INTERVAL_SECS = 60;
/** Where the generated Python classes register — the same store uploaded
    `<id>/mod.py` strats live in (api/src/user_strats.rs). */
const USER_STRATS_DIR = "polymarket-user-strats";

// ── settings + registry (on disk, so restarts and MCP see one truth) ──

export interface AutoStratSettings {
  enabled: boolean;
  intervalSecs: number;
}

export interface AutoStratRun {
  id: string;
  at: number;
  origin: "loop" | "click";
  theme: string;
  status: "running" | "done" | "failed";
  error?: string;
  /** The agent's one-line read of what it built and why. */
  note?: string;
  strat?: SavedIndex;
  /** Compact bench windows, verbatim from candidateBacktest. */
  windows?: Array<Record<string, unknown>>;
  warming?: string[];
  /** Cleared the auto-adopt bar (net-positive on ≥2 windows, enough trades). */
  passed?: boolean;
  /** Where the generated Python class + MCP server landed. */
  classPath?: string;
  model?: string;
}

function dir(): string {
  const d = join(stateDir(), "autostrat");
  mkdirSync(d, { recursive: true });
  return d;
}
const settingsPath = () => join(dir(), "settings.json");
const runsPath = () => join(dir(), "runs.json");

export function readSettings(): AutoStratSettings {
  try {
    const raw = JSON.parse(readFileSync(settingsPath(), "utf8")) as Partial<AutoStratSettings>;
    return {
      enabled: raw.enabled === true,
      intervalSecs: Math.max(Number(raw.intervalSecs) || MIN_INTERVAL_SECS, MIN_INTERVAL_SECS),
    };
  } catch {
    // Off until a human flips it: the loop spends real inference every minute.
    return { enabled: false, intervalSecs: MIN_INTERVAL_SECS };
  }
}

export function writeSettings(patch: Partial<AutoStratSettings>): AutoStratSettings {
  const next = { ...readSettings(), ...patch };
  next.intervalSecs = Math.max(Number(next.intervalSecs) || MIN_INTERVAL_SECS, MIN_INTERVAL_SECS);
  writeAtomic(settingsPath(), JSON.stringify(next));
  return next;
}

export function listRuns(): AutoStratRun[] {
  try {
    const raw = JSON.parse(readFileSync(runsPath(), "utf8"));
    return Array.isArray(raw) ? (raw as AutoStratRun[]) : [];
  } catch {
    return [];
  }
}

function saveRun(run: AutoStratRun): void {
  const runs = listRuns().filter((r) => r.id !== run.id);
  runs.unshift(run);
  writeAtomic(runsPath(), JSON.stringify(runs.slice(0, MAX_RUNS_KEPT)));
}

// ── the pipeline ────────────────────────────────────────────────

/** One run at a time: a run holds a bench slot and a model call, and the
    1-minute loop must stack skips, not processes. */
let active: AutoStratRun | null = null;
export const autoStratActive = () => active;

/** Each run rolls a lens so a 1-minute cadence explores the space instead of
    re-deriving the same top-of-board index sixty times an hour. */
const THEMES = [
  "concentrated: 3-4 of the strongest traders, equal weight",
  "diversified: 7-8 traders across different score profiles",
  "consistency: highest sharpe names, ignore raw pnl",
  "hit rate: highest winRate with enough decided positions to trust it",
  "contrarian value: gate entries to prices under 35 cents",
  "favorites only: gate entries to prices over 60 cents",
  "selective: few trades — raise minTrade, cap maxPerCycle at 1-2",
  "fast money: shorter holds — takeProfit and stopLoss set tight",
  "deep bench: weight traders by conviction, not equally",
  "wildcard: pick mid-board traders the obvious indexes skip",
];

interface BoardRow {
  address: string;
  pnl?: number;
  volume?: number;
  winRate?: number;
  sharpe?: number;
  exitEntry?: number;
  positions?: number;
  decidedPositions?: number;
}

/** The warm leaderboard, same paged read pm_top_traders does — answers from
    cache or reports cold; a cold board fails the run rather than blocking a
    minute-cadence loop on a minutes-long aggregation. */
async function boardSnapshot(): Promise<BoardRow[]> {
  const token = mintOwnerToken();
  if (!token) throw new Error("no owner/secret on this deployment — cannot read the leaderboard");
  const qs = new URLSearchParams({
    days: "7", pool: "2000", paged: "1", pageSize: "40", page: "0",
    sort: "sharpe", order: "desc", maxLastTradeHrs: "6",
  });
  const res = await fetch(`${API_BASE}/active-traders?${qs}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`leaderboard read failed: HTTP ${res.status}`);
  const body = (await res.json()) as { cold?: boolean; traders?: BoardRow[] };
  if (body.cold || !Array.isArray(body.traders) || body.traders.length === 0) {
    throw new Error("leaderboard cache is cold — the sync loop hasn't warmed it yet; retry in a few minutes");
  }
  return body.traders;
}

function shuffled<T>(rows: T[]): T[] {
  const a = [...rows];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const num = (v: number | undefined, d = 0) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "?");

function buildPrompt(theme: string, board: BoardRow[]): string {
  const rows = shuffled(board).slice(0, 30).map((t) =>
    `${t.address}  pnl7d=$${num(t.pnl)}  winRate=${t.winRate === -1 ? "?" : num((t.winRate ?? 0) * 100)}%` +
    `(${t.decidedPositions ?? 0} decided)  sharpe=${num(t.sharpe, 2)}  vol=$${num(t.volume)}`);
  return [
    `You design ONE new copy-index strategy for a self-hosted Polymarket copy-trading console. A copy index watches trader wallets and mirrors their entries, sized to the owner's capital; parameters gate WHICH fills get copied and how positions exit. Your output is a recipe that gets backtested and registered automatically — you place no orders and run no tools.`,
    ``,
    `TODAY'S LENS (follow it — it is what makes this run different from the last one): ${theme}`,
    ``,
    `THE BOARD — live 7-day leaderboard, active in the last 6h. Pick traders ONLY from these addresses, verbatim; any other address invalidates the run:`,
    ...rows,
    ``,
    `PARAMS you may set (nested JSON, same paths; set only what the lens implies — defaults exist for everything else):`,
    paramReference(),
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"name": "<short evocative name, <=40 chars>", "note": "<one sentence: the thesis and why these traders>", "traders": [{"address": "0x…", "weight": 1}], "params": { any other knobs }}`,
  ].join("\n");
}

/** Auto-adopt bar — looser than the lab's confidence bar on purpose: a
    1-minute factory feeds the sidebar with plausible, net-positive recipes and
    the full STRAT LAB remains the place for held-walk-forward conviction. The
    bench numbers ride along either way, so a passing card can still be judged. */
function passedBar(windows: Array<Record<string, unknown>>): boolean {
  const positive = windows.filter((w) => Number(w.pnl) > 0).length;
  const trades = windows.reduce((s, w) => s + (Number(w.trades) || 0), 0);
  return windows.length >= 2 && positive >= 2 && trades >= 5;
}

export function triggerAutoStrat(origin: "loop" | "click", theme?: string): AutoStratRun {
  if (active) throw new Error(`a run is already going (${active.id}) — one at a time`);
  const run: AutoStratRun = {
    id: `auto_${Date.now().toString(36)}`,
    at: Date.now(),
    origin,
    theme: (theme || "").trim() || THEMES[Math.floor(Math.random() * THEMES.length)],
    status: "running",
    model: MODEL,
  };
  active = run;
  saveRun(run);
  void executeRun(run)
    .catch((e) => {
      run.status = "failed";
      run.error = (e instanceof Error ? e.message : String(e)).slice(0, 400);
    })
    .finally(() => {
      if (run.status === "running") run.status = "failed";
      saveRun(run);
      active = null;
    });
  return run;
}

async function executeRun(run: AutoStratRun): Promise<void> {
  const board = await boardSnapshot();
  const valid = new Set(board.map((t) => t.address.toLowerCase()));

  const draft = await runClaude(buildPrompt(run.theme, board), MODEL, {
    timeoutMs: DRAFT_TIMEOUT_MS,
    extraArgs: ["--restricted", "--tools", ""],
  });
  if (draft.ok === false) throw new Error(draft.error);

  const parsed = digJson(draft.text, (o) => {
    if (!Array.isArray(o.traders) || o.traders.length === 0) return null;
    return {
      name: typeof o.name === "string" ? o.name.trim().slice(0, 40) : "",
      note: typeof o.note === "string" ? o.note.slice(0, 300) : "",
      traders: o.traders as Array<{ address?: string; weight?: number }>,
      params: (o.params && typeof o.params === "object" && !Array.isArray(o.params)
        ? o.params : {}) as Record<string, unknown>,
    };
  });
  if (!parsed) throw new Error("the agent answered, but not with a recipe object");

  // The board is the whitelist — a hallucinated address dies here, silently
  // for the row, fatally for the run if nothing survives.
  const traders: IndexTrader[] = parsed.traders
    .map((t) => ({
      address: String(t.address || "").toLowerCase(),
      weight: Number(t.weight) > 0 ? Number(t.weight) : 1,
    }))
    .filter((t) => valid.has(t.address))
    .slice(0, 10);
  if (traders.length === 0) throw new Error("every trader the agent named was off the board — recipe rejected");

  const candidate = { ...parsed.params, name: parsed.name || "auto strat", traders };
  const bench = await candidateBacktest(candidate, WINDOWS);
  run.note = parsed.note;
  run.windows = bench.windows;
  run.warming = bench.warming;
  run.passed = passedBar(bench.windows);

  const now = Date.now();
  const strat: SavedIndex = {
    ...(parsed.params as Partial<SavedIndex>),
    id: run.id,
    name: candidate.name,
    traders,
    liveEnabled: false, // registering is saving, never starting — lab's rule
    createdAt: now,
    updatedAt: now,
  } as SavedIndex;
  if (!(Number(strat.capital) > 0)) strat.capital = 1000;
  run.strat = strat;
  run.classPath = emitStratClass(run, strat);
  run.status = "done";
}

// ── registration as a runnable Python class + MCP server ────────

/** The generated file is a fixed template around the recipe JSON: the agent
    authored data, the template authors code. It subclasses the module's
    canonical Strat (src/strats/base/mod.py) and serves itself over MCP via
    src/strats/mcp_compat.py when run directly. */
function emitStratClass(run: AutoStratRun, strat: SavedIndex): string {
  const stratDir = join(stateDir(), USER_STRATS_DIR, run.id);
  mkdirSync(stratDir, { recursive: true });
  const recipe = JSON.stringify(strat, null, 2).replace(/'''/g, "\\u0027\\u0027\\u0027");
  const src = [
    `#!/usr/bin/env python3`,
    `# ${strat.name} — generated ${new Date(run.at).toISOString()} by AUTO STRAT (${run.origin}).`,
    `# Lens: ${run.theme.replace(/\n/g, " ")}`,
    `# ${run.note ? run.note.replace(/\n/g, " ") : "no note"}`,
    `#`,
    `# A registered copy-index recipe compiled into the module's canonical Strat`,
    `# class. Run it directly and it IS an MCP server (stdio):`,
    `#     python3 mod.py     # tools: strat_info / strat_signal / strat_backtest / strat_state`,
    `import json`,
    `import os`,
    `import sys`,
    ``,
    `SRC = os.environ.get("POLYMARKET_SRC") or "/root/mod/mod/orbit/polymarket/src"`,
    `sys.path.insert(0, SRC)`,
    ``,
    `from strats import CopyTrader, StratConfig  # noqa: E402`,
    `from strats.mcp_compat import serve  # noqa: E402`,
    ``,
    `RECIPE = json.loads(r'''${recipe}''')`,
    ``,
    ``,
    `class AutoStrat(CopyTrader):`,
    `    """${strat.name} (auto-generated). The recipe's trade gates, applied`,
    `    through the canonical _should_mirror hook."""`,
    ``,
    `    def _should_mirror(self, trade):`,
    `        f = RECIPE.get("tradeFilters") or {}`,
    `        lo = f.get("priceMin", f.get("minPrice"))`,
    `        hi = f.get("priceMax", f.get("maxPrice"))`,
    `        if lo is not None and trade.price < float(lo):`,
    `            return False`,
    `        if hi is not None and trade.price > float(hi):`,
    `            return False`,
    `        sides = f.get("sides")`,
    `        if sides and trade.side.value not in [str(s).upper() for s in sides]:`,
    `            return False`,
    `        return True`,
    ``,
    ``,
    `def build(capital=None):`,
    `    return AutoStrat(StratConfig(`,
    `        name=RECIPE.get("name") or "auto strat",`,
    `        capital=float(capital or RECIPE.get("capital") or 1000),`,
    `        watchlist=[{"address": t["address"], "weight": t.get("weight", 1)}`,
    `                   for t in RECIPE.get("traders") or []],`,
    `        min_order_size=float(RECIPE.get("minTrade") or 1),`,
    `        max_order_size=float(RECIPE.get("maxTrade") or 100),`,
    `        params=RECIPE,`,
    `    ))`,
    ``,
    ``,
    `if __name__ == "__main__":`,
    `    serve(build, name=${JSON.stringify(`polymarket-strat-${run.id}`)},`,
    `          description=${JSON.stringify(`${strat.name} — auto-generated copy-index strat`)})`,
    ``,
  ].join("\n");
  writeAtomic(join(stratDir, "mod.py"), src);
  writeAtomic(join(stratDir, "meta.json"), JSON.stringify({
    owner: ownerAddress() || "",
    title: strat.name,
    description: `AUTO STRAT (${run.origin}) — ${run.note || run.theme}`,
    public: false,
    createdAt: Math.floor(run.at / 1000),
    forks: 0,
  }, null, 2));
  return join(stratDir, "mod.py");
}

// ── the loop ────────────────────────────────────────────────────

let ticker: ReturnType<typeof setInterval> | null = null;
let lastFiredAt = 0;

/** Checks cheap state every 15s and fires when due — reading settings each
    tick means the console's toggle and interval apply without a restart, and
    an in-flight run makes a due tick a skip, never a second process. */
export function startAutoStratLoop(): void {
  if (ticker) return;
  ticker = setInterval(() => {
    const s = readSettings();
    if (!s.enabled || active) return;
    if (Date.now() - lastFiredAt < s.intervalSecs * 1000) return;
    lastFiredAt = Date.now();
    try {
      triggerAutoStrat("loop");
    } catch {
      // raced a click-started run — the next tick tries again
    }
  }, 15_000);
  ticker.unref?.();
}
