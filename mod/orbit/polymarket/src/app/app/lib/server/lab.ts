// STRAT LAB — the console's research agent, and the experiment bench it runs on.
//
// Two halves:
//
//   candidateBacktest()   replay an ARBITRARY parameter set (not a saved strat)
//                         over the worker's cached trader feeds, with the same
//                         engine and walk-forward check every hub card uses.
//                         Nothing is published: a lab experiment never touches
//                         backtests.json, the manifest, or the user's strats.
//
//   startLabRun()         spawn a headless `claude` agent whose only tools are
//                         this module's own read-only MCP tools plus the bench
//                         above. It researches traders, designs candidates,
//                         backtests them, and iterates until the DATA clears a
//                         stated confidence bar — or it says plainly that it
//                         doesn't. The run streams to disk; the console and MCP
//                         read it back. The agent cannot place an order, start
//                         a session, or edit a strat: adopting its verdict is a
//                         human action in the sidebar.
//
// Same trust model as strat-chat: local `claude` CLI (no API key of its own),
// owner-gated route, and the sandbox is the tool list — `--restricted
// --tools ""` + `--strict-mcp-config` + an explicit allowlist.

import { spawn } from "child_process";
import {
  closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync,
} from "fs";
import { join, resolve as resolvePath } from "path";

import {
  HUB_WINDOWS, backtestOne, type HubBacktest, type TraderFeed,
} from "../hubReplay";
import { WORKER_TAPE_BUDGET } from "../momentumTape";
import { paramReference } from "../stratPatch";
import type { IndexTrader, SavedIndex } from "../types";
import { AGENT_MODEL, CLAUDE_BIN, claudeEnv, digJson, runClaude } from "./agentCli";
import { feedSession, refreshRoster } from "./feedFetcher";
import { writeAtomic } from "./feedStore";
import { stateDir } from "./ownerToken";
import { resolutionsFor } from "./resolutionStore";

/** Opus by default — same reasoning as strat-chat: this is judgment work. */
const MODEL = process.env.POLYMARKET_LAB_MODEL || "claude-opus-5";
/** Enough turns to research + run a dozen experiments; a hard stop past it. */
const MAX_TURNS = Number(process.env.POLYMARKET_LAB_MAX_TURNS) || 80;
/** A candidate replay is real CPU inside the Next server; the agent calls
    them one at a time, so anything past a small overlap is a bug or abuse. */
const MAX_CONCURRENT_BENCH = 2;
/** Candidates are experiments, not portfolios. */
const MAX_TRADERS = 15;
const MAX_WINDOWS = 3;

// ── The bench: backtest a candidate ─────────────────────────────

export interface CandidateResult {
  candidate: { name: string; traders: number; capital: number };
  windows: Array<Record<string, unknown>>;
  /** Traders the feed store had nothing for — replayed as silent. A fetch for
      them was queued; a retest in a few minutes sees real numbers. */
  warming: string[];
}

let benchInFlight = 0;

function normalizeCandidate(raw: Record<string, unknown>): SavedIndex {
  const list = Array.isArray(raw.traders) ? raw.traders : [];
  const traders: IndexTrader[] = [];
  for (const t of list.slice(0, MAX_TRADERS)) {
    if (typeof t === "string" && /^0x[0-9a-fA-F]{40}$/.test(t)) {
      traders.push({ address: t.toLowerCase(), weight: 1 });
    } else if (t && typeof t === "object" && typeof (t as { address?: unknown }).address === "string") {
      const o = t as { address: string; weight?: number };
      if (/^0x[0-9a-fA-F]{40}$/.test(o.address)) {
        traders.push({ address: o.address.toLowerCase(), weight: Number(o.weight) > 0 ? Number(o.weight) : 1 });
      }
    }
  }
  const idx = {
    ...(raw as Partial<SavedIndex>),
    id: `lab:${Date.now().toString(36)}`,
    name: typeof raw.name === "string" && raw.name.trim() ? raw.name.trim().slice(0, 64) : "lab candidate",
    traders,
  } as SavedIndex;
  if (!(Number(idx.capital) > 0)) idx.capital = 1000;
  return idx;
}

