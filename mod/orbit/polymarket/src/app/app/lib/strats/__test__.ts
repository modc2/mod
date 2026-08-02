// Standalone smoke test for the ONE Strat class. Runs with:
//   cd src/app && npx tsx app/lib/strats/__test__.ts
// Verifies behavior, not types — proves the runtime contract holds.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  Strat,
  TraderTrade,
  StratHistory,
  SizeConstraints,
  MarketPriceSeries,
  seriesMomentum,
  candleSlug,
  emptyHistory,
  clobMinNotional,
  POLYMARKET_MIN_SHARES,
  statsFromReturns,
  successProbability,
  stopLossTriggered,
  takeProfitTriggered,
  copyRatioFor,
  rankTraders,
  DEFAULT_FILTER_TOP_N,
  DEFAULT_STOP_LOSS,
  DEFAULT_TAKE_PROFIT,
  DEFAULT_MAX_UPSCALE,
  REBALANCE_MARGIN_PCT,
} from "./strat";
import type { TraderRoiStats, PolymarketPosition } from "../types";

let failed = 0;
function check(label: string, ok: boolean, detail?: string) {
  const tag = ok ? "PASS" : "FAIL";
  console.log(`  [${tag}] ${label}${detail ? " — " + detail : ""}`);
  if (!ok) failed++;
}

function buildTrade(over: Partial<TraderTrade> = {}): TraderTrade {
  return {
    id: "t1", market: "MarketX", conditionId: "cid", timestamp: Date.now(),
    side: "BUY", price: 0.50, size: 100, pnl: 0,
    trader: "0xaaa", weight: 1, weightFraction: 1, copyRatio: 0.05,
    notional: 50, ...over,
  };
}
function buildStats(over: Partial<TraderRoiStats> = {}): TraderRoiStats {
  return {
    address: "0xaaa", windowDays: 30, roi: 0.20, stdev: 0.10,
    sampleSize: 10, sharpe: 2.0, cashDeployed: 1000, syncedAt: Date.now(),
    // 6 wins of 10 closed → Laplace-smoothed P = (6+2)/(10+4) = 4/7.
    wins: 6, successProb: 8 / 14,
    ...over,
  };
}
function buildHistory(over: Partial<StratHistory> = {}): StratHistory {
  return { ...emptyHistory(1000), now: Date.now(), ...over };
}
const H = buildHistory();

console.log("\n── scoreCandidate (probability-weighted expected profit) ──");
{
  const s = new Strat();
  // score = P(success) × roi × rawMirror; rawMirror = notional × copyRatio =
  // 250 × 0.05 = 12.5. Fixture P = 4/7 (6 wins / 10 closed), roi 0.20 →
  // score = 4/7 × $2.50 ≈ $1.43. Mirrors live_engine.rs `candidate_score`.
  const trade = buildTrade({ notional: 250 });
  const stats = buildStats({ roi: 0.20 });
  const P = 8 / 14;
  check("score = P × roi × mirror$", Math.abs(s.scoreCandidate(trade, stats, H) - P * 2.5) < 1e-9, `got ${s.scoreCandidate(trade, stats, H)}`);
  check("no stats → 0", s.scoreCandidate(trade, null, H) === 0);
  check("negative roi → negative score (P never flips sign)", s.scoreCandidate(trade, buildStats({ roi: -0.1 }), H) < 0);
  check("sampleSize does not gate score", Math.abs(s.scoreCandidate(trade, buildStats({ sampleSize: 2 }), H) - P * 2.5) < 1e-9);
  // Stale cached stats without a successProb read as the neutral 0.5 prior —
  // a candidate must never be zeroed out by a pre-upgrade localStorage cache.
  const stale = buildStats({ roi: 0.20 });
  delete (stale as Partial<TraderRoiStats>).successProb;
  check("stale cache → 0.5 prior", Math.abs(s.scoreCandidate(trade, stale, H) - 0.5 * 2.5) < 1e-9, `got ${s.scoreCandidate(trade, stale, H)}`);
}

console.log("\n── sizeAndPrice — sub-$1 mirror clamps UP (no skip) ──");
{
  const s = new Strat();
  // Tiny proportional mirror below the $1 user floor.
  // At 10¢, clobFloor = max($1, 5×0.10=$0.50) = $1. rawMirror = 50 × 0.01 = $0.50.
  const trade = buildTrade({ price: 0.10, notional: 50, copyRatio: 0.01 });
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: clobMinNotional(0.10), capital: 1000 };
  const d = s.sizeAndPrice(trade, c, H);
  check("clamped up to $1 (not skipped)", d.mirrorNotional === 1, `got ${d.mirrorNotional}`);
  check("reason says clamped up", !!d.reason?.includes("clamped up"), `reason: ${d.reason}`);
}

