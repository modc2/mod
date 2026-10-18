// Tests for THE COPY STUDIO — pick traders, allocate to each, simulate.
//
//   cd src/app && npx tsx app/lib/__test_copy_studio__.ts
//
// Pins the allocation editing (add twice never doubles, $ is cleaned), the
// splits (equal, fit-to-bankroll keeps conviction, by-result funds only
// winners), and that the per-trader preview and the whole-allocation run are
// the SAME replay the basket engine gives — a trader's preview number must
// not change when other traders are added beside it.

import {
  DEFAULT_LEG_USD, EMPTY_STUDIO, addLeg, cleanUsd, fitToBankroll, parseStudio,
  previewKey, removeLeg, setLegUsd, simulateAllocation, simulateTrader,
  splitByResult, splitEqual, toggleLeg, totalUsd, verdict, weights,
  type StudioLeg,
} from "./copyStudio";
import { replaySleeve, summarizeSleeve, type BasketFeeds, type Sleeve } from "./basketSim";
import type { PolymarketTrade } from "./types";
import { MAIN_TABS, activeTab } from "./navTabs";
import {
  DEFAULT_SCORE_WEIGHTS, cleanWeights, parseWeights, rankByScore, resampleEquity, smoothness, steadyScore,
} from "./copyScore";

let failures = 0;
function ok(cond: unknown, msg: string): void {
  if (cond) { console.log(`  [PASS] ${msg}`); return; }
  failures++;
  console.log(`  [FAIL] ${msg}`);
}
function near(a: number, b: number, tol: number, msg: string): void {
  ok(Math.abs(a - b) <= tol, `${msg} — got ${a.toFixed(4)}, want ~${b.toFixed(4)}`);
}

const A = "0x2222222222222222222222222222222222222222";
const B = "0x3333333333333333333333333333333333333333";
const C = "0x4444444444444444444444444444444444444444";
const HOUR = 3600_000;

let seq = 0;
function trade(o: { side: "BUY" | "SELL"; price: number; size: number; hoursAgo: number; conditionId: string; market: string }): PolymarketTrade {
  return {
    id: `t${seq++}`, market: o.market, conditionId: o.conditionId, side: o.side,
    price: o.price, size: o.size, pnl: 0, timestamp: Date.now() - o.hoursAgo * HOUR, outcome: "Yes",
  };
}

/** Six profitable out-of-window round trips — the strat prices its edge off
    past returns, so a leader with no record executes nothing. */
function record(tag: string): PolymarketTrade[] {
  const out: PolymarketTrade[] = [];
  for (let i = 0; i < 6; i++) {
    const cid = `0x${tag}past${i}`;
    out.push(trade({ side: "BUY", price: 0.5, size: 100, hoursAgo: 24 * (20 - i), conditionId: cid, market: `${tag} past ${i}` }));
    out.push(trade({ side: "SELL", price: 0.7, size: 100, hoursAgo: 24 * (20 - i) - 1, conditionId: cid, market: `${tag} past ${i}` }));
  }
  return out;
}

/** A winner: 20 in-window BUYs, two markets sold at 60¢. */
function winnerFeed(): PolymarketTrade[] {
  const out = record("wa");
  for (let i = 0; i < 20; i++) {
    out.push(trade({ side: "BUY", price: 0.5, size: 200, hoursAgo: 6 - i * 0.2, conditionId: `0xwamkt${i % 4}`, market: `a market ${i % 4}` }));
  }
  for (const m of [0, 1]) {
    out.push(trade({ side: "SELL", price: 0.6, size: 1000, hoursAgo: 1, conditionId: `0xwamkt${m}`, market: `a market ${m}` }));
  }
  return out;
}

/** A small leader: one round trip, in at 50¢ out at 60¢. */
function smallFeed(): PolymarketTrade[] {
  const out = record("sb");
  out.push(trade({ side: "BUY", price: 0.5, size: 200, hoursAgo: 6, conditionId: "0xwamkt0", market: "a market 0" }));
  out.push(trade({ side: "SELL", price: 0.6, size: 200, hoursAgo: 3, conditionId: "0xwamkt0", market: "a market 0" }));
  return out;
}