function compactWindow(bt: HubBacktest, days: number): Record<string, unknown> {
  const f = bt.forward as unknown as (Record<string, unknown> | undefined);
  const fun = bt.funnel as unknown as (Record<string, unknown> | undefined);
  const s = bt.settlement as unknown as (Record<string, unknown> | undefined);
  return {
    days,
    pnl: bt.pnl, roi: bt.roi, trades: bt.trades, capital: bt.capital,
    fees: bt.fees, fee_bps_of_volume: bt.feeBps,
    note: bt.note,
    funnel: fun ? {
      observed: fun.observed, copied: fun.executed, blocked_by_filters: fun.gated,
      outranked: fun.outranked, unplaceable: fun.skipped, reasons: fun.reasons,
    } : undefined,
    // Walk-forward: the window BEFORE this one, replayed blind. Only the
    // verdict "held" means the edge existed then AND now.
    forward: f ? {
      verdict: f.verdict, confirmed: f.ok,
      prior_pnl: f.pnl, prior_roi: f.roi, prior_trades: f.trades,
    } : undefined,
    settlement: s ? {
      resolved_positions: s.resolved, unverified_positions: s.marked,
      unverified_usd: s.markedUsd,
    } : undefined,
    tape: bt.tape,
  };
}

export async function candidateBacktest(
  raw: Record<string, unknown>,
  askedWindows: unknown,
): Promise<CandidateResult> {
  if (benchInFlight >= MAX_CONCURRENT_BENCH) {
    throw new Error("lab bench busy — a candidate replay is already running; retry in ~1 minute");
  }
  const idx = normalizeCandidate(raw);
  if (idx.traders.length === 0 && !idx.momentum) {
    throw new Error("candidate needs `traders` (0x… addresses) or `momentum` params — it has neither, so there is nothing to replay");
  }
  const windows = (Array.isArray(askedWindows) ? askedWindows : [1])
    .map(Number)
    .filter((d) => HUB_WINDOWS.includes(d))
    .slice(0, MAX_WINDOWS);
  if (windows.length === 0) windows.push(1);
  windows.sort((a, b) => a - b);

  benchInFlight++;
  try {
    const feeds = new Map<string, Promise<TraderFeed>>();
    const session = feedSession();
    let budget = 200;
    const resolveLegs = async (conditionIds: string[]) => {
      const { resolved, stats } = await resolutionsFor(conditionIds, { budget });
      budget -= stats.fetched;
      return resolved;
    };
    const out: Array<Record<string, unknown>> = [];
    for (const days of windows) {
      const bt = await backtestOne(idx, days, feeds, session.load, resolveLegs, {
        forward: true, tapeBudget: WORKER_TAPE_BUDGET,
      });
      if (bt) out.push(compactWindow(bt, days));
    }
    // What the bench lacked is the fetch loop's next job — the agent's retest
    // in a few minutes runs over real history instead of silence.
    if (session.pending.size > 0) {
      void refreshRoster([...session.pending], { maxAgeMs: 0 }).catch(() => {});
    }
    return {
      candidate: { name: idx.name, traders: idx.traders.length, capital: idx.capital ?? 1000 },
      windows: out,
      warming: [...session.pending],
    };
  } finally {
    benchInFlight--;
  }
}

// ── The drafter: plain words → a candidate param set ────────────
//
// The vibe-coding half of the lab: the owner describes a strat in their own
// words and a single no-tool model turn translates it into the exact JSON the
// bench replays. Nothing is saved and nothing is tested here — the draft lands
// back in the console's editor, where TEST (the bench above) and SAVE stay
// human presses. Tool-less on purpose: translation is cheap and fast; research
// belongs to the full lab run.

export interface DraftResult {
  /** One or two sentences: how the words were read, and any judgment calls. */
  note: string;
  /** The candidate, in exactly the shape pm_lab_backtest / the bench accepts. */
  params: Record<string, unknown>;
}

const DRAFT_TIMEOUT_MS = 90_000;

