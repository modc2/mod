// Standalone smoke test for the ONE Strat class. Runs with:
//   cd src/app && npx tsx app/lib/strats/__test__.ts
// Verifies behavior, not types — proves the runtime contract holds.

import {
  Strat,
  TraderTrade,
  StratHistory,
  SizeConstraints,
  emptyHistory,
  clobMinNotional,
  POLYMARKET_MIN_SHARES,
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
    ...over,
  };
}
function buildHistory(over: Partial<StratHistory> = {}): StratHistory {
  return { ...emptyHistory(1000), now: Date.now(), ...over };
}
const H = buildHistory();

console.log("\n── scoreCandidate (expected profit) ──");
{
  const s = new Strat();
  // EP = roi × rawMirror, rawMirror = notional × copyRatio = 250 × 0.05 = 12.5.
  // roi 0.20 → EP = 0.20 × 12.5 = $2.50.
  const trade = buildTrade({ notional: 250 });
  const stats = buildStats({ roi: 0.20 });
  check("EP = roi × mirror$", s.scoreCandidate(trade, stats, H) === 2.5, `got ${s.scoreCandidate(trade, stats, H)}`);
  check("no stats → 0", s.scoreCandidate(trade, null, H) === 0);
  check("negative roi → negative EP", s.scoreCandidate(trade, buildStats({ roi: -0.1 }), H) < 0);
  check("sampleSize does not gate EP", s.scoreCandidate(trade, buildStats({ sampleSize: 2 }), H) === 2.5);
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
  const s = new Strat();
  // At 50¢, 5-share floor = $2.50. Raw mirror $1.00 should clamp up to $2.50.
  const trade = buildTrade({ price: 0.50, notional: 200, copyRatio: 0.005 });
  const c: SizeConstraints = { userFloor: 0.5, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c, H);
  check("clobFloor at 50¢ = $2.50", c.clobFloor === 2.50, `got ${c.clobFloor}`);
  check("clamped up to clobFloor", d.mirrorNotional === 2.50, `got ${d.mirrorNotional}`);
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
  check("default passes everything", s.shouldMirror(buildTrade(), H) === true);
  check("pure mirror strat proposes nothing", s.propose(H, {} as SizeConstraints).length === 0);
  check("pure mirror strat: proposes() false", s.proposes() === false);
  const noMirror = new Strat({ mirror: false });
  check("mirror:false gates every trade", noMirror.shouldMirror(buildTrade(), H) === false);
  const filtered = new Strat({ tradeFilters: { sides: "buy" } });
  check("tradeFilters gate SELL", filtered.shouldMirror(buildTrade({ side: "SELL" }), H) === false);
  check("tradeFilters pass BUY", filtered.shouldMirror(buildTrade({ side: "BUY" }), H) === true);
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

console.log(`\n${failed === 0 ? "✓ all checks passed" : `✗ ${failed} failed`}\n`);
process.exit(failed === 0 ? 0 : 1);