const feeds: BasketFeeds = {
  trades: new Map([[A, winnerFeed()], [B, smallFeed()]]),
  positions: new Map([[A, []], [B, []]]),
  bankrolls: new Map([[A, 100_000], [B, 5_000]]),
};

console.log("\n─ editing the allocation ─");
{
  let legs: StudioLeg[] = [];
  legs = addLeg(legs, { address: A.toUpperCase().replace("0X", "0x"), usd: 250 });
  ok(legs.length === 1 && legs[0].address === A, "addresses are lowercased on add");
  legs = addLeg(legs, { address: A, usd: 999 });
  ok(legs.length === 1 && legs[0].usd === 250, "adding the same trader twice never doubles or resizes them");
  legs = addLeg(legs, { address: "not-an-address", usd: 10 });
  ok(legs.length === 1, "a junk address is refused");
  legs = addLeg(legs, { address: B, usd: 0 });
  ok(legs[1].usd === DEFAULT_LEG_USD, `a trader added with no $ gets the default $${DEFAULT_LEG_USD}`);
  legs = setLegUsd(legs, B, -5);
  ok(legs[1].usd === 0, "a negative amount is cleaned to $0");
  legs = setLegUsd(legs, B, 12.345);
  ok(legs[1].usd === 12.35, `amounts round to cents — got ${legs[1].usd}`);
  ok(totalUsd(legs) === 262.35, `total is the sum of the legs — got ${totalUsd(legs)}`);
  legs = toggleLeg(legs, { address: B, usd: 1 });
  ok(legs.length === 1, "toggle removes a selected trader");
  legs = toggleLeg(legs, { address: B, usd: 40 });
  ok(legs.length === 2 && legs[1].usd === 40, "toggle adds an unselected trader with its amount");
  legs = removeLeg(legs, A.toUpperCase().replace("0X", "0x"));
  ok(legs.length === 1 && legs[0].address === B, "remove matches case-insensitively");
  ok(cleanUsd("abc") === 0 && cleanUsd(Infinity) === 0 && cleanUsd("7.5") === 7.5, "cleanUsd tolerates junk input");
}

console.log("\n─ splits ─");
{
  const legs: StudioLeg[] = [
    { address: A, usd: 300 },
    { address: B, usd: 100 },
    { address: C, usd: 0 },
  ];
  const eq = splitEqual(legs, 900);
  ok(eq.every((l) => l.usd === 300), `equal split gives everyone the same — got ${eq.map((l) => l.usd)}`);

  const fit = fitToBankroll(legs, 800);
  ok(fit[0].usd === 600 && fit[1].usd === 200 && fit[2].usd === 0,
    `fit keeps the 3:1 conviction at the new size — got ${fit.map((l) => l.usd)}`);
  ok(totalUsd(fit) === 800, "fit sums to the bankroll");

  const fitEmpty = fitToBankroll(legs.map((l) => ({ ...l, usd: 0 })), 300);
  ok(fitEmpty.every((l) => l.usd === 100), "fit with nothing set falls back to equal");

  const w = weights(legs);
  near(w.get(A) ?? 0, 0.75, 1e-9, "A holds 75% of the allocation");
  near(w.get(C) ?? 0, 0, 1e-9, "a $0 leg holds nothing");

  const sleeves = new Map<string, Sleeve>([
    [A, { trades: 5, pct: 30 } as Sleeve],
    [B, { trades: 3, pct: 10 } as Sleeve],
    [C, { trades: 0, pct: 0 } as Sleeve],
  ]);
  const byRes = splitByResult(legs, 1000, sleeves);
  ok(byRes[0].usd === 750 && byRes[1].usd === 250 && byRes[2].usd === 0,
    `by-result funds winners in proportion to return, idle gets $0 — got ${byRes.map((l) => l.usd)}`);
  const allLosers = new Map<string, Sleeve>([[A, { trades: 5, pct: -3 } as Sleeve]]);
  ok(splitByResult(legs, 900, allLosers).every((l) => l.usd === 300), "by-result with no winner falls back to equal");
}

