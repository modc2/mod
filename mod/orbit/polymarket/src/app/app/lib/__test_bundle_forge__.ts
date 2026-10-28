// Tests for THE BUNDLE FORGE (lib/bundleForge.ts) — the BUNDLER agent's engine.
//
//   cd src/app && npx tsx app/lib/__test_bundle_forge__.ts
//
// The property that matters most is pinned first: selection never reads the
// holdout. Rewriting every trader's holdout blocks must not change which legs
// any bundle gets, nor their order. Then: the screen's rejects, the greedy
// search preferring legs that cover each other's red blocks, the overlap
// rule, grading, ranking-without-holdout, and an end-to-end run through the
// real replay on synthetic feeds.

import {
  DEFAULT_FORGE, GREEN_EPS, blockWindows, bootstrapUp, bundleSeries, cleanForge, gradeBundle,
  mulberry32, objective, pWeekUp, proveBundles, rankBundles, screen, searchBundles, selectionSkill, seriesStats,
  traderBlocks,
  type Bundle, type ForgeSettings, type TraderBlocks,
} from "./bundleForge";
import type { BasketFeeds } from "./basketSim";
import type { PolymarketTrade } from "./types";

let failures = 0;
function ok(cond: unknown, msg: string): void {
  if (cond) { console.log(`  [PASS] ${msg}`); return; }
  failures++;
  console.log(`  [FAIL] ${msg}`);
}

const S: ForgeSettings = { ...DEFAULT_FORGE, minTrainBlocks: 5, minTrainTrades: 5 };
const addr = (n: number) => `0x${n.toString(16).padStart(40, "0")}`;

/** A trader whose block returns are given most-recent-first (index 0 = b0). */
function tb(n: number, rets: number[], o: Partial<TraderBlocks> = {}): TraderBlocks {
  return {
    address: addr(n),
    covered: rets.map(() => true),
    ret: rets,
    trades: rets.map(() => 3),
    confidence: rets.map(() => 1),
    trainMarkets: [`m${n}a`, `m${n}b`],
    topMarkets: [`market ${n}`],
    activeRecently: true,
    ...o,
  };
}
// 10 blocks: [b0 b1 b2 | b3 … b9] — first 3 are holdout under DEFAULT_FORGE.
const H = [0.01, 0.01, 0.01];

console.log("\n─ settings ─");
{
  const c = cleanForge({ blockDays: 5, blocks: 99, testBlocks: 50, maxLegs: 3, minLegs: 9, legUsd: -4 });
  ok(c.blocks === 6, `blocks capped to the 30d feed (5d × 6) — got ${c.blocks}`);
  ok(c.testBlocks === 4, `holdout leaves ≥2 train blocks — got ${c.testBlocks}`);
  ok(c.minLegs === 3, `minLegs ≤ maxLegs — got ${c.minLegs}`);
  ok(c.legUsd === 10, `legUsd floored at $10 — got ${c.legUsd}`);
  const d = cleanForge(null);
  ok(d.blocks === 10 && d.blockDays === 3 && d.testBlocks === 3, "defaults: 10 × 3-day blocks, 3 held out");
  const w = blockWindows(1_000_000_000_000, d);
  ok(w[0].end === 1_000_000_000_000 && w[0].holdout && !w[3].holdout, "b0 ends now and is holdout; b3 is train");
  ok(w[9].start === 1_000_000_000_000 - 30 * 86400_000, "the oldest block starts 30 days back");
}

console.log("\n─ series stats + objective ─");
{
  const steady = seriesStats([0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]);
  const lumpy = seriesStats([0.2, -0.02, -0.02, -0.02, -0.02, -0.02, -0.02]);
  ok(steady.green === 7 && steady.greenLB > 0.7, `7/7 green → lower bound ${steady.greenLB.toFixed(3)} > 0.7`);
  ok(steady.objective > lumpy.objective, "a steady grinder outranks one lucky block with the same-ish total");
  ok(seriesStats([0.0005, 0.0005]).green === 0, `flat (≤ ${GREEN_EPS}) is not green`);
  const deep = objective(0.8, 0.01, 0.01, -0.15);
  const shallow = objective(0.8, 0.01, 0.01, -0.04);
  ok(shallow - deep > 19, `a −15% block costs ~20 pts of tail penalty — got ${(shallow - deep).toFixed(1)}`);
  ok(seriesStats([]).objective === -Infinity, "no evidence ranks last");
}

