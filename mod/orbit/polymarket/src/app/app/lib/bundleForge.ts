// THE BUNDLE FORGE — build copy-trade bundles that keep working, and prove it
// on days the forge never looked at.
//
// The question it answers: "which 3–6 traders, copied together at $L each,
// have the best chance of being UP over any stretch I start copying them?"
// That is a consistency question, not a profit one, so the unit of evidence
// is the BLOCK — a short window (default 3 days) replayed as if you started
// copying at its open with fresh cash. Thirty days of feed = ten blocks; a
// trader who is green in 8 of 10 blocks is worth more here than one whose
// whole month rode on a single resolution.
//
//   blocks:  [ b0 b1 b2 | b3 b4 b5 b6 b7 b8 b9 ]   (b0 = most recent)
//              HOLDOUT     TRAIN
//
// Every pick — the per-trader screen, the greedy bundle search, the ranking —
// reads TRAIN blocks only. The HOLDOUT is then replayed once, continuously,
// for the bundles already chosen; a bundle whose holdout is red is graded F
// no matter how good its train looked. That split is the whole point: the
// 2026-10-09 study found "top 5 by 30d" strats at +469% headline and −5.8%
// holdout, and copy-bleed eating steady grinders' edge. Selecting on the past
// and scoring on the after is the only number here worth deploying on.
//
// Sleeves are additive (basketSim pins it: a leg replayed alone == its number
// inside a basket), so a bundle's block return is just the mean of its legs'
// block returns at equal capital — the greedy search costs arithmetic, not
// replays. Only the per-trader blocks and the final holdout are replays.
//
// PURE: no network, no fs. The server (lib/server/bundler.ts) loads feeds and
// calls in; the tests call in with synthetic feeds.

import {
  runBasketSim, replaySleeve, summarizeSleeve,
  type BasketFeeds, type BasketLeg,
} from "./basketSim";
import { wilsonLower } from "./scoreFormula";

// ── settings ─────────────────────────────────────────────────────────────

export interface ForgeSettings {
  /** Length of one evidence block, days. */
  blockDays: number;
  /** How many blocks back from now (blocks × blockDays ≤ the 30d feed cap). */
  blocks: number;
  /** The most recent N blocks are HOLDOUT — never read by selection. */
  testBlocks: number;
  /** Dollars per leg in every replay. Under a leader's order floor a leg
      takes NO position, so this is part of the answer, not a display unit. */
  legUsd: number;
  minLegs: number;
  maxLegs: number;
  /** A candidate must have feed history covering this many TRAIN blocks. */
  minTrainBlocks: number;
  /** …and have actually copied this many trades across them. */
  minTrainTrades: number;
  /** Two legs whose train markets overlap more than this (Jaccard) are the
      same bet twice — the desk would place both orders. */
  maxOverlap: number;
  /** How many distinct bundles to return. */
  bundles: number;
  /** Shortlist size the greedy search draws from. */
  shortlist: number;
  /** Every leg must be consistent ON ITS OWN (Wilson LB of its green train
      blocks). Combinations overfit faster than single names: a pair whose
      red blocks happen to interleave looks perfect in train and needn't
      repeat. On the 10-19 data, legs ≥ 0.4 were up on the holdout 69–83% of
      the time vs 49% below it. */
  minLegGreenLB: number;
  /** A wallet may appear in at most this many of the returned bundles. A
      low-variance leg flatters every combination it joins, and five
      bundles that share it are one bet, not five options (10-19: one idle
      weather wallet sat in 5/5). */
  maxLegReuse: number;
}

export const DEFAULT_FORGE: ForgeSettings = {
  blockDays: 3,
  blocks: 10,
  testBlocks: 3,
  legUsd: 250,
  minLegs: 3,
  maxLegs: 5,
  minTrainBlocks: 5,
  minTrainTrades: 8,
  maxOverlap: 0.5,
  bundles: 5,
  shortlist: 40,
  minLegGreenLB: 0.4,
  maxLegReuse: 2,
};