console.log("\n─ simulation: preview == allocation sleeve ─");
{
  const a: StudioLeg = { address: A, usd: 1000 };
  const b: StudioLeg = { address: B, usd: 120 };
  const solo = simulateTrader(a, feeds, 7);
  ok(solo.trades > 0, `the winner is copyable at $1,000 — ${solo.trades} trades`);
  ok(solo.allocationUsd === 1000 && solo.weight === 1, "a preview is its own whole portfolio (weight 1)");

  const direct = summarizeSleeve({ address: A, allocationUsd: 1000 }, replaySleeve({ address: A, allocationUsd: 1000 }, feeds, { days: 7 }), 1000);
  near(solo.net, direct.net, 1e-9, "the preview IS the basket engine's sleeve, not a lookalike");

  const run = simulateAllocation([a, b], feeds, 7);
  ok(run.sleeves.length === 2, "both traders replayed");
  near(run.sleeves[0].net, solo.net, 1e-9, "a trader's result does not change when others are added beside it");
  near(run.portfolio.net, run.sleeves.reduce((s, x) => s + x.net, 0), 1e-9, "portfolio P&L is the sum of the sleeves");
  ok(run.portfolio.capital === 1120, `portfolio capital is the allocated total — got ${run.portfolio.capital}`);
  near(run.sleeves[0].weight, 1000 / 1120, 1e-9, "sleeve weight is its share of the allocation");

  const withZero = simulateAllocation([a, { address: B, usd: 0 }], feeds, 7);
  ok(withZero.sleeves.length === 1, "a $0 leg is not replayed");

  const missing = simulateTrader({ address: C, usd: 100 }, feeds, 7);
  ok(missing.trades === 0 && !!missing.note, `a trader with no feed is an honest empty, not break-even — "${missing.note}"`);
  ok(verdict(missing, 7).tone === "idle", "an idle sleeve reads as idle");
  ok(verdict(null, 7).tone === "loading", "no result yet reads as loading");
  const v = verdict(solo, 7);
  ok(v.tone === (solo.net > 0 ? "up" : solo.net < 0 ? "down" : "flat") && v.text.includes("copied trade"),
    `the winner's verdict is in words — "${v.text}"`);
}

console.log("\n─ preview cache key + persistence ─");
{
  const k1 = previewKey({ address: A, usd: 100 }, 7);
  ok(k1 === previewKey({ address: A.toUpperCase().replace("0X", "0x"), usd: 100 }, 7), "key ignores address case");
  ok(k1 !== previewKey({ address: A, usd: 200 }, 7), "key changes with $");
  ok(k1 !== previewKey({ address: A, usd: 100 }, 30), "key changes with window");
  ok(k1 !== previewKey({ address: A, usd: 100, marketQuery: "nba" }, 7), "key changes with topic gate");

  ok(parseStudio(null) === EMPTY_STUDIO, "nothing stored → empty studio");
  ok(parseStudio("{not json") === EMPTY_STUDIO, "junk stored → empty studio");
  const round = parseStudio(JSON.stringify({
    legs: [{ address: A, usd: 50, marketQuery: "bitcoin" }, { address: A, usd: 70 }, { address: "x" }, null],
    days: 30, bankroll: 2500,
  }));
  ok(round.legs.length === 1 && round.legs[0].usd === 50 && round.legs[0].marketQuery === "bitcoin",
    "stored legs are deduped and validated");
  ok(round.days === 30 && round.bankroll === 2500, "window and bankroll survive");
  ok(parseStudio(JSON.stringify({ days: 9, bankroll: -1 })).days === EMPTY_STUDIO.days, "an unsupported window falls back");
}