console.log("\n─ the screen ─");
{
  const good = tb(1, [...H, 0.01, 0.02, 0.01, 0.015, 0.01, 0.01, 0.02]);
  const shortHist = tb(2, [...H, 0.01, 0.01, 0.01, 0.01, 0, 0, 0], { covered: [...H.map(() => true), true, true, true, true, false, false, false] });
  const idle = tb(3, [...H, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01], { trades: Array(10).fill(0) });
  const gone = tb(4, [...H, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01], { activeRecently: false });
  const loser = tb(5, [...H, -0.01, -0.02, 0.01, -0.01, -0.01, 0, 0]);
  const blowup = tb(6, [...H, 0.1, 0.1, 0.1, -0.3, 0.1, 0.1, 0.1]);
  const marked = tb(7, [...H, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05], { confidence: Array(10).fill(0.1) });
  const c = screen([loser, gone, idle, shortHist, good, blowup, marked], S);
  const why = (n: number) => c.find((x) => x.address === addr(n))?.reject ?? "";
  ok(c[0].address === addr(1) && !c[0].reject, "the steady trader survives and sorts first");
  ok(/history covers 4\/7/.test(why(2)), `short history is named — "${why(2)}"`);
  ok(/copied trades/.test(why(3)), `a leader the desk can't copy is cut — "${why(3)}"`);
  ok(/last block/.test(why(4)), `a wallet that went quiet is cut — "${why(4)}"`);
  ok(/mean/.test(why(5)), `a train loser is cut — "${why(5)}"`);
  ok(/lost -30%/.test(why(6)), `a −30% train block is cut even with a green mean — "${why(6)}"`);
  ok(/unsettled marks \(10% settled\)/.test(why(7)), `a record that is mostly open marks is cut — "${why(7)}"`);
}

console.log("\n─ the search ─");
{
  // X and Y alternate: each is red when the other is green. Together every
  // block is green — the search must find that pairing over a lone grinder.
  const x = tb(10, [...H, 0.04, -0.01, 0.04, -0.01, 0.04, -0.01, 0.04]);
  const y = tb(11, [...H, -0.01, 0.04, -0.01, 0.04, -0.01, 0.04, -0.01]);
  const z = tb(12, [...H, 0.004, 0.003, 0.004, 0.002, 0.004, 0.003, 0.004]);
  // Floor off: this pins the greedy mechanism itself. (At the default floor
  // X and Y — 4/7 green each — are refused as legs; see the next section.)
  const s2: ForgeSettings = { ...S, minLegs: 2, maxLegs: 2, bundles: 3, minLegGreenLB: 0 };
  const drafts = searchBundles(screen([x, y, z], s2), s2);
  const pair = drafts.find((d) => d.legs.some((l) => l.address === addr(10)) && d.legs.some((l) => l.address === addr(11)));
  ok(!!pair, "the complementary pair X+Y is found");
  ok(pair && pair.train.green === 7, `X+Y is green in every train block — got ${pair?.train.green}/7`);
  ok(drafts[0] === pair, "and it is the top draft");
  const series = bundleSeries([x, y], [3, 4]);
  ok(Math.abs(series[0] - 0.015) < 1e-12, "bundle block return = mean of leg returns (equal $, sleeves additive)");

  // Overlap: a clone trading the same markets is the same bet twice.
  const clone = tb(13, x.ret, { trainMarkets: x.trainMarkets });
  const d2 = searchBundles(screen([x, clone, y], s2), s2);
  ok(d2.every((d) => !(d.legs.some((l) => l.address === addr(10)) && d.legs.some((l) => l.address === addr(13)))),
    "two legs trading the same markets are never bundled together");

  // THE property: holdout blocks never steer selection.
  const pool = [x, y, z, tb(14, [...H, 0.01, 0.01, 0.02, 0.01, -0.005, 0.01, 0.01]),
    tb(15, [...H, 0.02, 0.0, 0.01, 0.01, 0.01, 0.02, 0.01])];
  const s3: ForgeSettings = { ...S, minLegs: 2, maxLegs: 4, bundles: 4 };
  const before = searchBundles(screen(pool, s3), s3).map((d) => d.legs.map((l) => l.address).join(","));
  const poisoned = pool.map((t, i) => ({ ...t, ret: [i % 2 ? 0.5 : -0.5, -0.4, 0.9, ...t.ret.slice(3)] }));
  const after = searchBundles(screen(poisoned, s3), s3).map((d) => d.legs.map((l) => l.address).join(","));
  ok(before.length > 0 && JSON.stringify(before) === JSON.stringify(after),
    "rewriting every holdout block changes NO bundle and NO order (selection is train-only)");
}