console.log("\n── sizeAndPrice — CLOB 5-share floor ──");
{
  // At 50¢, 5-share floor = $2.50. Raw mirror $1.00 is 2.5× under it — past
  // the default fidelity limit, so the default strat refuses rather than
  // placing an order 2.5× the size proportionality asked for.
  const trade = buildTrade({ price: 0.50, notional: 200, copyRatio: 0.005 });
  const c: SizeConstraints = { userFloor: 0.5, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  check("clobFloor at 50¢ = $2.50", c.clobFloor === 2.50, `got ${c.clobFloor}`);

  const refused = new Strat().sizeAndPrice(trade, c, H);
  check("2.5× upscale is refused by default", refused.mirrorNotional === 0, `got ${refused.mirrorNotional}`);
  check("reason says SUB_SCALE", !!refused.reason?.startsWith("SUB_SCALE"), `reason: ${refused.reason}`);

  // maxUpscale: null opts back into the legacy unbounded clamp.
  const d = new Strat({ maxUpscale: null }).sizeAndPrice(trade, c, H);
  check("clamped up to clobFloor when unbounded", d.mirrorNotional === 2.50, `got ${d.mirrorNotional}`);
  check("reason mentions CLOB", !!d.reason?.includes("CLOB"), `reason: ${d.reason}`);
  // size = ceil(2.50 / 0.50) = 5 → matches CLOB minimum exactly
  check("resulting shares ≥ 5", Math.ceil(d.mirrorNotional / 0.50) >= POLYMARKET_MIN_SHARES);
}

console.log("\n── sizeAndPrice — ceiling below CLOB floor ──");
{
  const s = new Strat();
  // At 80¢, clobFloor = max($1, 5 × 0.80) = $4. Ceiling=$2 → no legal size.
  const trade = buildTrade({ price: 0.80, notional: 200, copyRatio: 0.02 });
  const c: SizeConstraints = { userFloor: 0.5, userCeiling: 2, clobFloor: clobMinNotional(0.80), capital: 1000 };
  const d = s.sizeAndPrice(trade, c, H);
  check("clobFloor at 80¢ = $4", c.clobFloor === 4);
  check("returns 0 notional", d.mirrorNotional === 0);
  check("reason = CEILING_BELOW_CLOB_FLOOR", !!d.reason?.startsWith("CEILING_BELOW_CLOB_FLOOR"));
}

console.log("\n── sizeAndPrice — leader dust ──");
{
  const s = new Strat();
  // Leader's OWN trade ($2.00) is below CLOB floor ($2.50), and our
  // mirror would clear userFloor but still fall below clobFloor.
  // Expect LEADER_DUST skip (no real signal to mirror).
  const trade = buildTrade({ price: 0.50, size: 4, notional: 2.0, copyRatio: 1.0 });
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c, H);
  check("returns 0 notional", d.mirrorNotional === 0);
  check("reason = LEADER_DUST", !!d.reason?.startsWith("LEADER_DUST"), `got: ${d.reason}`);
}

console.log("\n── sizeAndPrice — happy path + slippage widening ──");
{
  const s = new Strat();
  // Big trade: raw mirror $10, well above floor, below ceiling. No clamp.
  const trade = buildTrade({ price: 0.50, notional: 1000, copyRatio: 0.01 });
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c, H);
  check("no clamp, mirror = $10", d.mirrorNotional === 10);
  check("no reason set", d.reason === undefined);
  check("limitPrice widened up for BUY", d.limitPrice > 0.50);
  const dSell = s.sizeAndPrice(buildTrade({ side: "SELL", price: 0.50, notional: 1000, copyRatio: 0.01 }), c, H);
  check("limitPrice widened down for SELL", dSell.limitPrice < 0.50);
  const exact = new Strat({ slippageBps: 0 });
  const dExact = exact.sizeAndPrice(trade, c, H);
  check("slippageBps: 0 quotes leader price", dExact.limitPrice === 0.50, `got ${dExact.limitPrice}`);
}