/** Clamp a caller's patch into something the feed can actually answer. */
export function cleanForge(raw: Partial<ForgeSettings> | null | undefined): ForgeSettings {
  const s = { ...DEFAULT_FORGE, ...(raw ?? {}) } as ForgeSettings;
  const int = (v: unknown, lo: number, hi: number, d: number) => {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d;
  };
  s.blockDays = int(s.blockDays, 1, 10, DEFAULT_FORGE.blockDays);
  s.blocks = int(s.blocks, 3, Math.floor(30 / s.blockDays), Math.min(DEFAULT_FORGE.blocks, Math.floor(30 / s.blockDays)));
  s.testBlocks = int(s.testBlocks, 1, s.blocks - 2, Math.min(DEFAULT_FORGE.testBlocks, s.blocks - 2));
  s.legUsd = Math.min(100_000, Math.max(10, Number(s.legUsd) || DEFAULT_FORGE.legUsd));
  s.maxLegs = int(s.maxLegs, 1, 10, DEFAULT_FORGE.maxLegs);
  s.minLegs = int(s.minLegs, 1, s.maxLegs, Math.min(DEFAULT_FORGE.minLegs, s.maxLegs));
  const train = s.blocks - s.testBlocks;
  s.minTrainBlocks = int(s.minTrainBlocks, 1, train, Math.min(DEFAULT_FORGE.minTrainBlocks, train));
  s.minTrainTrades = int(s.minTrainTrades, 0, 10_000, DEFAULT_FORGE.minTrainTrades);
  s.maxOverlap = Math.min(1, Math.max(0, Number(s.maxOverlap)));
  if (!Number.isFinite(s.maxOverlap)) s.maxOverlap = DEFAULT_FORGE.maxOverlap;
  s.bundles = int(s.bundles, 1, 12, DEFAULT_FORGE.bundles);
  s.shortlist = int(s.shortlist, 5, 200, DEFAULT_FORGE.shortlist);
  s.minLegGreenLB = Math.min(0.9, Math.max(0, Number(s.minLegGreenLB)));
  if (!Number.isFinite(s.minLegGreenLB)) s.minLegGreenLB = DEFAULT_FORGE.minLegGreenLB;
  s.maxLegReuse = int(s.maxLegReuse, 1, 12, DEFAULT_FORGE.maxLegReuse);
  return s;
}

// ── blocks ───────────────────────────────────────────────────────────────

export interface Block {
  /** 0 = most recent. */
  index: number;
  start: number;
  end: number;
  holdout: boolean;
}

export function blockWindows(now: number, s: ForgeSettings): Block[] {
  const len = s.blockDays * 86400_000;
  return Array.from({ length: s.blocks }, (_, i) => ({
    index: i,
    end: now - i * len,
    start: now - (i + 1) * len,
    holdout: i < s.testBlocks,
  }));
}

/** A block return counts as GREEN above this (fraction of capital). Flat is
    not success — idle cash is not a win. */
export const GREEN_EPS = 0.001;

// ── one trader, block by block ───────────────────────────────────────────

export interface TraderBlocks {
  address: string;
  /** Per block (index = Block.index): did the feed reach back this far? */
  covered: boolean[];
  /** Per block: net P&L on `legUsd` as a fraction (0.02 = +2%). 0 if idle. */
  ret: number[];
  trades: number[];
  /** Per block: settled share of the exit value, 0–1 (1 when idle). */
  confidence: number[];
  /** conditionIds traded in TRAIN blocks — the overlap check reads these. */
  trainMarkets: string[];
  /** Most-traded market titles in TRAIN blocks, for humans and the reviewer. */
  topMarkets: string[];
  /** Did the leader trade in the last block at all? Copyable now? */
  activeRecently: boolean;
}

/** Replay one trader over every block. */
export function traderBlocks(
  address: string,
  feeds: BasketFeeds,
  blocks: Block[],
  s: ForgeSettings,
): TraderBlocks {
  const steps = traderBlockSteps(address, feeds, blocks, s);
  for (;;) {
    const r = steps.next();
    if (r.done) return r.value;
  }
}

/** The same, one block replay per step — the server drains it with a yield
    to the event loop between steps, so the console it shares a process with
    stays responsive through a minutes-long run. */