console.log("\n─ per-leg consistency floor ─");
{
  // W has a great mean but is green only 4/7 — on its own, not consistent.
  const w = tb(20, [...H, 0.08, -0.01, 0.08, -0.01, 0.08, -0.01, 0.0]);
  const v = tb(21, [...H, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]);
  const u = tb(22, [...H, 0.01, 0.02, 0.01, 0.01, 0.01, 0.02, 0.01]);
  const s4: ForgeSettings = { ...S, minLegs: 2, maxLegs: 3 };
  const legs = searchBundles(screen([w, v, u], s4), s4).flatMap((d) => d.legs.map((l) => l.address));
  ok(!legs.includes(addr(20)), "a leg green in only 4/7 train blocks never enters a bundle at the default floor");
  const loose = { ...s4, minLegs: 3, maxLegs: 3, minLegGreenLB: 0 };
  ok(searchBundles(screen([w, v, u], loose), loose).some((d) => d.legs.some((l) => l.address === addr(20))),
    "…and does with the floor at 0 (the knob works)");
}

console.log("\n─ leg reuse cap ─");
{
  // One flawless low-variance wallet + many decent ones: without the cap it
  // would sit in every bundle.
  const star = tb(50, [...H, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005]);
  const rnd = mulberry32(9);
  const others = [1, 2, 3, 4, 5, 6, 7, 8].map((n) =>
    tb(50 + n, [...H, ...Array.from({ length: 7 }, (_, i) => (i === n % 7 ? -0.004 : 0.002 + rnd() * 0.02))]));
  const s5: ForgeSettings = { ...S, minLegs: 2, maxLegs: 3, bundles: 5, minLegGreenLB: 0, maxLegReuse: 2 };
  const ds = searchBundles(screen([star, ...others], s5), s5);
  const counts = new Map<string, number>();
  for (const d of ds) for (const l of d.legs) counts.set(l.address, (counts.get(l.address) ?? 0) + 1);
  ok(ds.length >= 2, `still returns several bundles — got ${ds.length}`);
  ok([...counts.values()].every((c) => c <= 2), `no wallet in more than 2 bundles — max ${Math.max(...counts.values())}`);
}

console.log("\n─ selection skill (reporting only) ─");
{
  const good = [1, 2, 3].map((n) => tb(30 + n, [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]));
  const bad = [1, 2].map((n) => tb(40 + n, [-0.01, -0.01, -0.01, -0.01, -0.02, 0.0, -0.01, 0.0, -0.01, 0.0]));
  const sk = selectionSkill(screen([...good, ...bad], S), S, 2);
  ok(sk.top.n === 2 && sk.top.pUp === 1 && sk.rest.n === 1, "top-2 kept traders counted, rest separately");
  ok(sk.rejected.n === 2 && sk.rejected.pUp === 0, "rejects (with enough history) form the baseline");
  ok(Math.abs(sk.top.mean - 0.01) < 1e-12, "mean = per-block holdout return");
}

console.log("\n─ grading + ranking ─");
{
  const base = {
    train: seriesStats([0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]),
    test: { ...seriesStats([0.01, 0.01, 0.01]), rets: [0.01, 0.01, 0.01] },
    holdout: { days: 9, net: 10, pct: 1, drawdown: -2, trades: 20, legsTrading: 3, legs: 3, confidence: 0.8, idleUsd: 0 },
  };
  ok(gradeBundle(base, 3) === "A", "green holdout + steady train + all legs trading = A");
  ok(gradeBundle({ ...base, holdout: { ...base.holdout, pct: -0.1 } }, 3) === "F", "a red holdout is F whatever the train says");
  ok(gradeBundle({ ...base, holdout: { ...base.holdout, legsTrading: 2 } }, 3) === "B", "an idle leg blocks A");
  ok(gradeBundle({ ...base, holdout: { ...base.holdout, confidence: 0.3 } }, 3) === "B", "mostly-marked (unsettled) result blocks A");
  const weak = { ...base, train: seriesStats([0.02, -0.01, 0.02, -0.01, 0.0, 0.0, 0.01]) };
  ok(gradeBundle(weak, 3) === "C", `weak train consistency (LB ${weak.train.greenLB.toFixed(2)}) is C`);

  const mk = (grade: Bundle["grade"], obj: number, hold: number): Bundle => ({
    id: "x", legs: [], totalUsd: 0, pWeekUp: 0, grade, gradeText: "",
    train: { ...base.train, objective: obj }, test: base.test, holdout: { ...base.holdout, pct: hold },
  });
  const r = rankBundles([mk("B", 90, 50), mk("A", 10, 0.1), mk("A", 20, 0.2), mk("F", 99, -1)]);
  ok(r.map((b) => b.grade).join("") === "AABF", "grade first");
  ok(r[0].train.objective === 20 && r[2].holdout.pct === 50, "then TRAIN objective — a big holdout number buys no rank");
  ok(r.map((b) => b.id).join() === "b1,b2,b3,b4", "ids follow the final order");
}