console.log("\n── shouldMirror + propose defaults ──");
{
  const s = new Strat();
  // No explicit price band ⇒ BUYs face the likely-to-win default floor
  // (DEFAULT_MIN_ENTRY_PRICE, 60¢); SELLs (exits) always pass.
  check("default passes ≥60¢ BUY", s.shouldMirror(buildTrade({ price: 0.65 }), H) === true);
  check("default blocks sub-60¢ BUY", s.shouldMirror(buildTrade(), H) === false); // 50¢
  check("default never floors SELL", s.shouldMirror(buildTrade({ side: "SELL", price: 0.05 }), H) === true);
  const optOut = new Strat({ tradeFilters: { minPrice: 0 } });
  check("explicit minPrice:0 opts out of floor", optOut.shouldMirror(buildTrade(), H) === true);
  const longshots = new Strat({ tradeFilters: { maxPrice: 0.2 } });
  check("explicit maxPrice band skips floor", longshots.shouldMirror(buildTrade({ price: 0.1 }), H) === true);
  check("pure mirror strat proposes nothing", s.propose(H, {} as SizeConstraints).length === 0);
  check("pure mirror strat: proposes() false", s.proposes() === false);
  const noMirror = new Strat({ mirror: false });
  check("mirror:false gates every trade", noMirror.shouldMirror(buildTrade({ price: 0.65 }), H) === false);
  const filtered = new Strat({ tradeFilters: { sides: "buy" } });
  check("tradeFilters gate SELL", filtered.shouldMirror(buildTrade({ side: "SELL", price: 0.65 }), H) === false);
  check("tradeFilters pass BUY", filtered.shouldMirror(buildTrade({ side: "BUY", price: 0.65 }), H) === true);
}

console.log("\n── FILTER — only the top-ranked traders get copied ──");
{
  // Three watched traders, wildly different quality. The strat keeps the top 2
  // by expected edge per dollar (P × ROI), so the bleeder's fills are observed
  // but never mirrored.
  const good = buildStats({ address: "0xaaa", roi: 0.30, sharpe: 3.0, sampleSize: 10, wins: 8, successProb: 10 / 14 });
  const ok = buildStats({ address: "0xbbb", roi: 0.05, sharpe: 1.0, sampleSize: 10, wins: 6, successProb: 8 / 14 });
  const bad = buildStats({ address: "0xccc", roi: -0.20, sharpe: -2.0, sampleSize: 10, wins: 2, successProb: 4 / 14 });
  const hist = buildHistory({
    traderStats: { "0xaaa": good, "0xbbb": ok, "0xccc": bad },
    watchlist: [
      { address: "0xaaa", weight: 1 },
      { address: "0xbbb", weight: 1 },
      { address: "0xccc", weight: 1 },
    ],
  });
  const s = new Strat({ filter: { topN: 2 }, tradeFilters: { minPrice: 0 } });
  check("top-ranked trader passes", s.shouldMirror(buildTrade({ trader: "0xaaa" }), hist) === true);
  check("2nd-ranked trader passes", s.shouldMirror(buildTrade({ trader: "0xbbb" }), hist) === true);
  check("bottom trader is filtered out", s.shouldMirror(buildTrade({ trader: "0xccc" }), hist) === false);
  check("case-insensitive on address", s.shouldMirror(buildTrade({ trader: "0xAAA" }), hist) === true);
  check("skipReason names the rank", /rank 3 of 3/.test(s.skipReason(buildTrade({ trader: "0xccc" }), hist)));
  check("ranking() is best-first", s.ranking(hist).map((r) => r.address).join(",") === "0xaaa,0xbbb,0xccc");
  check("no filter ⇒ no ranking, no gate",
    new Strat({ tradeFilters: { minPrice: 0 } }).ranking(hist).length === 0 &&
    new Strat({ tradeFilters: { minPrice: 0 } }).shouldMirror(buildTrade({ trader: "0xccc" }), hist) === true);

  // A metric switch changes who survives: 0xbbb is steadier (sharpe 1.0 on a
  // 5% ROI) than nobody here, but ranking on winRate reorders nothing —
  // ranking on roi keeps the same top 2 for a different reason. The point of
  // the check is that the metric is actually read.
  const bySharpe = new Strat({ filter: { metric: "sharpe", topN: 1 }, tradeFilters: { minPrice: 0 } });
  check("metric: sharpe keeps only the best Sharpe",
    bySharpe.shouldMirror(buildTrade({ trader: "0xaaa" }), hist) === true &&
    bySharpe.shouldMirror(buildTrade({ trader: "0xbbb" }), hist) === false);

  // Thresholds cut inside the top N: minScore demands positive expected edge.
  const positiveOnly = new Strat({ filter: { topN: 0, minScore: 0.1 }, tradeFilters: { minPrice: 0 } });
  check("minScore floor cuts a positive-but-thin edge",
    positiveOnly.shouldMirror(buildTrade({ trader: "0xaaa" }), hist) === true &&
    positiveOnly.shouldMirror(buildTrade({ trader: "0xbbb" }), hist) === false);

  // minSamples: a 2-trade record is noise no matter how good it looks.
  const thin = buildStats({ address: "0xddd", roi: 0.9, sampleSize: 2, wins: 2, successProb: 4 / 6 });
  const thinHist = buildHistory({
    traderStats: { "0xaaa": good, "0xddd": thin },
    watchlist: [{ address: "0xaaa", weight: 1 }, { address: "0xddd", weight: 1 }],
  });
  const seasoned = new Strat({ filter: { topN: 2, minSamples: 5 }, tradeFilters: { minPrice: 0 } });
  check("minSamples cuts a thin record even at rank 1",
    seasoned.shouldMirror(buildTrade({ trader: "0xddd" }), thinHist) === false &&
    seasoned.shouldMirror(buildTrade({ trader: "0xaaa" }), thinHist) === true);

  // An unranked trader (stats haven't loaded yet) sorts on the neutral prior —
  // below anyone with proven edge, above a proven loser.
  const unranked = buildHistory({
    traderStats: { "0xccc": bad },
    watchlist: [{ address: "0xccc", weight: 1 }, { address: "0xeee", weight: 1 }],
  });
  const top1 = new Strat({ filter: { topN: 1 }, tradeFilters: { minPrice: 0 } });
  check("unranked trader outranks a proven loser",
    top1.shouldMirror(buildTrade({ trader: "0xeee" }), unranked) === true &&
    top1.shouldMirror(buildTrade({ trader: "0xccc" }), unranked) === false);
}