export function* traderBlockSteps(
  address: string,
  feeds: BasketFeeds,
  blocks: Block[],
  s: ForgeSettings,
): Generator<void, TraderBlocks, void> {
  const key = address.toLowerCase();
  const trades = feeds.trades.get(key) ?? feeds.trades.get(address) ?? [];
  const first = trades.reduce((m, t) => Math.min(m, t.timestamp), Infinity);
  const leg: BasketLeg = { address: key, allocationUsd: s.legUsd };
  const out: TraderBlocks = {
    address: key, covered: [], ret: [], trades: [], confidence: [],
    trainMarkets: [], topMarkets: [], activeRecently: false,
  };
  const markets = new Set<string>();
  const titles = new Map<string, number>();
  for (const b of blocks) {
    // Timestamps in the feed are seconds or ms depending on the source path;
    // the replay normalizes, so only coverage needs care here.
    const firstMs = first < 1e12 ? first * 1000 : first;
    const covered = Number.isFinite(firstMs) && firstMs <= b.start;
    out.covered[b.index] = covered;
    if (!covered) {
      out.ret[b.index] = 0; out.trades[b.index] = 0; out.confidence[b.index] = 1;
      continue;
    }
    const sleeve = summarizeSleeve(leg,
      replaySleeve(leg, feeds, { days: s.blockDays, asOf: b.end }), s.legUsd);
    out.ret[b.index] = sleeve.net / s.legUsd;
    out.trades[b.index] = sleeve.trades;
    out.confidence[b.index] = sleeve.confidence;
    if (!b.holdout) for (const m of sleeve.markets) markets.add(m);
    yield;
  }
  const trainStart = blocks.filter((b) => !b.holdout).reduce((m, b) => Math.min(m, b.start), Infinity);
  const trainEnd = blocks.filter((b) => !b.holdout).reduce((m, b) => Math.max(m, b.end), 0);
  for (const t of trades) {
    const ms = t.timestamp < 1e12 ? t.timestamp * 1000 : t.timestamp;
    if (ms >= trainStart && ms <= trainEnd && t.market) titles.set(t.market, (titles.get(t.market) ?? 0) + 1);
  }
  out.trainMarkets = [...markets];
  out.topMarkets = [...titles.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([m]) => m);
  // "Recently" = inside the last block. Reading the holdout's own activity is
  // deliberate and harmless: it decides who you CAN copy now, not who is good.
  const last = blocks[0];
  out.activeRecently = trades.some((t) => {
    const ms = t.timestamp < 1e12 ? t.timestamp * 1000 : t.timestamp;
    return ms >= last.start && ms <= last.end;
  });
  return out;
}

// ── scoring a return series ──────────────────────────────────────────────

export interface SeriesStats {
  n: number;
  green: number;
  /** Wilson lower bound (one-sided 95%) on P(a block is green). */
  greenLB: number;
  mean: number;
  sd: number;
  worst: number;
  /** Compounded over the series. */
  total: number;
  objective: number;
}

export function seriesStats(rets: number[]): SeriesStats {
  const n = rets.length;
  if (n === 0) {
    return { n: 0, green: 0, greenLB: 0, mean: 0, sd: 0, worst: 0, total: 0, objective: -Infinity };
  }
  const green = rets.filter((r) => r > GREEN_EPS).length;
  const mean = rets.reduce((s, r) => s + r, 0) / n;
  const sd = Math.sqrt(rets.reduce((s, r) => s + (r - mean) ** 2, 0) / n);
  const worst = Math.min(...rets);
  const total = rets.reduce((acc, r) => acc * (1 + r), 1) - 1;
  const greenLB = wilsonLower(green / n, n);
  return { n, green, greenLB, mean, sd, worst, total, objective: objective(greenLB, mean, sd, worst) };
}

/** What the forge maximizes. Consistency dominates by construction:
 *    greenLB   0–100  P(green block), discounted for small samples
 *    smooth    0–15   mean / (sd + 0.5%) — reward per unit of wobble
 *    return    0–10   tanh(mean / 2%) — small, so a lottery can't buy rank
 *    tail      −∞–0   −200 per point of block loss past −5%
 *  Exported (and pinned in tests) so the UI's HOW IT'S SCORED is the code. */
export function objective(greenLB: number, mean: number, sd: number, worst: number): number {
  const smooth = 15 * Math.tanh(mean / (sd + 0.005));
  const ret = 10 * Math.tanh(mean / 0.02);
  const tail = -200 * Math.max(0, -0.05 - worst);
  return 100 * greenLB + smooth + ret + tail;
}

function jaccard(a: string[], b: string[]): number {
  if (a.length === 0 || b.length === 0) return 0;
  const sb = new Set(b);
  let inter = 0;
  for (const x of a) if (sb.has(x)) inter++;
  return inter / (a.length + b.length - inter);
}

// ── the screen ───────────────────────────────────────────────────────────

export interface Candidate {
  address: string;
  blocks: TraderBlocks;
  train: SeriesStats;
  trainTrades: number;
  coveredTrain: number;
  /** Why a trader was screened out — only set on rejects. */
  reject?: string;
}