console.log("\n─ bootstrap ─");
{
  const a = bootstrapUp([0.01, -0.005, 0.02], 2, 500, mulberry32(42));
  const b = bootstrapUp([0.01, -0.005, 0.02], 2, 500, mulberry32(42));
  ok(a === b, "same seed, same answer — a run's P(up) is reproducible");
  ok(bootstrapUp([0.01, 0.02], 3, 200, mulberry32(1)) === 1, "all-green blocks → P(up) = 1");
  ok(bootstrapUp([-0.01, -0.02], 3, 200, mulberry32(1)) === 0, "all-red blocks → P(up) = 0");
  ok(bootstrapUp([], 3) === 0, "no blocks → 0, never NaN");
  const ten = Array(10).fill(0.01);
  const p = pWeekUp(ten, 2, mulberry32(3));
  ok(p < 0.85 && Math.abs(p - seriesStats(ten).greenLB) < 1e-12,
    `10/10 green blocks claims the Wilson bound (${p.toFixed(2)}), not 100%`);
}

console.log("\n─ end to end through the real replay ─");
{
  const HOUR = 3600_000;
  const now = Date.now();
  let seq = 0;
  const t = (side: "BUY" | "SELL", price: number, size: number, hoursAgo: number, cid: string): PolymarketTrade => ({
    id: `e${seq++}`, market: `mkt ${cid}`, conditionId: cid, side, price, size, pnl: 0,
    timestamp: now - hoursAgo * HOUR, outcome: "Yes",
  });
  // A grinder: a winning round trip every ~12h for 31 days.
  const grinder: PolymarketTrade[] = [];
  for (let h = 31 * 24; h > 2; h -= 12) {
    grinder.push(t("BUY", 0.5, 200, h, `g${h}`), t("SELL", 0.6, 200, h - 2, `g${h}`));
  }
  // A newcomer whose feed only reaches back 5 days.
  const fresh: PolymarketTrade[] = [];
  for (let h = 5 * 24; h > 2; h -= 12) fresh.push(t("BUY", 0.5, 200, h, `f${h}`), t("SELL", 0.6, 200, h - 2, `f${h}`));
  const G = addr(100), N = addr(101);
  const feeds: BasketFeeds = {
    trades: new Map([[G, grinder], [N, fresh]]),
    positions: new Map([[G, []], [N, []]]),
    bankrolls: new Map([[G, 10_000], [N, 10_000]]),
  };
  const s: ForgeSettings = { ...DEFAULT_FORGE, minLegs: 1, maxLegs: 2, minTrainTrades: 1 };
  const blocks = blockWindows(now, s);
  const g = traderBlocks(G, feeds, blocks, s);
  const n = traderBlocks(N, feeds, blocks, s);
  ok(g.covered.every(Boolean), "a 31-day feed covers all ten blocks");
  ok(n.covered.filter(Boolean).length === 1, `a 5-day feed covers only b0 — got ${n.covered.filter(Boolean).length}`);
  ok(n.ret.slice(1).every((r) => r === 0) && n.trades.slice(1).every((x) => x === 0), "uncovered blocks are cash, not losses");
  ok(g.activeRecently && g.topMarkets.length > 0, "recent activity + train market titles are recorded");
  const cands = screen([g, n], s);
  ok(!!cands.find((c) => c.address === N)?.reject, "the newcomer can't qualify on 0 train blocks");
  const proven = proveBundles(searchBundles(cands, s), feeds, s, now, 7);
  ok(proven.length >= 1 && proven[0].legs[0].address === G, "the grinder is bundled");
  ok(proven[0].holdout.days === 9 && Number.isFinite(proven[0].holdout.pct), "the holdout is one 9-day replay with a real number");
  ok(["A", "B", "C", "F"].includes(proven[0].grade) && proven[0].gradeText.length > 0, `graded (${proven[0].grade})`);
}

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