console.log("\n── flow origination — history in, trades out ──");
{
  const s = new Strat({ mirror: false, flow: { lookbackMinutes: 90, minTraders: 2, minFlowUsd: 50 } });
  check("flow strat: proposes() true", s.proposes() === true);
  const now = Date.now();
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: 1, capital: 1000 };
  // Consensus: two distinct traders net-BUY the same market inside the window.
  const consensus = buildHistory({
    now,
    trades: [
      buildTrade({ id: "a", conditionId: "0xm1", market: "M1", trader: "0xaaa", notional: 40, price: 0.40, timestamp: now - 10 * 60_000 }),
      buildTrade({ id: "b", conditionId: "0xm1", market: "M1", trader: "0xbbb", notional: 40, price: 0.42, timestamp: now - 5 * 60_000 }),
    ],
  });
  const props = s.propose(consensus, c);
  check("consensus flow → 1 BUY proposal", props.length === 1 && props[0].side === "BUY", `got ${props.length}`);
  check("proposal chases last print", props.length === 1 && props[0].limitPrice === 0.44, `got ${props[0]?.limitPrice}`);
  check("proposal sized into trade band", props.length === 1 && props[0].notional >= 1 && props[0].notional <= 100);

  // Single trader → below minTraders, no proposal.
  const solo = buildHistory({
    now,
    trades: [buildTrade({ id: "a", conditionId: "0xm1", market: "M1", trader: "0xaaa", notional: 400, timestamp: now - 10 * 60_000 })],
  });
  check("solo flow → no proposal", s.propose(solo, c).length === 0);

  // Held position + flow flipped net-SELL → SELL exit.
  const pos: PolymarketPosition = {
    conditionId: "0xm1", tokenId: "1", market: "M1", outcome: "Yes",
    size: 10, avgPrice: 0.40, currentPrice: 0.45, value: 4.5, pnlUsd: 0.5,
    negRisk: false, redeemable: false,
  };
  const flipped = buildHistory({
    now,
    positions: [pos],
    trades: [
      buildTrade({ id: "a", conditionId: "0xm1", market: "M1", trader: "0xaaa", side: "SELL", notional: 60, timestamp: now - 10 * 60_000 }),
      buildTrade({ id: "b", conditionId: "0xm1", market: "M1", trader: "0xbbb", side: "SELL", notional: 60, timestamp: now - 5 * 60_000 }),
    ],
  });
  const exits = s.propose(flipped, c);
  check("flipped flow → SELL exit", exits.length === 1 && exits[0].side === "SELL", `got ${exits.length}`);
  check("never mirrors per-trade", s.shouldMirror(buildTrade(), H) === false);
  // flow: {} alone turns origination on with defaults.
  const bare = new Strat({ flow: {} });
  check("flow:{} → proposes() true", bare.proposes() === true);
}