export async function draftCandidate(ask: string): Promise<DraftResult> {
  const prompt = [
    `You translate a plain-language strategy description into the parameter JSON of a Polymarket copy-trading console. Output params only — you have no tools, so NEVER invent trader addresses: only 0x… addresses quoted verbatim in the description may appear in "traders". A description that names no wallets gets an empty traders list (the owner adds them, or the filter block picks them).`,
    ``,
    `HOW A STRATEGY WORKS HERE: a "copy index" watches a list of trader wallets and mirrors their entries, sized proportionally to the owner's capital. Parameters gate WHICH of their fills get copied and how positions exit. An optional \`momentum\` block instead ORIGINATES trades off a market's own price tape (no watchlist needed).`,
    ``,
    `PARAMS SHAPE — name (string), traders (0x… addresses, max ${MAX_TRADERS}), capital (USD, default 1000), plus any of (nested JSON, same paths):`,
    paramReference(),
    ``,
    `THE DESCRIPTION`,
    ask,
    ``,
    `RULES: set only the parameters the description implies — defaults exist for everything else, and an unasked-for knob is noise the owner has to audit. Give the candidate a short name in their words.`,
    ``,
    `Reply with ONE JSON object and nothing else — no prose outside it, no markdown fence:`,
    `{"note": "<one or two sentences: how you read the ask + any judgment call>", "params": { the candidate }}`,
  ].join("\n");

  const run = await runClaude(prompt, AGENT_MODEL, {
    timeoutMs: DRAFT_TIMEOUT_MS,
    extraArgs: ["--restricted", "--tools", ""],
  });
  if (run.ok === false) throw new Error(run.error);

  const parsed = digJson(run.text, (o) => {
    if (!o.params || typeof o.params !== "object" || Array.isArray(o.params)) return null;
    return {
      note: typeof o.note === "string" ? o.note.slice(0, 500) : "",
      params: o.params as Record<string, unknown>,
    };
  });
  if (!parsed) throw new Error("the drafter answered, but not with a params object — try rephrasing");
  return parsed;
}

// ── The agent run ───────────────────────────────────────────────

export interface LabRunMeta {
  id: string;
  goal: string;
  model: string;
  maxTurns: number;
  pid: number;
  startedAt: number;
}

export interface LabStep {
  kind: "tool" | "note";
  tool?: string;
  input?: string;
  text?: string;
}

export interface LabRun extends LabRunMeta {
  status: "running" | "done" | "died";
  steps: LabStep[];
  /** The agent's closing message, verbatim. */
  finalText?: string;
  /** The JSON verdict dug out of it, when it kept the contract. */
  verdict?: Record<string, unknown>;
  turns?: number;
  costUsd?: number;
  error?: string;
}

function runsDir(): string {
  const dir = join(stateDir(), "lab", "runs");
  mkdirSync(dir, { recursive: true });
  return dir;
}
const metaPath = (id: string) => join(runsDir(), `${id}.json`);
const streamPath = (id: string) => join(runsDir(), `${id}.ndjson`);

/** The module's own MCP server — the agent's entire world. */
export function mcpServerPath(): string {
  const fromEnv = process.env.POLYMARKET_MCP_PY;
  if (fromEnv && existsSync(fromEnv)) return fromEnv;
  const local = resolvePath(process.cwd(), "..", "mcp.py");
  if (existsSync(local)) return local;
  return "/root/mod/mod/orbit/polymarket/src/mcp.py";
}

/** Read-only research + the bench. No copy-desk, no live tools, no orders. */
const ALLOWED_TOOLS = [
  "pm_health", "pm_markets", "pm_top_traders", "pm_trader",
  "pm_strats", "pm_backtests", "pm_lab_backtest",
].map((t) => `mcp__polymarket__${t}`);

const DEFAULT_GOAL =
  "Find the best copy-index strategy this deployment's own data supports right now, " +
  "and keep refining it until the evidence clears the confidence bar.";

