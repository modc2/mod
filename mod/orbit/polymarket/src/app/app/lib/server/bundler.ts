// BUNDLER — the agent that forges copy-trade bundles with a high probability
// of consistent success, and the loop that keeps a fresh set waiting.
//
// One run:
//   1. POOL     every trader in the worker's feed store with enough history
//               (no network: the fetch loop already keeps these warm)
//   2. FORGE    lib/bundleForge.ts — block replays, train-only screen and
//               greedy bundle search, then ONE holdout replay per bundle and
//               an A/B/C/F grade. All numbers come from here.
//   3. REVIEW   a no-tool claude turn reads the proven bundles and writes a
//               name, a thesis, the risks a number can't show (one niche,
//               one event, candle flow) and a deploy/watch/skip verdict. It
//               cannot add, drop or re-size a leg: the forge owns the legs
//               and the numbers, the agent owns the words. If the review
//               fails (no token, timeout) the run still lands, unreviewed.
//
// Nothing here allocates money. A bundle reaches the copy desk only through
// the studio's COPY ALL (DRY RUN) or pm_copy_allocate (owner-approved).
//
// State: ~/.mod/polymarket/bundler/{settings.json,runs.json}. The loop is OFF
// until a human flips it — a run is minutes of CPU plus one model turn.

import { mkdirSync, readFileSync } from "fs";
import { join } from "path";

import {
  blockWindows, cleanForge, proveBundles, rankBundles, screen, searchBundles, selectionSkill, traderBlockSteps,
  DEFAULT_FORGE, type Bundle, type ForgeSettings, type TraderBlocks,
} from "../bundleForge";
import type { BasketFeeds } from "../basketSim";
import { fetchTraderBankrolls } from "../liveSessions";
import { setServerAuthToken } from "../polymarket";
import type { PolymarketPosition, PolymarketTrade } from "../types";
import { agentEnabled } from "./agentMods";
import { digJson, runClaude } from "./agentCli";
import { listFeedAddresses, readFeed, writeAtomic } from "./feedStore";
import { mintOwnerToken, stateDir } from "./ownerToken";
import { knownResolutions } from "./resolutionStore";

const MODEL = process.env.POLYMARKET_BUNDLER_MODEL || "claude-sonnet-5";
const REVIEW_TIMEOUT_MS = 120_000;
const MAX_RUNS_KEPT = 20;
const MIN_INTERVAL_SECS = 30 * 60;
/** Ceiling on the pool — each trader is `blocks` full replays. */
const MAX_POOL = 400;

// ── settings + runs on disk ──────────────────────────────────────────────

export interface BundlerSettings {
  enabled: boolean;
  intervalSecs: number;
  forge: ForgeSettings;
  /** Ask the reviewing agent for names/theses/verdicts. */
  review: boolean;
}

export interface BundlerRun {
  id: string;
  at: number;
  origin: "loop" | "click";
  status: "running" | "done" | "failed";
  /** Where a running run is: "pool", "replay 120/340", "prove", "review". */
  stage?: string;
  error?: string;
  forge: ForgeSettings;
  pool?: number;
  survivors?: number;
  /** The screen's reject reasons, counted — why the pool shrank. */
  rejects?: Record<string, number>;
  bundles?: Bundle[];
  /** Did this run's screen predict the holdout? (lib/bundleForge selectionSkill) */
  skill?: ReturnType<typeof selectionSkill>;
  reviewed?: boolean;
  reviewError?: string;
  summary?: string;
  finishedAt?: number;
  model?: string;
}

function dir(): string {
  const d = join(stateDir(), "bundler");
  mkdirSync(d, { recursive: true });
  return d;
}
const settingsPath = () => join(dir(), "settings.json");
const runsPath = () => join(dir(), "runs.json");

export function readSettings(): BundlerSettings {
  try {
    const raw = JSON.parse(readFileSync(settingsPath(), "utf8")) as Partial<BundlerSettings>;
    return {
      enabled: raw.enabled === true,
      intervalSecs: Math.max(Number(raw.intervalSecs) || 6 * 3600, MIN_INTERVAL_SECS),
      forge: cleanForge(raw.forge),
      review: raw.review !== false,
    };
  } catch {
    return { enabled: false, intervalSecs: 6 * 3600, forge: DEFAULT_FORGE, review: true };
  }
}