console.log("\n── price-momentum origination — rising odds in, BUY out ──");
{
  const s = new Strat({ momentum: { lookbackMinutes: 60, minRiseCents: 5 } });
  check("momentum strat: proposes() true", s.proposes() === true);
  check("momentum strat: wantsMarketPrices() true", s.wantsMarketPrices() === true);
  check("plain strat: wantsMarketPrices() false", new Strat().wantsMarketPrices() === false);

  const now = Date.now();
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: 1, capital: 1000 };
  const mk = (over: Partial<MarketPriceSeries> = {}): MarketPriceSeries => ({
    conditionId: "0xbtc",
    market: "Bitcoin above $130k this week?",
    outcomes: ["Yes", "No"],
    tokenIds: ["111", "222"],
    endDateMs: now + 3 * 24 * 60 * 60_000,
    // 50¢ ninety minutes ago → 60¢ now: the user's canonical example.
    points: [
      { t: now - 90 * 60_000, p: 0.50 },
      { t: now - 60 * 60_000, p: 0.50 },
      { t: now - 30 * 60_000, p: 0.55 },
      { t: now, p: 0.60 },
    ],
    ...over,
  });

  // 50¢ → 60¢ over the lookback ⇒ BUY the rising Yes with its exact token id.
  const props = s.propose(buildHistory({ now, marketPrices: [mk()] }), c);
  check("50¢→60¢ → 1 BUY proposal", props.length === 1 && props[0].side === "BUY", `got ${props.length}`);
  check("buys the RISING outcome (Yes)", props[0]?.outcome === "Yes");
  check("carries the series token id", props[0]?.tokenId === "111");
  check("chases 2¢ past the last print", props[0]?.limitPrice === 0.62, `got ${props[0]?.limitPrice}`);
  check("reason shows the move", !!props[0]?.reason?.includes("50¢→60¢"), `got: ${props[0]?.reason}`);
  check("sized into the trade band", props[0]?.notional >= 1 && props[0]?.notional <= 100);

  // A FALLING series is a rising No — buy the other side.
  const falling = mk({ points: mk().points.map(({ t, p }) => ({ t, p: 1 - p })) });
  const noProps = s.propose(buildHistory({ now, marketPrices: [falling] }), c);
  check("falling Yes → BUY No instead", noProps.length === 1 && noProps[0].outcome === "No" && noProps[0].tokenId === "222", `got ${JSON.stringify(noProps[0])}`);

  // Below the rise threshold → nothing.
  const flat = mk({ points: [{ t: now - 90 * 60_000, p: 0.50 }, { t: now, p: 0.52 }] });
  check("+2¢ < minRiseCents → no proposal", s.propose(buildHistory({ now, marketPrices: [flat] }), c).length === 0);

  // Outside the price band (rose into near-certainty) → no chase.
  const rich = mk({ points: [{ t: now - 90 * 60_000, p: 0.82 }, { t: now, p: 0.92 }] });
  check("rise into >85¢ band → no proposal", s.propose(buildHistory({ now, marketPrices: [rich] }), c).length === 0);

  // Series that starts inside the window (new market) can't fake a rise.
  const young = mk({ points: [{ t: now - 20 * 60_000, p: 0.50 }, { t: now, p: 0.60 }] });
  check("partial-window series → no proposal", s.propose(buildHistory({ now, marketPrices: [young] }), c).length === 0);

  // Market resolving within minMinutesToClose (default 90) → skipped.
  const closing = mk({ endDateMs: now + 30 * 60_000 });
  check("sub-90-min-to-close market → no proposal", s.propose(buildHistory({ now, marketPrices: [closing] }), c).length === 0);

  // Held rising outcome whose price now FELL over the window → SELL exit.
  const pos: PolymarketPosition = {
    conditionId: "0xbtc", tokenId: "111", market: "Bitcoin above $130k this week?", outcome: "Yes",
    size: 10, avgPrice: 0.60, currentPrice: 0.52, value: 5.2, pnlUsd: -0.8,
    negRisk: false, redeemable: false,
  };
  const dropped = mk({ points: [{ t: now - 90 * 60_000, p: 0.60 }, { t: now - 60 * 60_000, p: 0.60 }, { t: now, p: 0.52 }] });
  const exits = s.propose(buildHistory({ now, marketPrices: [dropped], positions: [pos] }), c);
  check("momentum flipped → SELL exit", exits.length === 1 && exits[0].side === "SELL", `got ${exits.length}`);
  check("exit reason says flipped", !!exits[0]?.reason?.includes("MOMENTUM FLIPPED"), `got: ${exits[0]?.reason}`);

  // seriesMomentum guards: terminal prices and empty series.
  check("seriesMomentum: resolved (p=1) → null",
    seriesMomentum([{ t: now - 90 * 60_000, p: 0.9 }, { t: now, p: 1 }], now, 60 * 60_000) === null);
  check("seriesMomentum: single point → null", seriesMomentum([{ t: now, p: 0.5 }], now, 60 * 60_000) === null);

  // momentum: {} alone turns origination on with defaults.
  check("momentum:{} → proposes() true", new Strat({ momentum: {} }).proposes() === true);
}

