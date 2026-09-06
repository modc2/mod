// Smoke test for THE TRADER INDEXER — the console's default sizing model.
//
//   cd src/app && npx tsx app/lib/__test_trader_index__.ts
//
// Verifies behavior, not types: that the ratio really is "your capital over
// theirs", that a multi-trader index divides capital instead of multiplying
// the weight in twice, and — the part that costs real money when it's wrong —
// that a mirror landing under Polymarket's order floor is reported honestly
// rather than silently inflated to the minimum.
//
// Deliberately separate from lib/strats/__test__.ts: that file pins the
// cross-language parity fixtures (strat.ts against live_engine.rs) and must
// stay about the engine contract. This one is about the layer above it.

import {
  capitalToTrack,
  formatScale,
  isTraderIndex,
  projectMirror,
  scaleIndex,
  summarizeIndex,
  visibilityThreshold,
} from "./traderIndex";
import { DEFAULT_STRATS, traderIndexTemplate } from "./defaultStrats";
import type { IndexTrader } from "./types";

let failed = 0;
function check(label: string, ok: boolean, detail?: string) {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}${detail ? " — " + detail : ""}`);
  if (!ok) failed++;
}
const near = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) <= eps;

const A = "0xaaa0000000000000000000000000000000000001";
const B = "0xbbb0000000000000000000000000000000000002";
const equal = (addrs: string[]): IndexTrader[] =>
  addrs.map((address) => ({ address, weight: 1 / addrs.length, enabled: true }));

console.log("\nTRADER INDEXER\n");

// ── The ratio itself ──
{
  // One trader, $1,000 of ours against $100,000 of theirs ⇒ we run at 1%.
  const [s] = scaleIndex(equal([A]), 1_000, { [A]: 100_000 });
  check("ratio is myCapital / theirBankroll", near(s.ratio, 0.01), `got ${s.ratio}`);
  check("timesBigger is the ratio inverted", near(s.timesBigger ?? 0, 100));
  check("formatScale reads as a ratio", formatScale(s) === "1 : 100", formatScale(s));

  // Their $5,000 conviction bet lands as $50. Their $50 punt lands as 50¢ —
  // under the floor, so it is skipped rather than inflated to $2.50.
  const big = projectMirror(5_000, s, {});
  check("a big trade mirrors proportionally", big.verdict === "placed" && near(big.notional, 50));
  const small = projectMirror(50, s, {});
  check("a trade under the floor is refused, not inflated",
    small.verdict === "sub-scale" && small.notional === 0,
    `${small.verdict} @ ${small.notional}`);
}

// ── The bug this module exists to not have: weight applied twice ──
{
  // Two traders, equal weight, $1,000 total. Each gets $500 against their own
  // book — NOT $500 × ½ against it.
  const scales = scaleIndex(equal([A, B]), 1_000, { [A]: 100_000, [B]: 50_000 });
  check("capital is split across the bench", near(scales[0].mySlice, 500) && near(scales[1].mySlice, 500));
  check("weight is not squared into the ratio",
    near(scales[0].ratio, 0.005) && near(scales[1].ratio, 0.01),
    `${scales[0].ratio} / ${scales[1].ratio}`);

  // The same $2,000 trade from each leader is sized against THEIR book, so the
  // smaller trader's identical bet lands twice as big on ours. That asymmetry
  // is the entire point of the model.
  const a = projectMirror(2_000, scales[0], {});
  const b = projectMirror(2_000, scales[1], {});
  check("the smaller leader's identical bet lands bigger", near(b.notional, a.notional * 2),
    `${a.notional} vs ${b.notional}`);
}

// ── Disabled traders redistribute, they don't leak capital ──
{
  const traders: IndexTrader[] = [
    { address: A, weight: 0.5, enabled: true },
    { address: B, weight: 0.5, enabled: false },
  ];
  const scales = scaleIndex(traders, 1_000, { [A]: 100_000, [B]: 50_000 });
  check("a disabled trader is dropped from the bench", scales.length === 1);
  check("their capital goes to whoever is left, not idle",
    near(scales[0].mySlice, 1_000), `got ${scales[0].mySlice}`);
}

// ── A weightless watchlist is an EQUAL index, not a dead one ──
{
  const traders: IndexTrader[] = [
    { address: A, weight: 0 },
    { address: B, weight: 0 },
  ];
  const scales = scaleIndex(traders, 1_000, { [A]: 10_000, [B]: 10_000 });
  check("zero weights fall back to equal", near(scales[0].mySlice, 500) && near(scales[1].mySlice, 500));
}

// ── Unknowns are unknown, never zero ──
{
  const [s] = scaleIndex(equal([A]), 1_000, {});
  check("an unreadable book is a gap, not a ratio of 0", s.gap === "no-bankroll" && s.bankroll === null);
  check("nothing is projected off a gap", projectMirror(5_000, s, {}).verdict === "unknown");

  const [z] = scaleIndex(equal([A]), 0, { [A]: 10_000 });
  check("no capital is its own gap", z.gap === "no-capital");
}

// ── The floor, and the two ways past it ──
{
  const [s] = scaleIndex(equal([A]), 1_000, { [A]: 100_000 }); // ratio 0.01
  // At 50¢ the CLOB floor is max($1, 5×0.5) = $2.50. Proportionality reaches
  // it at a $250 trade of theirs; maxUpscale 2 pulls that down to $125.
  check("visibility threshold accounts for maxUpscale",
    near(visibilityThreshold(s, { maxUpscale: 2 }) ?? 0, 125),
    String(visibilityThreshold(s, { maxUpscale: 2 })));
  check("unbounded upscale sees everything",
    visibilityThreshold(s, { maxUpscale: 0 }) === 0);

  // Inside the 2× band: rounded up and placed, and SAID so.
  const nudged = projectMirror(200, s, { maxUpscale: 2 });
  check("a near-floor mirror is upscaled and labelled",
    nudged.verdict === "upscaled" && near(nudged.notional, 2.5),
    `${nudged.verdict} @ ${nudged.notional}`);
  // Outside it: refused.
  check("far below the floor is refused", projectMirror(50, s, { maxUpscale: 2 }).verdict === "sub-scale");
  // Unbounded: placed at the floor, proportionality abandoned.
  check("unbounded upscale places anyway", projectMirror(50, s, { maxUpscale: 0 }).verdict === "upscaled");

  // The per-order ceiling caps, it never skips.
  const capped = projectMirror(50_000, s, { maxTrade: 100 });
  check("the per-order cap caps rather than skips",
    capped.verdict === "capped" && near(capped.notional, 100), `${capped.verdict} @ ${capped.notional}`);
}

// ── "So how much do I need?" ──
{
  const [s] = scaleIndex(equal([A]), 1_000, { [A]: 100_000 });
  // Track them down to a $100 trade at 50¢ with maxUpscale 2: need
  // (2.50/2) × 100,000 / (100 × 1) = $1,250.
  const need = capitalToTrack(s, 100, { maxUpscale: 2 });
  check("capitalToTrack solves the floor for capital", near(need ?? 0, 1_250), String(need));
  check("capitalToTrack is null without a denominator",
    capitalToTrack(scaleIndex(equal([A]), 1_000, {})[0], 100) === null);
}

// ── The roll-up ──
{
  const scales = scaleIndex(equal([A, B]), 1_000, { [A]: 100_000 });
  const sum = summarizeIndex(scales, { maxUpscale: 2 });
  check("summary counts readable and unreadable books", sum.known === 1 && sum.unknown === 1);
  check("summary ratio uses only readable capital", near(sum.ratio ?? 0, 500 / 100_000), String(sum.ratio));
}

// ── The template really is an index ──
{
  const t = traderIndexTemplate();
  check("the default template uses bankroll sizing", t.params.sizing === "bankroll");
  check("the default template is the shelf's default", t.isDefault === true);
  check("the default template seeds a bench", (t.seed.count ?? 0) > 1);
  check("isTraderIndex agrees with the template",
    isTraderIndex({ sizing: t.params.sizing }) && isTraderIndex({}),
    "absent sizing defaults to bankroll");
  check("a conviction strat is NOT an index", !isTraderIndex({ sizing: "flow" }));
  check("a momentum strat is NOT an index",
    !isTraderIndex({ sizing: "bankroll", momentum: { lookbackMinutes: 60 } }));
  check("exactly one default per lane",
    DEFAULT_STRATS.filter((x) => x.isDefault && (x.lane ?? "any") === "any").length === 1);
}

console.log(`\n${failed === 0 ? "✓ all checks passed" : `✗ ${failed} failed`}\n`);
process.exit(failed === 0 ? 0 : 1);