function trainIdx(s: ForgeSettings): number[] {
  return Array.from({ length: s.blocks - s.testBlocks }, (_, i) => i + s.testBlocks);
}
function testIdx(s: ForgeSettings): number[] {
  return Array.from({ length: s.testBlocks }, (_, i) => i);
}

/** Below this settled share a trader's train P&L is mostly last-printed
    marks on open positions — an upper bound, not evidence. Train blocks are
    ≥ testBlocks×blockDays old, so an honest record has mostly resolved. */
export const MIN_SETTLED = 0.3;

/** Mean settled share over the train blocks the trader actually traded in. */
export function settledShare(tb: TraderBlocks, idx: number[]): number {
  const active = idx.filter((i) => tb.covered[i] && (tb.trades[i] ?? 0) > 0);
  if (active.length === 0) return 1;
  return active.reduce((s, i) => s + (tb.confidence[i] ?? 1), 0) / active.length;
}

/** Screen every trader on TRAIN only. Returns all of them, rejects carrying
    their reason, survivors sorted best-first. */
export function screen(all: TraderBlocks[], s: ForgeSettings): Candidate[] {
  const idx = trainIdx(s);
  const out: Candidate[] = all.map((tb) => {
    const covered = idx.filter((i) => tb.covered[i]);
    const train = seriesStats(covered.map((i) => tb.ret[i]));
    const trainTrades = covered.reduce((n, i) => n + (tb.trades[i] ?? 0), 0);
    const c: Candidate = { address: tb.address, blocks: tb, train, trainTrades, coveredTrain: covered.length };
    if (covered.length < s.minTrainBlocks) c.reject = `history covers ${covered.length}/${idx.length} train blocks`;
    else if (trainTrades < s.minTrainTrades) c.reject = `${trainTrades} copied trades in train (< ${s.minTrainTrades})`;
    else if (!tb.activeRecently) c.reject = "no trades in the last block — not copyable now";
    else if (train.mean <= 0) c.reject = "train mean return ≤ 0";
    else if (train.worst <= -0.25) c.reject = `a train block lost ${(train.worst * 100).toFixed(0)}%`;
    else if (settledShare(tb, idx) < MIN_SETTLED) c.reject = `train result is mostly unsettled marks (${Math.round(settledShare(tb, idx) * 100)}% settled)`;
    return c;
  });
  return out.sort((a, b) => {
    if (!!a.reject !== !!b.reject) return a.reject ? 1 : -1;
    return b.train.objective - a.train.objective;
  });
}

// ── does the screen predict? ─────────────────────────────────────────────

export interface SkillRow {
  n: number;
  /** Share whose holdout blocks summed positive. */
  pUp: number;
  /** Mean holdout block return. */
  mean: number;
}

/** The calibration check every run publishes: of the traders the screen
    ranked top, how many were up on the holdout — against those it kept
    lower and those it rejected. READS THE HOLDOUT, so it is reporting only:
    nothing here feeds back into a pick. If "top" stops beating "rejected",
    the forge's notion of consistency has stopped predicting and its bundles
    deserve no trust that run. */
export function selectionSkill(cands: Candidate[], s: ForgeSettings, top = 20): {
  top: SkillRow; rest: SkillRow; rejected: SkillRow;
} {
  const t = testIdx(s);
  const row = (cs: Candidate[]): SkillRow => {
    const sums = cs.map((c) => t.reduce((acc, i) => acc + (c.blocks.covered[i] ? c.blocks.ret[i] : 0), 0));
    const n = sums.length;
    return {
      n,
      pUp: n ? sums.filter((x) => x > GREEN_EPS).length / n : 0,
      mean: n ? sums.reduce((a, b) => a + b, 0) / n / t.length : 0,
    };
  };
  const kept = cands.filter((c) => !c.reject);
  return {
    top: row(kept.slice(0, top)),
    rest: row(kept.slice(top)),
    rejected: row(cands.filter((c) => !!c.reject && c.coveredTrain >= s.minTrainBlocks)),
  };
}

// ── the bundle search ────────────────────────────────────────────────────

/** Bundle return per block = mean of its legs' block returns (equal $ per
    leg, sleeves additive). A leg with no history that far back sits in cash. */