export function writeSettings(patch: Partial<BundlerSettings>): BundlerSettings {
  const cur = readSettings();
  const next: BundlerSettings = {
    ...cur,
    ...patch,
    forge: cleanForge({ ...cur.forge, ...(patch.forge ?? {}) }),
  };
  next.intervalSecs = Math.max(Number(next.intervalSecs) || MIN_INTERVAL_SECS, MIN_INTERVAL_SECS);
  writeAtomic(settingsPath(), JSON.stringify(next, null, 2));
  return next;
}

export function listRuns(): BundlerRun[] {
  try {
    const raw = JSON.parse(readFileSync(runsPath(), "utf8"));
    return Array.isArray(raw) ? (raw as BundlerRun[]) : [];
  } catch {
    return [];
  }
}

function saveRun(run: BundlerRun): void {
  const runs = listRuns().filter((r) => r.id !== run.id);
  runs.unshift(run);
  writeAtomic(runsPath(), JSON.stringify(runs.slice(0, MAX_RUNS_KEPT)));
}

/** The newest finished run — what the console shows by default. */
export function latestDone(): BundlerRun | null {
  return listRuns().find((r) => r.status === "done") ?? null;
}

// ── the run ──────────────────────────────────────────────────────────────

let active: BundlerRun | null = null;
export const bundlerActive = () => active;

/** Give the event loop a breath between replays — the console is served by
    this same process and a run is minutes of synchronous replay. */
const breathe = () => new Promise<void>((r) => setImmediate(r));

export function triggerBundler(
  origin: "loop" | "click",
  forgePatch?: Partial<ForgeSettings>,
  review?: boolean,
): BundlerRun {
  if (active) throw new Error(`a forge run is already going (${active.id}) — one at a time`);
  const settings = readSettings();
  const run: BundlerRun = {
    id: `bdl_${Date.now().toString(36)}`,
    at: Date.now(),
    origin,
    status: "running",
    stage: "pool",
    forge: cleanForge({ ...settings.forge, ...(forgePatch ?? {}) }),
    model: MODEL,
  };
  active = run;
  saveRun(run);
  void executeRun(run, review ?? settings.review)
    .catch((e) => {
      run.status = "failed";
      run.error = (e instanceof Error ? e.message : String(e)).slice(0, 400);
    })
    .finally(() => {
      if (run.status === "running") run.status = "failed";
      run.stage = undefined;
      run.finishedAt = Date.now();
      saveRun(run);
      active = null;
    });
  return run;
}