function buildPrompt(goal: string, maxExperiments: number): string {
  return [
    `You are the STRAT LAB agent inside a self-hosted Polymarket copy-trading console. You run experiments for the owner; nothing you do places an order or changes a strategy — your verdict is a proposal a human adopts or ignores.`,
    ``,
    `GOAL: ${goal}`,
    `If the GOAL itself names trader addresses, parameter values, or a specific strategy idea, build THAT as candidate #1 exactly as asked, backtest it, and iterate from there — the owner is vibe-coding through you, so their idea gets tested first even if you suspect a better one.`,
    ``,
    `HOW A STRATEGY WORKS HERE: a "copy index" watches a list of trader wallets and mirrors their entries, sized proportionally to the owner's capital. Parameters gate WHICH of their fills get copied and how positions exit. An optional \`momentum\` block instead ORIGINATES trades off a market's own price tape (no watchlist needed).`,
    ``,
    `CANDIDATE SHAPE for pm_lab_backtest — {"params": {...}, "windows": [1,3,7]}. params fields:`,
    `- name (string), traders (array of 0x… addresses, max ${MAX_TRADERS}), capital (USD, default 1000)`,
    `- plus any of the tunable parameters below (same paths, as nested JSON — e.g. tradeFilters.priceMin, filter.top, momentum.minRiseCents):`,
    paramReference(),
    ``,
    `METHOD — follow it in order:`,
    `1. SURVEY: pm_strats + pm_backtests (days 1, 3, 7) — what this console already runs, what worked, and WHY the rest didn't (read each funnel's blocked reasons and the fees line before theorising).`,
    `2. RESEARCH: pm_top_traders over more than one window and sort (winRate, sharpe, pnl; set min_history_days on long windows), then pm_trader on shortlisted wallets. Disqualify leaders whose flow is mostly sub-hour Up/Down candles — a poller cannot copy those. Prefer traders good in BOTH a short and a long window.`,
    `3. DESIGN 2-3 genuinely different candidates (different rosters or different gating philosophies — not one roster with a knob moved).`,
    `4. TEST each with pm_lab_backtest over windows [1,3,7]. Read every result fully: pnl is NET of fees; forward.verdict "held" is the only walk-forward pass; a large settlement.unverified_usd means the pnl is provisional (unresolved legs marked at last price, usually optimistic); warming > 0 means the number is a FLOOR — those traders had no cached history yet. When a result is warming, run other experiments first and RETEST that candidate a few minutes later.`,
    `5. ITERATE (up to ~${maxExperiments} experiments): change what the evidence indicts. Fees eating the edge → fewer, larger trades (maxPerCycle down, minTrade up). Funnel blocked on one gate → loosen that gate, not others. A trader dragging the index → drop them and retest. Great 1-day, bad 7-day → the edge is recency; say so.`,
    ``,
    `CONFIDENCE BAR — declare "confident" ONLY when your best candidate shows ALL of:`,
    `- positive roi net of fees on at least 2 of the 3 windows, including the 7-day;`,
    `- forward.verdict "held" on at least 2 windows;`,
    `- at least 10 trades in the 7-day window (fewer is an anecdote);`,
    `- unverified_usd under ~30% of capital in the windows you cite;`,
    `- warming = 0 on the cited results (retest until it is).`,
    `If ${maxExperiments} experiments don't clear the bar, stop and report confident: false with the honest reason — "this data does not support a confident strat today" is a valid, useful finding. Never lower the bar to manufacture a winner.`,
    ``,
    `FINISH by ending your final message with ONE json code fence and nothing after it:`,
    "```json",
    `{"confident": true|false, "best": {"name": "...", "params": { the exact winning candidate params }}, "windows_tested": [1,3,7], "evidence": ["one line per confidence criterion, with the numbers"], "runners_up": [{"name": "...", "why_not": "..."}], "notes": "caveats, and what to re-check in a day"}`,
    "```",
  ].join("\n");
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export function labRunning(): LabRunMeta | null {
  for (const m of listRunMetas()) {
    if (isAlive(m.pid) && !runFinished(m.id)) return m;
  }
  return null;
}

export function startLabRun(opts: { goal?: string; maxExperiments?: number } = {}): LabRunMeta {
  const active = labRunning();
  if (active) throw new Error(`a lab run is already going (${active.id}) — stop it or wait`);

  const id = `lab_${Date.now().toString(36)}`;
  const goal = (opts.goal || "").trim() || DEFAULT_GOAL;
  const maxExperiments = Math.min(Math.max(Number(opts.maxExperiments) || 12, 3), 25);
  const prompt = buildPrompt(goal, maxExperiments);

  const mcpConfig = JSON.stringify({
    mcpServers: {
      polymarket: { command: "python3", args: [mcpServerPath()] },
    },
  });
  const out = openSync(streamPath(id), "a");
  let child;
  try {
    child = spawn(CLAUDE_BIN, [
      "--print", "--model", MODEL,
      "--restricted", "--tools", "",
      "--mcp-config", mcpConfig, "--strict-mcp-config",
      "--allowedTools", ALLOWED_TOOLS.join(","),
      "--max-turns", String(MAX_TURNS),
      "--output-format", "stream-json", "--verbose",
    ], {
      // Detached, own process group: the run outlives this request, and a
      // stop can kill the agent AND its python MCP child in one signal.
      detached: true, stdio: ["pipe", out, out], cwd: "/tmp",
      env: claudeEnv(),
    });
  } finally {
    closeSync(out);
  }
  // A missing binary must fail the request, not crash the server (see the
  // EPIPE note in strat-chat).
  child.on("error", () => {});
  child.stdin?.on("error", () => {});
  child.stdin?.end(prompt);
  child.unref();

  const meta: LabRunMeta = {
    id, goal, model: MODEL, maxTurns: MAX_TURNS,
    pid: child.pid ?? 0, startedAt: Date.now(),
  };
  writeAtomic(metaPath(id), JSON.stringify(meta));
  return meta;
}

export function stopLabRun(id: string): boolean {
  const run = readRunMeta(id);
  if (!run || !run.pid) return false;
  try {
    process.kill(-run.pid, "SIGKILL"); // the whole group: claude + its MCP python
    return true;
  } catch {
    try {
      process.kill(run.pid, "SIGKILL");
      return true;
    } catch {
      return false;
    }
  }
}

function readRunMeta(id: string): LabRunMeta | null {
  try {
    return JSON.parse(readFileSync(metaPath(id), "utf8")) as LabRunMeta;
  } catch {
    return null;
  }
}

function listRunMetas(): LabRunMeta[] {
  let files: string[] = [];
  try {
    files = readdirSync(runsDir()).filter((f) => f.endsWith(".json"));
  } catch {
    return [];
  }
  return files
    .map((f) => readRunMeta(f.slice(0, -5)))
    .filter((m): m is LabRunMeta => !!m)
    .sort((a, b) => b.startedAt - a.startedAt);
}

/** The verdict object, dug out of the agent's closing fence. */
function extractVerdict(text: string): Record<string, unknown> | undefined {
  const candidates: string[] = [];
  const fenced = [...text.matchAll(/```(?:json)?\s*([\s\S]*?)```/g)];
  if (fenced.length > 0) candidates.push(fenced[fenced.length - 1][1]);
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first >= 0 && last > first) candidates.push(text.slice(first, last + 1));
  for (const c of candidates) {
    try {
      const o = JSON.parse(c.trim()) as Record<string, unknown>;
      if (typeof o.confident === "boolean") return o;
    } catch {
      // next shape
    }
  }
  return undefined;
}