export function bundleSeries(legs: TraderBlocks[], idx: number[]): number[] {
  return idx.map((i) => legs.reduce((s, l) => s + (l.covered[i] ? l.ret[i] : 0), 0) / Math.max(1, legs.length));
}

export interface Draft {
  legs: Candidate[];
  train: SeriesStats;
  seed: string;
}

function greedy(seed: Candidate, pool: Candidate[], s: ForgeSettings, idx: number[]): Draft {
  const legs = [seed];
  let best = seriesStats(bundleSeries(legs.map((l) => l.blocks), idx));
  while (legs.length < s.maxLegs) {
    let pick: Candidate | null = null;
    let pickStats: SeriesStats | null = null;
    for (const c of pool) {
      if (legs.some((l) => l.address === c.address)) continue;
      if (legs.some((l) => jaccard(l.blocks.trainMarkets, c.blocks.trainMarkets) > s.maxOverlap)) continue;
      const st = seriesStats(bundleSeries([...legs, c].map((l) => l.blocks), idx));
      if (!pickStats || st.objective > pickStats.objective) { pick = c; pickStats = st; }
    }
    if (!pick || !pickStats) break;
    // Past the minimum, a leg must earn its place. Below it, take the best
    // available — a one-name "bundle" is a single bet, not diversification.
    if (legs.length >= s.minLegs && pickStats.objective <= best.objective) break;
    legs.push(pick);
    best = pickStats;
  }
  return { legs, train: best, seed: seed.address };
}

/** Train-only search: greedy forward selection from several seeds, deduped,
    then picked for diversity (a second bundle that is the first one plus a
    name is not a second option). */
export function searchBundles(cands: Candidate[], s: ForgeSettings): Draft[] {
  const idx = trainIdx(s);
  const pool = cands.filter((c) => !c.reject && c.train.greenLB >= s.minLegGreenLB).slice(0, s.shortlist);
  if (pool.length === 0) return [];
  const seeds = pool.slice(0, Math.min(pool.length, Math.max(12, s.bundles * 5)));
  const seen = new Set<string>();
  const drafts: Draft[] = [];
  for (const seed of seeds) {
    const d = greedy(seed, pool, s, idx);
    if (d.legs.length < Math.min(s.minLegs, pool.length)) continue;
    const key = d.legs.map((l) => l.address).sort().join(",");
    if (seen.has(key)) continue;
    seen.add(key);
    drafts.push(d);
  }
  drafts.sort((a, b) => b.train.objective - a.train.objective);
  const picked: Draft[] = [];
  const uses = new Map<string, number>();
  for (const d of drafts) {
    const names = d.legs.map((l) => l.address);
    if (picked.some((p) => jaccard(p.legs.map((l) => l.address), names) >= 0.6)) continue;
    if (names.some((n) => (uses.get(n) ?? 0) >= s.maxLegReuse)) continue;
    picked.push(d);
    for (const n of names) uses.set(n, (uses.get(n) ?? 0) + 1);
    if (picked.length >= s.bundles) break;
  }
  return picked;
}

// ── holdout + grade ──────────────────────────────────────────────────────

export interface BundleLeg {
  address: string;
  usd: number;
  trainGreen: string;
  trainMean: number;
  trainWorst: number;
  trainTrades: number;
  holdoutPct: number;
  holdoutTrades: number;
  note?: string;
  topMarkets: string[];
}

export type Grade = "A" | "B" | "C" | "F";

export const GRADE_TEXT: Record<Grade, string> = {
  A: "CONSISTENT — green on unseen days, steady in train, every leg trading",
  B: "PROMISING — green on unseen days, decent train consistency",
  C: "UNPROVEN — green on unseen days, but train consistency is weak",
  F: "FAILED HOLDOUT — picked on the past, lost money after",
};

export interface Bundle {
  id: string;
  legs: BundleLeg[];
  totalUsd: number;
  train: SeriesStats;
  /** Holdout block series and its stats (blocks the search never saw). */
  test: SeriesStats & { rets: number[] };
  /** One continuous replay over the whole holdout, as you'd have copied it. */
  holdout: {
    days: number;
    net: number;
    pct: number;
    drawdown: number;
    trades: number;
    legsTrading: number;
    legs: number;
    confidence: number;
    idleUsd: number;
  };
  /** P(up) over a ~7-day stretch: a bootstrap over ALL of this bundle's
      blocks, capped at the Wilson lower bound of its green-block rate — ten
      green blocks resample to "100%", and ten samples can't support that. */
  pWeekUp: number;
  grade: Grade;
  gradeText: string;
  /** Filled by the reviewing agent, never by the forge. */
  review?: { name?: string; thesis?: string; risks?: string[]; verdict?: string };
}