console.log("\n── candle-mode momentum — the BTC 5-MIN DELTA rules ──");
{
  // The template's exact params: ≥60¢ side, ±5¢ delta over 2 minutes,
  // one position, entries need ≥1 minute of candle left.
  const s = new Strat({ momentum: {
    candles: { slugPrefix: "btc-updown-5m", periodMinutes: 5 },
    lookbackMinutes: 2, minRiseCents: 5, exitDropCents: 5,
    minPrice: 0.6, maxPrice: 0.9, maxPositions: 1, minMinutesToClose: 1,
  } });
  check("candle strat: proposes() true", s.proposes() === true);
  check("candle strat: wantsMarketPrices() true", s.wantsMarketPrices() === true);

  const now = 1_784_659_200_000; // a real 5-min boundary (2:40PM ET candle)
  const c: SizeConstraints = { userFloor: 1, userCeiling: 10, clobFloor: 1, capital: 100 };
  const mk = (pts: [number, number][], endInSec = 180): MarketPriceSeries => ({
    conditionId: "0xcandle",
    market: "Bitcoin Up or Down - July 21, 2:40PM-2:45PM ET",
    outcomes: ["Up", "Down"],
    tokenIds: ["tokUp", "tokDown"],
    endDateMs: now + endInSec * 1000,
    points: pts.map(([dtSec, p]) => ({ t: now + dtSec * 1000, p })),
  });

  // Up 57¢ → 64¢ over the 2-minute lookback: +7¢ ≥ +5¢ at ≥60¢ → BUY Up.
  const rising = mk([[-180, 0.55], [-120, 0.57], [-60, 0.60], [0, 0.64]]);
  let props = s.propose(buildHistory({ now, marketPrices: [rising] }), c);
  check("≥60¢ + positive ≥5¢ delta → BUY Up",
    props.length === 1 && props[0].side === "BUY" && props[0].outcome === "Up" && props[0].tokenId === "tokUp",
    JSON.stringify(props.map((p) => `${p.side} ${p.outcome}`)));

  // 64¢ but only +3¢ over the window — inside the 5% band, no trade.
  const slow = mk([[-180, 0.61], [-120, 0.61], [-60, 0.62], [0, 0.64]]);
  check("≥60¢ but delta under 5¢ → no entry",
    s.propose(buildHistory({ now, marketPrices: [slow] }), c).length === 0);

  // Up decaying 40¢→33¢ IS Down climbing 60¢→67¢ — negative delta on one
  // side means buy the other (the sellable expression of "delta negative").
  const downRising = mk([[-180, 0.42], [-120, 0.40], [-60, 0.36], [0, 0.33]]);
  props = s.propose(buildHistory({ now, marketPrices: [downRising] }), c);
  check("negative Up delta → BUY Down (complement)",
    props.length === 1 && props[0].outcome === "Down" && props[0].tokenId === "tokDown",
    JSON.stringify(props.map((p) => `${p.side} ${p.outcome}`)));

  // Holding Up when its delta flips −5¢ → SELL exit.
  const pos: PolymarketPosition = {
    conditionId: "0xcandle", tokenId: "tokUp",
    market: "Bitcoin Up or Down - July 21, 2:40PM-2:45PM ET", outcome: "Up",
    size: 15, avgPrice: 0.64, currentPrice: 0.60, value: 9, pnlUsd: -0.6,
    negRisk: false, redeemable: false,
  };
  const falling = mk([[-180, 0.70], [-120, 0.68], [-60, 0.64], [0, 0.60]]);
  const exits = s.propose(buildHistory({ now, marketPrices: [falling], positions: [pos] }), c);
  check("held side's delta flips −5¢ → SELL exit",
    exits.length === 1 && exits[0].side === "SELL" && exits[0].outcome === "Up",
    JSON.stringify(exits.map((p) => `${p.side} ${p.outcome}`)));

  // Inside the final minute there's no time to act — entry vetoed.
  const late = mk([[-180, 0.55], [-120, 0.57], [-60, 0.60], [0, 0.64]], 30);
  check("under 1 minute to candle close → no entry",
    s.propose(buildHistory({ now, marketPrices: [late] }), c).length === 0);

  // Deterministic candle addressing: slug floors to the live candle's start.
  check("candleSlug floors mid-candle to the start boundary",
    candleSlug("btc-updown-5m", 5, 1_784_659_300_000) === "btc-updown-5m-1784659200",
    candleSlug("btc-updown-5m", 5, 1_784_659_300_000));
  check("candleSlug rolls over exactly at the boundary",
    candleSlug("btc-updown-5m", 5, 1_784_659_500_000) === "btc-updown-5m-1784659500");
}