function runFinished(id: string): boolean {
  try {
    const raw = readFileSync(streamPath(id), "utf8");
    return raw.includes('"type":"result"');
  } catch {
    return false;
  }
}

/** Everything the console needs to show a run: parsed from the stream file on
    demand — no in-process state, so it survives restarts. */
export function readLabRun(id: string, opts: { steps?: boolean } = {}): LabRun | null {
  const meta = readRunMeta(id);
  if (!meta) return null;
  let raw = "";
  try {
    raw = readFileSync(streamPath(id), "utf8");
  } catch {
    // spawned but nothing written yet
  }
  const steps: LabStep[] = [];
  let finalText: string | undefined;
  let turns: number | undefined;
  let costUsd: number | undefined;
  let error: string | undefined;
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (ev.type === "assistant") {
      const msg = ev.message as { content?: Array<Record<string, unknown>> } | undefined;
      for (const block of msg?.content ?? []) {
        if (block.type === "tool_use") {
          steps.push({
            kind: "tool",
            tool: String(block.name ?? "").replace(/^mcp__polymarket__/, ""),
            input: JSON.stringify(block.input ?? {}).slice(0, 400),
          });
        } else if (block.type === "text" && typeof block.text === "string" && block.text.trim()) {
          steps.push({ kind: "note", text: block.text.trim().slice(0, 1200) });
        }
      }
    } else if (ev.type === "result") {
      if (typeof ev.result === "string") finalText = ev.result;
      if (typeof ev.num_turns === "number") turns = ev.num_turns;
      if (typeof ev.total_cost_usd === "number") costUsd = ev.total_cost_usd;
      if (ev.is_error) error = typeof ev.result === "string" ? ev.result.slice(0, 400) : "agent run errored";
    }
  }
  const finished = finalText !== undefined || error !== undefined;
  const status: LabRun["status"] = finished ? "done" : isAlive(meta.pid) ? "running" : "died";
  return {
    ...meta,
    status,
    steps: opts.steps === false ? [] : steps,
    finalText,
    verdict: finalText ? extractVerdict(finalText) : undefined,
    turns,
    costUsd,
    ...(status === "died" ? { error: "the agent process died before finishing" } : error ? { error } : {}),
  };
}

export function listLabRuns(): Array<LabRunMeta & { status: LabRun["status"]; confident?: boolean }> {
  return listRunMetas().map((m) => {
    const finished = runFinished(m.id);
    const status: LabRun["status"] = finished ? "done" : isAlive(m.pid) ? "running" : "died";
    let confident: boolean | undefined;
    if (finished) {
      const run = readLabRun(m.id, { steps: false });
      confident = typeof run?.verdict?.confident === "boolean" ? (run.verdict.confident as boolean) : undefined;
    }
    return { ...m, status, confident };
  });
}