/** Deterministic PRNG so a run's bootstrap is reproducible from its id. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Fraction of `draws` resampled stretches of `k` blocks that end up. */
export function bootstrapUp(rets: number[], k: number, draws = 2000, rnd: () => number = Math.random): number {
  if (rets.length === 0 || k <= 0) return 0;
  let up = 0;
  for (let d = 0; d < draws; d++) {
    let acc = 1;
    for (let j = 0; j < k; j++) acc *= 1 + rets[Math.floor(rnd() * rets.length)];
    if (acc - 1 > GREEN_EPS) up++;
  }
  return up / draws;
}

export function pWeekUp(rets: number[], weekBlocks: number, rnd: () => number = Math.random): number {
  const st = seriesStats(rets);
  return Math.min(bootstrapUp(rets, weekBlocks, 2000, rnd), st.greenLB);
}

export function gradeBundle(b: Pick<Bundle, "train" | "test" | "holdout">, testBlocks: number): Grade {
  const h = b.holdout;
  if (!(h.pct > 0)) return "F";
  if (
    b.train.greenLB >= 0.5
    && b.test.green >= Math.max(1, testBlocks - 1)
    && h.legsTrading === h.legs
    && h.confidence >= 0.5
    && h.drawdown > -10
  ) return "A";
  if (b.train.greenLB >= 0.35) return "B";
  return "C";
}

/** Score the drafts on the holdout. This is the ONLY place holdout data
    touches a bundle, and it cannot change which legs are in it. */
export function proveBundles(
  drafts: Draft[],
  feeds: BasketFeeds,
  s: ForgeSettings,
  now: number,
  seed = 1,
): Bundle[] {
  const rnd = mulberry32(seed);
  const tIdx = testIdx(s);
  const all = [...trainIdx(s), ...tIdx];
  const holdDays = s.testBlocks * s.blockDays;
  const weekBlocks = Math.max(1, Math.round(7 / s.blockDays));
  return drafts.map((d, i) => {
    const legs: BasketLeg[] = d.legs.map((c) => ({ address: c.address, allocationUsd: s.legUsd }));
    const run = runBasketSim(legs, feeds, { days: holdDays, asOf: now });
    const p = run.portfolio;
    const testRets = bundleSeries(d.legs.map((c) => c.blocks), tIdx);
    const test = { ...seriesStats(testRets), rets: testRets };
    const bundle: Bundle = {
      id: `b${i + 1}`,
      legs: d.legs.map((c) => {
        const sl = run.sleeves.find((x) => x.address === c.address);
        return {
          address: c.address,
          usd: s.legUsd,
          trainGreen: `${c.train.green}/${c.train.n}`,
          trainMean: c.train.mean,
          trainWorst: c.train.worst,
          trainTrades: c.trainTrades,
          holdoutPct: sl ? sl.pct : 0,
          holdoutTrades: sl ? sl.trades : 0,
          ...(sl?.note ? { note: sl.note } : {}),
          topMarkets: c.blocks.topMarkets,
        };
      }),
      totalUsd: s.legUsd * legs.length,
      train: d.train,
      test,
      holdout: {
        days: holdDays,
        net: p.net,
        pct: p.pct,
        drawdown: p.drawdown,
        trades: p.trades,
        legsTrading: p.legsTrading,
        legs: p.legs,
        confidence: p.confidence,
        idleUsd: p.idleUsd,
      },
      pWeekUp: pWeekUp(bundleSeries(d.legs.map((c) => c.blocks), all), weekBlocks, rnd),
      grade: "F",
      gradeText: "",
    };
    bundle.grade = gradeBundle(bundle, s.testBlocks);
    bundle.gradeText = GRADE_TEXT[bundle.grade];
    return bundle;
  });
}

/** Best first: grade, then the TRAIN objective. Never the holdout number —
    sorting on it would re-select on the test set and quietly un-hold it. */
export function rankBundles(bundles: Bundle[]): Bundle[] {
  const order: Record<Grade, number> = { A: 0, B: 1, C: 2, F: 3 };
  return [...bundles]
    .sort((a, b) => order[a.grade] - order[b.grade] || b.train.objective - a.train.objective)
    .map((b, i) => ({ ...b, id: `b${i + 1}` }));
}