async function executeRun(run: BundlerRun, review: boolean): Promise<void> {
  const s = run.forge;
  const now = Date.now();
  const blocks = blockWindows(now, s);

  // ── 1. pool: the feed store, filtered to who can possibly qualify ──
  const minHistoryMs = now - (s.testBlocks + s.minTrainBlocks) * s.blockDays * 86400_000;
  const trades = new Map<string, PolymarketTrade[]>();
  const positions = new Map<string, PolymarketPosition[]>();
  const conds = new Set<string>();
  const sized: { address: string; n: number }[] = [];
  for (const addr of listFeedAddresses()) {
    const feed = readFeed(addr);
    if (!feed || feed.trades.length < s.minTrainTrades) continue;
    const first = feed.trades.reduce((m, t) => Math.min(m, t.timestamp), Infinity);
    if (!(first <= minHistoryMs)) continue;
    const key = addr.toLowerCase();
    trades.set(key, feed.trades);
    positions.set(key, feed.positions);
    sized.push({ address: key, n: feed.trades.length });
  }
  // Too many? Keep the most active — a quiet wallet can't fill enough blocks
  // to be called consistent anyway.
  const pool = sized.sort((a, b) => b.n - a.n).slice(0, MAX_POOL).map((x) => x.address);
  for (const a of pool) for (const t of trades.get(a) ?? []) if (t.conditionId) conds.add(t.conditionId);
  run.pool = pool.length;
  if (pool.length === 0) {
    throw new Error("the feed store has no trader with enough history yet — the hub worker fills it; retry later");
  }

  const token = mintOwnerToken();
  if (token) setServerAuthToken(token);
  const bankrolls = await fetchTraderBankrolls(pool).catch(() => new Map<string, number>());
  const feeds: BasketFeeds = { trades, positions, bankrolls, resolved: knownResolutions(conds) };

  // ── 2. forge ──
  const tbs: TraderBlocks[] = [];
  for (let i = 0; i < pool.length; i++) {
    if (i % 10 === 0) {
      run.stage = `replay ${i}/${pool.length}`;
      if (i % 50 === 0) saveRun(run);
    }
    const steps = traderBlockSteps(pool[i], feeds, blocks, s);
    for (;;) {
      const r = steps.next();
      await breathe();
      if (r.done) { tbs.push(r.value); break; }
    }
  }
  const cands = screen(tbs, s);
  run.survivors = cands.filter((c) => !c.reject).length;
  const rejects: Record<string, number> = {};
  for (const c of cands) {
    if (!c.reject) continue;
    const k = c.reject.replace(/-?[0-9]+(\.[0-9]+)?/g, "#");
    rejects[k] = (rejects[k] ?? 0) + 1;
  }
  run.rejects = rejects;
  run.skill = selectionSkill(cands, s);

  run.stage = "prove";
  saveRun(run);
  const drafts = searchBundles(cands, s);
  await breathe();
  // One holdout replay per bundle — proved one at a time for the same reason.
  const proven: Bundle[] = [];
  for (const d of drafts) {
    proven.push(...proveBundles([d], feeds, s, now, (run.at + proven.length) % 2_147_483_647));
    await breathe();
  }
  run.bundles = rankBundles(proven);
  const top = run.bundles[0];
  run.summary = top
    ? `${run.bundles.length} bundle(s) from ${run.survivors}/${run.pool} screened traders; best ${top.grade} `
      + `· ${top.legs.length} legs · holdout ${top.holdout.pct.toFixed(1)}% over ${top.holdout.days}d`
    : `no bundle — ${run.survivors}/${run.pool} traders survived the train screen`;

  // ── 3. review ──
  if (review && run.bundles.length > 0) {
    run.stage = "review";
    saveRun(run);
    try {
      await reviewBundles(run);
      run.reviewed = true;
    } catch (e) {
      run.reviewError = (e instanceof Error ? e.message : String(e)).slice(0, 300);
    }
  }
  run.status = "done";
}

// ── the reviewing agent ──────────────────────────────────────────────────

const pct = (v: number, d = 1) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;

function bundleBrief(b: Bundle): string {
  const lines = [
    `BUNDLE ${b.id} — grade ${b.grade} (${b.gradeText})`,
    `  train: green ${b.train.green}/${b.train.n} blocks (P-green lower bound ${(b.train.greenLB * 100).toFixed(0)}%), `
      + `mean ${pct(b.train.mean, 2)}/block, worst ${pct(b.train.worst)}`,
    `  HOLDOUT (unseen ${b.holdout.days}d): ${b.holdout.pct >= 0 ? "+" : ""}${b.holdout.pct.toFixed(2)}% `
      + `($${b.holdout.net.toFixed(2)} on $${b.totalUsd}), drawdown ${b.holdout.drawdown.toFixed(1)}%, `
      + `${b.holdout.legsTrading}/${b.holdout.legs} legs traded, settled share ${(b.holdout.confidence * 100).toFixed(0)}%, `
      + `test blocks green ${b.test.green}/${b.test.n}`,
    `  bootstrap P(up over ~7d): ${(b.pWeekUp * 100).toFixed(0)}%`,
    `  legs:`,
    ...b.legs.map((l) => `    ${l.address}  train ${l.trainGreen} green, mean ${pct(l.trainMean, 2)}, worst ${pct(l.trainWorst)}, `
      + `${l.trainTrades} trades | holdout ${l.holdoutPct >= 0 ? "+" : ""}${l.holdoutPct.toFixed(1)}% `
      + `(${l.holdoutTrades} trades)${l.note ? ` [${l.note}]` : ""} | trades: ${l.topMarkets.slice(0, 3).join(" · ") || "?"}`),
  ];
  return lines.join("\n");
}

