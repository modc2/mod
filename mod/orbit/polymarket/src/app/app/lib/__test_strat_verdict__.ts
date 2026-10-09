// THE STRAT VERDICT — which strats are consistently making money.
//
//   cd src/app && npx tsx app/lib/__test_strat_verdict__.ts
//
// Pins lib/stratVerdict.ts: signs not magnitudes, the live book counts
// double, a holdout leak can never rank CONSISTENT, stale/warming/0-trade
// cells decide nothing, and thin evidence says UNKNOWN instead of dressing
// up as a verdict.

import { computeStratVerdict } from "./stratVerdict";
import type { WindowRow } from "./hubBacktest";
import type { HubBacktest } from "./hubReplay";
import type { StratMoney } from "./stratStats";

let failed = 0;
function check(label: string, ok: boolean, detail?: string) {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? " — " + detail : ""}`);
  if (!ok) failed++;
}

function bt(days: number, pnl: number, o: Partial<HubBacktest & { stale: boolean }> = {}): HubBacktest {
  return {
    pnl, roi: pnl, trades: 10, skipped: 0, capital: 1000, days,
    traders: 3, curve: [], at: Date.now(), sig: "s", ...o,
  } as HubBacktest;
}

function ladder(cells: Record<number, HubBacktest>): WindowRow {
  return cells as WindowRow;
}

function money(totalPnl: number, o: Partial<StratMoney> = {}): StratMoney {
  return { totalPnl, pnlPct: totalPnl / 10, fills: 5, openPositions: 1, ...o } as StratMoney;
}

console.log("tiers");
{
  const green = ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 5), 14: bt(14, 12), 30: bt(30, 20) });
  check("green across the ladder = CONSISTENT", computeStratVerdict(green, undefined).tier === "consistent");

  const red = ladder({ 1: bt(1, -10), 3: bt(3, -8), 7: bt(7, 2), 14: bt(14, -12), 30: bt(30, -20) });
  check("mostly red = BLEEDING", computeStratVerdict(red, undefined).tier === "bleeding");

  const split = ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, -5), 14: bt(14, -12) });
  check("half and half = MIXED", computeStratVerdict(split, undefined).tier === "mixed");

  check("no data = UNKNOWN", computeStratVerdict(undefined, undefined).tier === "unknown");
  check("one sample = UNKNOWN", computeStratVerdict(ladder({ 1: bt(1, 10) }), undefined).tier === "unknown",
    "a single green window is a coin flip, not a verdict");
}

console.log("signs, not magnitudes");
{
  // One +$500 window must not buy back four -$5 ones.
  const lotto = ladder({ 1: bt(1, 500, { roi: 50 }), 3: bt(3, -5), 7: bt(7, -5), 14: bt(14, -5), 30: bt(30, -5) });
  check("one 10-bagger vs four bleeds = BLEEDING", computeStratVerdict(lotto, undefined).tier === "bleeding");
}

console.log("the live book counts double");
{
  const green4 = ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 5), 14: bt(14, 12) });
  check("green ladder + live loss drops out of CONSISTENT",
    computeStratVerdict(green4, money(-20)).tier !== "consistent",
    `got ${computeStratVerdict(green4, money(-20)).tier}`);
  check("green ladder + live gain stays CONSISTENT",
    computeStratVerdict(green4, money(20)).tier === "consistent");
  check("live drift inside the flat band decides nothing",
    computeStratVerdict(green4, money(0.4)).tier === "consistent");
  const v = computeStratVerdict(undefined, money(50));
  check("live alone is one (double) sample = CONSISTENT", v.tier === "consistent", `got ${v.tier}`);
}

console.log("holdout outranks the headline");
{
  const leak = ladder({
    7: bt(7, 469, { roi: 46.9, holdout: { statsFrom: 0, statsTo: 1, days: 7, pnl: -58, roi: -5.8, trades: 40, ok: false } }),
    1: bt(1, 10), 3: bt(3, 8), 14: bt(14, 12),
  });
  const v = computeStratVerdict(leak, undefined);
  check("green ladder + negative holdout can't be CONSISTENT", v.tier !== "consistent", `got ${v.tier}`);
  check("leak is named", v.holdout === "leak");

  const honest = ladder({
    7: bt(7, 83, { holdout: { statsFrom: 0, statsTo: 1, days: 7, pnl: 86, roi: 8.6, trades: 220, ok: true } }),
    1: bt(1, 10), 3: bt(3, 8),
  });
  const h = computeStratVerdict(honest, undefined);
  check("confirmed holdout stays CONSISTENT and outranks an unchecked twin",
    h.tier === "consistent" && h.score > computeStratVerdict(ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 83) }), undefined).score);
}

console.log("cells that decide nothing");
{
  const noisy = ladder({
    1: bt(1, 10), 3: bt(3, 8),
    7: bt(7, -999, { stale: true }),
    14: bt(14, -999, { warming: 2 }),
    30: bt(30, 0, { trades: 0, note: "no leader flow in this window" }),
  });
  const v = computeStratVerdict(noisy, undefined);
  check("stale / warming / 0-trade cells are skipped", v.tier === "consistent" && v.samples === 2, `${v.wins}/${v.samples}`);
}

console.log("deep window loss blocks CONSISTENT");
{
  // The live bug this rule exists for: four green short windows + a 30D
  // window that lost 91% of its capital ranked CONSISTENT in the first cut.
  const cliff = ladder({ 1: bt(1, 0.3, { roi: 0.3 }), 3: bt(3, 68, { roi: 6.9 }), 7: bt(7, 142, { roi: 14 }), 14: bt(14, 137, { roi: 14 }), 30: bt(30, -909, { roi: -91 }) });
  const v = computeStratVerdict(cliff, undefined);
  check("4 green windows + one −91% window ≠ CONSISTENT", v.tier !== "consistent", `got ${v.tier}`);
  check("a shallow red window does not trip the rule",
    computeStratVerdict(ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 5), 30: bt(30, -5, { roi: -5 }) }), undefined).tier === "consistent");
}

console.log("the saved backtest is a sample; an OOS one is a holdout");
{
  const green = ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 5), 14: bt(14, 12), 30: bt(30, 20) });
  // TOP 5 · BEST's shape: green ladder, OOS 7D backtest at −$387.
  const leak = computeStratVerdict(green, undefined, { pnl: -387, trades: 85, oos: true });
  check("green ladder + red OOS backtest = leak, not CONSISTENT", leak.tier !== "consistent" && leak.holdout === "leak", `got ${leak.tier}`);
  const ok = computeStratVerdict(green, undefined, { pnl: 5999, trades: 215, oos: true });
  check("green ladder + green OOS backtest = CONSISTENT, confirmed", ok.tier === "consistent" && ok.holdout === "confirmed");
  const manual = computeStratVerdict(green, undefined, { pnl: -742, trades: 334, oos: false });
  check("a red NON-OOS backtest counts as a loss sample, not a leak", manual.holdout === null && manual.samples === 6, `${manual.wins}/${manual.samples}`);
  check("a near-flat backtest decides nothing",
    computeStratVerdict(green, undefined, { pnl: -0.04, trades: 4, oos: true }).tier === "consistent");
}

console.log("ranking");
{
  const a = computeStratVerdict(ladder({ 1: bt(1, 10), 3: bt(3, 8), 7: bt(7, 5) }), undefined);
  const b = computeStratVerdict(ladder({ 1: bt(1, 10), 3: bt(3, -8), 7: bt(7, 5) }), undefined);
  const c = computeStratVerdict(ladder({ 1: bt(1, -10), 3: bt(3, -8), 7: bt(7, 5) }), undefined);
  check("CONSISTENT > MIXED > BLEEDING by score", a.score > b.score && b.score > c.score);
  check("UNKNOWN sits between MIXED and BLEEDING", computeStratVerdict(undefined, undefined).score > c.score
    && computeStratVerdict(undefined, undefined).score < b.score);
}

if (failed > 0) {
  console.error(`\n${failed} check(s) FAILED`);
  process.exit(1);
}
console.log("\nall checks passed");