console.log("\n── params are the whole configuration ──");
{
  const s = new Strat({ name: "MY STRAT", maxPerCycle: 7 });
  check("maxPerCycle threaded through params", s.maxPerCycle() === 7);
  check("params echoed on the instance", s.params.maxPerCycle === 7);
  check("name echoed from params", s.name === "MY STRAT");
  check("default name = strat", new Strat().name === "strat");
  check("default maxPerCycle = 3", new Strat().maxPerCycle() === 3);
}

console.log("\n── Modularity probe: subclass override still works ──");
{
  // Drop-in custom strat — the standard path is params on the one class,
  // but overriding a hook and handing the instance to CopyEngine remains
  // supported for behavior no param expresses.
  class FixedSizeStrat extends Strat {
    scoreCandidate(): number { return 100; } // always copy
    sizeAndPrice(trade: TraderTrade): ReturnType<Strat["sizeAndPrice"]> {
      return { mirrorNotional: 5, limitPrice: trade.price };
    }
  }
  const s = new FixedSizeStrat({ name: "fixed_size" });
  check("subclass scoreCandidate fires", s.scoreCandidate() === 100);
  const d = s.sizeAndPrice(buildTrade());
  check("subclass sizeAndPrice fires", d.mirrorNotional === 5);
  check("inherited maxPerCycle still works", s.maxPerCycle() === 3);
  // The capability probe the engine uses to gate Phase 4.
  class Originator extends Strat {
    propose(): ReturnType<Strat["propose"]> { return []; }
  }
  check("propose override → proposes() true", new Originator().proposes() === true);
  check("plain subclass → proposes() false", new FixedSizeStrat().proposes() === false);
}

