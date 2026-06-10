// Standalone smoke test for the Strat surface. Runs with:
//   cd src/app && npx tsx app/lib/strats/__test__.ts
// Verifies behavior, not types — proves the runtime contract holds.

import { CopyTrader } from "./copytrader";
import { getStrat, listStratNames, DEFAULT_STRAT } from "./registry";
import { TraderTrade, SizeConstraints, clobMinNotional, POLYMARKET_MIN_SHARES } from "./base";
import type { TraderRoiStats } from "../types";

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

console.log("\n── CopyTrader.scoreCandidate ──");
{
  const s = new CopyTrader();
  const trade = buildTrade({ notional: 250 });
  const stats = buildStats({ sharpe: 1.5 });
  check("Sharpe × notional", s.scoreCandidate(trade, stats) === 375);
  check("no stats → 0", s.scoreCandidate(trade, null) === 0);
  check("sampleSize < 3 → 0", s.scoreCandidate(trade, buildStats({ sampleSize: 2 })) === 0);
  check("negative sharpe → 0", s.scoreCandidate(trade, buildStats({ sharpe: -1 })) === 0);
}

console.log("\n── CopyTrader.sizeAndPrice — CLOB 5-share floor ──");
{
  const s = new CopyTrader();
  // At 50¢, 5-share floor = $2.50. Raw mirror $1.00 should clamp up to $2.50.
  const trade = buildTrade({ price: 0.50, notional: 200, copyRatio: 0.005 });
  const c: SizeConstraints = { userFloor: 0.5, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c);
  check("clobFloor at 50¢ = $2.50", c.clobFloor === 2.50, `got ${c.clobFloor}`);
  check("clamped up to clobFloor", d.mirrorNotional === 2.50, `got ${d.mirrorNotional}`);
  check("reason mentions CLOB min", !!d.reason?.includes("CLOB min"), `reason: ${d.reason}`);
  // size = ceil(2.50 / 0.50) = 5 → matches CLOB minimum exactly
  check("resulting shares ≥ 5", Math.ceil(d.mirrorNotional / 0.50) >= POLYMARKET_MIN_SHARES);
}

console.log("\n── CopyTrader.sizeAndPrice — ceiling below CLOB floor ──");
{
  const s = new CopyTrader();
  // At 80¢, clobFloor = max($1, 5 × 0.80) = $4. Ceiling=$2 → no legal size.
  const trade = buildTrade({ price: 0.80, notional: 200, copyRatio: 0.02 });
  const c: SizeConstraints = { userFloor: 0.5, userCeiling: 2, clobFloor: clobMinNotional(0.80), capital: 1000 };
  const d = s.sizeAndPrice(trade, c);
  check("clobFloor at 80¢ = $4", c.clobFloor === 4);
  check("returns 0 notional", d.mirrorNotional === 0);
  check("reason = CEILING_BELOW_CLOB_FLOOR", !!d.reason?.startsWith("CEILING_BELOW_CLOB_FLOOR"));
}

console.log("\n── CopyTrader.sizeAndPrice — leader dust ──");
{
  const s = new CopyTrader();
  // Leader's OWN trade ($2.00) is below CLOB floor ($2.50), and our
  // mirror would clear userFloor but still fall below clobFloor.
  // Expect LEADER_DUST skip (no real signal to mirror).
  const trade = buildTrade({ price: 0.50, size: 4, notional: 2.0, copyRatio: 1.0 });
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c);
  check("returns 0 notional", d.mirrorNotional === 0);
  check("reason = LEADER_DUST", !!d.reason?.startsWith("LEADER_DUST"), `got: ${d.reason}`);
}

console.log("\n── CopyTrader.sizeAndPrice — happy path ──");
{
  const s = new CopyTrader();
  // Big trade: raw mirror $10, well above floor, below ceiling. No clamp.
  const trade = buildTrade({ price: 0.50, notional: 1000, copyRatio: 0.01 });
  const c: SizeConstraints = { userFloor: 1, userCeiling: 100, clobFloor: clobMinNotional(0.50), capital: 1000 };
  const d = s.sizeAndPrice(trade, c);
  check("no clamp, mirror = $10", d.mirrorNotional === 10);
  check("no reason set", d.reason === undefined);
  check("limitPrice widened up for BUY", d.limitPrice > 0.50);
}

console.log("\n── CopyTrader.shouldMirror ──");
{
  const s = new CopyTrader();
  check("default passes everything", s.shouldMirror(buildTrade()) === true);
  class NoSellMirror extends CopyTrader {
    shouldMirror(t: TraderTrade): boolean { return t.side !== "SELL"; }
  }
  const f = new NoSellMirror();
  check("override filters SELL", f.shouldMirror(buildTrade({ side: "SELL" })) === false);
  check("override passes BUY", f.shouldMirror(buildTrade({ side: "BUY" })) === true);
}

console.log("\n── Registry ──");
{
  const names = listStratNames();
  check("DEFAULT_STRAT in registry", names.includes(DEFAULT_STRAT));
  const s1 = getStrat(DEFAULT_STRAT, { maxPerCycle: 7 });
  check("getStrat returns instance", !!s1);
  check("maxPerCycle threaded through opts", s1.maxPerCycle() === 7);
  const s2 = getStrat("does-not-exist" as never, { maxPerCycle: 3 });
  check("unknown name falls back to DEFAULT", s2.name === "copytrader");
}

console.log("\n── Modularity probe: subclass override ──");
{
  // Drop-in custom strat — prove the contract allows overriding any one
  // method without touching the rest.
  class FixedSizeStrat extends CopyTrader {
    readonly name = "fixed_size";
    scoreCandidate(_trade: TraderTrade, _stats: TraderRoiStats | null): number { return 100; } // always copy
    sizeAndPrice(trade: TraderTrade, _c: SizeConstraints): ReturnType<CopyTrader["sizeAndPrice"]> {
      return { mirrorNotional: 5, limitPrice: trade.price };
    }
  }
  const s = new FixedSizeStrat();
  check("subclass scoreCandidate fires", s.scoreCandidate(buildTrade(), null) === 100);
  const d = s.sizeAndPrice(buildTrade(), {} as SizeConstraints);
  check("subclass sizeAndPrice fires", d.mirrorNotional === 5);
  check("inherited maxPerCycle still works", s.maxPerCycle() === 3);
}

console.log(`\n${failed === 0 ? "✓ all checks passed" : `✗ ${failed} failed`}\n`);
process.exit(failed === 0 ? 0 : 1);