console.log("\n─ nav: COPY is the front door ─");
{
  ok(MAIN_TABS[0].label === "COPY" && MAIN_TABS[0].href === "/copytrade", "COPY is the first tab");
  ok(activeTab("/copytrade") === "/copytrade", "/copytrade lights COPY");
  ok(activeTab("/copy") === "/copy" && activeTab("/copy/basket") === "/copy" && activeTab("/copy/0xabc") === "/copy", "/copy/* lights DESK");
  ok(activeTab("/strats") === "/strats", "/strats lights STRATS");
  ok(activeTab("/traders/0xabc") === "/traders" && activeTab("/markets") === "/traders", "everything else lights TRADERS");
}

console.log("\n─ STEADY score: consistency over luck, disclosed, user-weighted ─");
{
  const curve = (ys: number[]) => ys.map((v, i) => ({ t: i * HOUR, liq: v, pos: 0 }));
  const sleeve = (o: Partial<Sleeve>): Sleeve => ({
    address: A, label: "a", allocationUsd: 100, weight: 1, net: 0, pct: 0, endEquity: 100, volume: 0,
    fees: 0, feeBps: 0, trades: 20, observed: 20, executed: 20, skipped: 0, ratio: 1, drawdown: 0,
    confidence: 1, markets: [], equity: [], markers: [], ...o,
  });
  ok(smoothness([1, 2, 3, 4, 5]) > 0.99, "a straight climb is perfectly smooth");
  ok(smoothness([5, 4, 3, 2, 1]) === 0, "a straight fall scores 0, not R²=1");
  ok(smoothness([1, 1, 1, 1, 1]) === 0, "a flat line has nothing to judge");
  ok(smoothness([100, 100, 100, 100, 100, 100, 100, 130]) < 0.5, "one late spike is not smooth");
  ok(resampleEquity(curve([1, 2, 3])).length === 24, "equity is resampled to even time steps");

  const stair = sleeve({ pct: 10, net: 10, drawdown: -2, equity: curve([100, 102, 104, 106, 108, 110]) });
  const spike = sleeve({ pct: 30, net: 30, drawdown: -25, equity: curve([100, 85, 80, 78, 80, 130]) });
  const s1 = steadyScore(stair).score!, s2 = steadyScore(spike).score!;
  ok(s1 > s2, `a steady +10% outranks a lucky +30% under default weights (${s1} vs ${s2})`);
  const greedy = { smooth: 0, shallow: 0, ret: 100, sample: 0, settled: 0 };
  ok(steadyScore(spike, greedy).score! > steadyScore(stair, greedy).score!, "weights are the user's: RETURN-only flips it");
  ok(steadyScore(sleeve({ trades: 0 })).score === null, "an idle trader has no score (not 0)");
  ok(steadyScore(null).score === null, "a missing replay has no score");
  ok(steadyScore(stair, { smooth: 0, shallow: 0, ret: 0, sample: 0, settled: 0 }).score === s1, "all-zero weights fall back to the defaults");
  const few = steadyScore(sleeve({ ...stair, trades: 2 })).score!;
  ok(few < s1, "a thin sample scores lower than the same curve on 20 trades");
  const sc = steadyScore(stair).score!;
  ok(sc >= 0 && sc <= 100, "score is 0–100");

  ok(JSON.stringify(rankByScore(["a", "b", "c", "d"], (x) => ({ a: null, b: 40, c: 80, d: null } as Record<string, number | null>)[x]))
    === JSON.stringify(["c", "b", "a", "d"]), "rank: best first, unscored last in their incoming order");
  ok(cleanWeights({ smooth: 500, shallow: -3, ret: "x" }).smooth === 100 && cleanWeights({ shallow: -3 }).shallow === 0,
    "weights are clamped 0–100");
  ok(JSON.stringify(parseWeights("not json")) === JSON.stringify(DEFAULT_SCORE_WEIGHTS), "junk storage → default weights");
}

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