console.log("\n── Cross-language playbook parity (parity.fixture.json) ──");
{
  // The same fixture is asserted by `cargo test` in live_engine.rs — if the
  // TS and Rust playbook math ever drift, one of the two suites goes red.
  const fixturePath = fileURLToPath(new URL("./parity.fixture.json", import.meta.url));
  const fx = JSON.parse(readFileSync(fixturePath, "utf8"));
  const close = (a: number, b: number) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b));

  for (const c of fx.statsCases) {
    const got = statsFromReturns(c.returns);
    const exp = c.expected;
    check(
      `stats[${c.name}] roi/stdev/sharpe/wins/P match fixture`,
      close(got.roi, exp.roi) && close(got.stdev, exp.stdev) && close(got.sharpe, exp.sharpe) &&
        got.sampleSize === exp.sampleSize && got.wins === exp.wins && close(got.successProb, exp.successProb),
      `got P=${got.successProb} roi=${got.roi}, want P=${exp.successProb} roi=${exp.roi}`,
    );
  }

  const strat = new Strat();
  for (const c of fx.scoreCases) {
    const s = statsFromReturns(c.returns);
    const stats = buildStats({ ...s });
    const trade = buildTrade({ notional: c.notional, copyRatio: c.copyRatio });
    const got = strat.scoreCandidate(trade, stats, buildHistory());
    check(
      `score[${c.name}] = P × roi × mirror$ matches fixture`,
      close(got, c.expectedScore),
      `got ${got}, want ${c.expectedScore}`,
    );
  }

  for (const c of fx.stopLossCases) {
    check(
      `stopLoss(entry=${c.entry}, mark=${c.mark}, sl=${c.stopLoss}) → ${c.hit}`,
      stopLossTriggered(c.entry, c.mark, c.stopLoss) === c.hit,
    );
  }

  for (const c of fx.takeProfitCases) {
    check(
      `takeProfit(mark=${c.mark}, tp=${c.takeProfit}) → ${c.hit}`,
      takeProfitTriggered(c.mark, c.takeProfit) === c.hit,
    );
  }

  // Sizing pipeline — sizeAndPrice must reproduce the fixture the Rust
  // engine's plan_mirror also asserts: same clamps, same skip classes,
  // same slippage-widened tick-rounded limit price. This is what makes
  // "what you backtest is what live places" true beyond the score math.
  for (const c of fx.sizingCases) {
    const s = new Strat({ slippageBps: c.slippageBps, maxUpscale: c.maxUpscale });
    const trade = buildTrade({
      side: c.side, price: c.price, size: c.leaderNotional / c.price,
      notional: c.leaderNotional, copyRatio: c.copyRatio,
    });
    const d = s.sizeAndPrice(trade, {
      userFloor: c.userFloor, userCeiling: c.ceiling,
      clobFloor: clobMinNotional(c.price), capital: 1000,
    }, buildHistory());
    const skip = d.mirrorNotional <= 0
      ? (d.reason?.startsWith("CEILING") ? "CEILING"
        : d.reason?.startsWith("LEADER_DUST") ? "DUST"
        : d.reason?.startsWith("SUB_SCALE") ? "SUB_SCALE" : "OTHER")
      : null;
    const exp = c.expected;
    check(
      `sizing[${c.name}] matches fixture`,
      skip === exp.skip &&
        (exp.skip !== null || (close(d.mirrorNotional, exp.notional) && close(d.limitPrice, exp.limitPrice))),
      `got skip=${skip} notional=${d.mirrorNotional} limit=${d.limitPrice}, want ${JSON.stringify(exp)}`,
    );
  }

  // Copy ratio — the number that decides how big every mirror is. The two
  // languages agreeing on it IS the guarantee that a backtest predicts the
  // position sizes the live engine will actually take.
  for (const c of fx.copyRatioCases) {
    const got = copyRatioFor(c.accountValue, c.weightFraction, c.leaderBankroll, c.capitalAlloc, c.traderVol);
    check(
      `copyRatio[${c.name}] — ${c.why}`,
      close(got, c.expected),
      `got ${got}, want ${c.expected}`,
    );
  }

  // Trader FILTER — the ranking that decides WHO gets copied. Rust
  // (`select_top_traders`) reads the same cases; a drift here means the
  // console's preview of "these 5 are being copied" stops being true live.
  {
    const stats: Record<string, TraderRoiStats> = {};
    const addresses: string[] = [];
    for (const t of fx.traderFilterTraders as { address: string; returns: number[] }[]) {
      addresses.push(t.address);
      stats[t.address] = {
        address: t.address, windowDays: 30, cashDeployed: 0, syncedAt: 0,
        ...statsFromReturns(t.returns),
      };
    }
    for (const c of fx.traderFilterCases) {
      const rows = rankTraders(addresses, stats, c.filter);
      const order = rows.map((r) => r.address);
      const kept = rows.filter((r) => r.kept).map((r) => r.address);
      check(
        `traderFilter[${c.name}] ranking matches fixture`,
        JSON.stringify(order) === JSON.stringify(c.expectedOrder),
        `got ${JSON.stringify(order)}`,
      );
      check(
        `traderFilter[${c.name}] kept set matches fixture`,
        JSON.stringify(kept) === JSON.stringify(c.expectedKept),
        `got ${JSON.stringify(kept)}`,
      );
    }
    check("DEFAULT_FILTER_TOP_N matches fixture", DEFAULT_FILTER_TOP_N === fx.defaults.filterTopN);
  }

  check("REBALANCE_MARGIN_PCT matches fixture", REBALANCE_MARGIN_PCT === fx.rebalanceMarginPct);
  check("DEFAULT_STOP_LOSS matches fixture", DEFAULT_STOP_LOSS === fx.defaults.stopLoss);
  check("DEFAULT_TAKE_PROFIT matches fixture", DEFAULT_TAKE_PROFIT === fx.defaults.takeProfit);
  check("DEFAULT_MAX_UPSCALE matches fixture", DEFAULT_MAX_UPSCALE === fx.defaults.maxUpscale);
  check("successProbability falls back to 0.5 on stale cached stats",
    successProbability({ ...buildStats(), successProb: undefined as unknown as number }) === 0.5 &&
    successProbability(null) === 0.5);
}

console.log(`\n${failed === 0 ? "✓ all checks passed" : `✗ ${failed} failed`}\n`);
process.exit(failed === 0 ? 0 : 1);