function reviewPrompt(run: BundlerRun): string {
  const b = run.bundles ?? [];
  return [
    `You review copy-trade BUNDLES for a self-hosted Polymarket copy desk. Each bundle copies 3-6 trader wallets at $${run.forge.legUsd} each. A deterministic forge built them: it replayed copying every trader in ${run.forge.blockDays}-day blocks, picked legs using OLDER blocks only (train), and then replayed the most recent ${run.forge.testBlocks * run.forge.blockDays} days it never looked at (HOLDOUT). Grades: A consistent, B promising, C unproven, F failed holdout.`,
    ``,
    `Your job is judgment the numbers can't do. You CANNOT change legs or numbers. For each bundle write:`,
    `- name: short, evocative, <=32 chars, describes what the legs actually trade`,
    `- thesis: one or two sentences — why these wallets together should keep working`,
    `- risks: 1-3 short strings — concentration in one niche/event, sports-season or election dependence, candle/short-horizon flow a poller copies late, thin holdout sample, legs that did nothing in holdout`,
    `- verdict: "deploy" (start in DRY RUN, then small real size), "watch" (paper only), or "skip". Be strict: a red or tiny holdout, an idle leg, or one market carrying the result is not "deploy".`,
    ``,
    ...(run.skill ? [
      `SCREEN CALIBRATION this run (traders up over the holdout): top-20 kept ${(run.skill.top.pUp * 100).toFixed(0)}% of ${run.skill.top.n}, `
        + `other kept ${(run.skill.rest.pUp * 100).toFixed(0)}% of ${run.skill.rest.n}, rejected ${(run.skill.rejected.pUp * 100).toFixed(0)}% of ${run.skill.rejected.n}. `
        + `If top does not clearly beat rejected, say the method isn't predicting this run.`,
      ``,
    ] : []),
    ...b.map(bundleBrief),
    ``,
    `Reply with ONE JSON object and nothing else:`,
    `{"summary": "<one sentence across all bundles: which to use and why>", "bundles": [{"id": "b1", "name": "…", "thesis": "…", "risks": ["…"], "verdict": "deploy|watch|skip"}]}`,
  ].join("\n");
}

async function reviewBundles(run: BundlerRun): Promise<void> {
  const res = await runClaude(reviewPrompt(run), MODEL, {
    timeoutMs: REVIEW_TIMEOUT_MS,
    extraArgs: ["--restricted", "--tools", ""],
  });
  if (res.ok === false) throw new Error(res.error);
  const parsed = digJson(res.text, (o) => (Array.isArray(o.bundles) ? o : null));
  if (!parsed) throw new Error("the reviewer answered, but not with a bundles object");
  const byId = new Map<string, Record<string, unknown>>();
  for (const r of parsed.bundles as Record<string, unknown>[]) {
    if (r && typeof r.id === "string") byId.set(r.id, r);
  }
  const str = (v: unknown, n: number) => (typeof v === "string" ? v.trim().slice(0, n) : undefined);
  for (const b of run.bundles ?? []) {
    const r = byId.get(b.id);
    if (!r) continue;
    const verdict = str(r.verdict, 10)?.toLowerCase();
    b.review = {
      name: str(r.name, 40),
      thesis: str(r.thesis, 400),
      risks: Array.isArray(r.risks) ? r.risks.map((x) => str(x, 160)).filter((x): x is string => !!x).slice(0, 4) : [],
      // An F can never be talked into "deploy" — the holdout already spoke.
      verdict: b.grade === "F" && verdict === "deploy" ? "watch"
        : verdict && ["deploy", "watch", "skip"].includes(verdict) ? verdict : undefined,
    };
  }
  const summary = str(parsed.summary, 400);
  if (summary) run.summary = summary;
}

// ── the loop ─────────────────────────────────────────────────────────────

let ticker: ReturnType<typeof setInterval> | null = null;

export function startBundlerLoop(): void {
  if (ticker) return;
  // A server restart mid-run leaves a "running" row behind; nothing is
  // driving it any more, so say so instead of showing a forever-spinner.
  const stale = listRuns().filter((r) => r.status === "running");
  for (const r of stale) saveRun({ ...r, status: "failed", stage: undefined, error: "interrupted by a server restart" });
  ticker = setInterval(() => {
    const s = readSettings();
    if (!s.enabled || active || !agentEnabled("bundler")) return;
    const last = listRuns()[0];
    if (last && Date.now() - last.at < s.intervalSecs * 1000) return;
    try {
      triggerBundler("loop");
    } catch {
      // raced a click — next tick
    }
  }, 60_000);
  ticker.unref?.();
}
