// The replay engine's own checks — no network, no deployment, no clock skew.
//
//   cd src/app && npx tsx app/lib/__test__.ts
//
// Everything here is a hand-built leader feed fed straight to `runBacktest`,
// because both bugs it covers were invisible in aggregate: the numbers looked
// plausible, they were just measuring the wrong thing.
//
//   1. OUTCOME LEGS. A market is one conditionId but two tradable tokens with
//      opposite payoffs. The sim used to book both into one position, so a No
//      hold got marked at whatever the Yes leg last printed and a Yes exit
//      closed No shares. 19% of the markets in the cached leader feeds have
//      both legs traded.
//   2. SETTLEMENT. A position the leaders stop trading has to be valued.
//      Marking it at the last observed price forgives every loser (leaders
//      let losers expire, so the last print is the entry price) while booking
//      every winner. With a resolution map the sim settles at what the market
//      actually paid.

import { runBacktest, DEFAULT_CAPITAL } from "./backtest";
import {
  basketTotal, compareToEqualSplit, equalSplit, replaySleeve, runBasketSim, sleeveFloor,
  weightedSplit, type BasketFeeds, type BasketLeg,
} from "./basketSim";
import { forwardVerdict } from "./hubReplay";
import { Strat } from "./strats/strat";
import { legKey } from "./leg";
import { computeFifoTrades } from "./pnlEngine";
import { evenSplit, fundingBudget, type FundRow } from "./multiFund";
import {
  aggregateFills, fetchWalletTradesUntil, walkReachedCutoff,
  type FetchTradesProgress,
} from "./polymarket";
import {
  applySemanticQuery, compileGate, parseSemanticQuery, semanticMatch,
} from "./semanticFilter";
import { buildCopyTrades, scoreLeaders } from "./copyTrades";
import { marketMatchesQuery } from "./marketQuery";
import type { PolymarketPosition, PolymarketTrade } from "./types";

let failures = 0;
function ok(cond: unknown, msg: string): void {
  if (cond) { console.log(`  [PASS] ${msg}`); return; }
  failures++;
  console.log(`  [FAIL] ${msg}`);
}
function near(a: number, b: number, tol: number, msg: string): void {
  ok(Math.abs(a - b) <= tol, `${msg} — got ${a.toFixed(2)}, want ~${b.toFixed(2)}`);
}

const LEADER = "0x1111111111111111111111111111111111111111";
const MKT = "0xaaaa000000000000000000000000000000000000000000000000000000000001";
const HOUR = 3600_000;

let seq = 0;
function trade(o: Partial<PolymarketTrade> & { side: "BUY" | "SELL"; price: number; size: number; hoursAgo: number }): PolymarketTrade {
  return {
    id: `t${seq++}`,
    market: o.market ?? "Will the coin land heads?",
    conditionId: o.conditionId ?? MKT,
    side: o.side,
    price: o.price,
    size: o.size,
    pnl: 0,
    timestamp: Date.now() - o.hoursAgo * HOUR,
    outcome: o.outcome,
  };
}

/** A track record for the leader, OUTSIDE the 7-day replay window but inside
    the 30-day stats window. The strat prices its edge off the leader's past
    returns (score = P(success) × ROI × mirror$), so a leader with no closed
    trades scores every candidate at zero and the replay executes nothing —
    "no scoreable edge". Six profitable round trips is a boring leader the
    tests can then observe doing one interesting thing. */
function trackRecord(): PolymarketTrade[] {
  const out: PolymarketTrade[] = [];
  for (let i = 0; i < 6; i++) {
    const past = `0xbbbb00000000000000000000000000000000000000000000000000000000000${i}`;
    out.push(trade({ side: "BUY", price: 0.50, size: 100, hoursAgo: 24 * (20 - i), conditionId: past, outcome: "Yes", market: `past ${i}` }));
    out.push(trade({ side: "SELL", price: 0.70, size: 100, hoursAgo: 24 * (20 - i) - 1, conditionId: past, outcome: "Yes", market: `past ${i}` }));
  }
  return out;
}

/** A strat with every discretionary gate open, so these tests measure the
    wallet simulation and not the entry filters. */
function openStrat(): Strat {
  return new Strat({
    name: "test",
    maxPerCycle: 50,
    minMinutesToClose: 0,
    pollIntervalMs: 60_000,
  });
}

function replay(
  trades: PolymarketTrade[],
  opts: {
    resolved?: Map<string, number>;
    positions?: PolymarketPosition[];
    /** Window length + where it ENDS — the walk-forward's two knobs. */
    days?: number;
    asOf?: number;
    /** Drop the built-in track record, for the leakage test. */
    record?: PolymarketTrade[];
  } = {},
) {
  return runBacktest({
    watchlist: [LEADER],
    traderTrades: new Map([[LEADER, [...(opts.record ?? trackRecord()), ...trades]]]),
    traderPositions: new Map([[LEADER, opts.positions ?? []]]),
    traderWeights: { [LEADER]: 100 },
    // Pinned bankroll: the mirror is then a fixed fraction of the leader's
    // size, so these assertions don't move with the leader's real net worth.
    traderBankrolls: new Map([[LEADER, 10_000]]),
    strat: openStrat(),
    days: opts.days ?? 7,
    capital: DEFAULT_CAPITAL,
    minTrade: 1,
    maxTrade: 1000,
    maxOpenPositions: 20,
    stopLossPct: 0,
    takeProfitFrac: 0,
    marketQuery: "",
    pollMinutes: 1,
    resolved: opts.resolved,
    asOf: opts.asOf,
  }).sim;
}

console.log("\n─ outcome legs are separate positions ─");
{
  // The leader buys the cheap No leg, then trades the Yes leg high. Nothing
  // about the Yes print says anything about what the No token is worth — but
  // with one book per market, the No hold inherited the 94¢ Yes mark.
  const sim = replay([
    trade({ side: "BUY", price: 0.06, size: 100, hoursAgo: 6, outcome: "No" }),
    trade({ side: "BUY", price: 0.94, size: 100, hoursAgo: 5, outcome: "Yes" }),
  ]);
  ok(sim.settlement.marked === 2, `both legs settle as their own position — got ${sim.settlement.marked}`);
  const bought = (leg: string) =>
    sim.rows.filter((r) => r.side === "BUY" && r.conditionId === MKT && Math.abs(r.price - (leg === "No" ? 0.06 : 0.94)) < 1e-6);
  const noCost = bought("No").reduce((s, r) => s + r.amount, 0);
  ok(noCost > 0, "the No leg was mirrored");
  // 6¢ in, settled at the 6¢ it was last worth: no move. Cross-marked at the
  // Yes leg's 94¢ it would have been a ~15× gain.
  near(sim.settlement.markedUsd, noCost + bought("Yes").reduce((s, r) => s + r.amount, 0), 0.05,
    "each leg settles at its OWN last price, not the market's");
}

{
  // A SELL of the Yes leg must not close No inventory. Before the fix the
  // exit consumed the No shares and booked the difference as profit.
  const sim = replay([
    trade({ side: "BUY", price: 0.10, size: 100, hoursAgo: 6, outcome: "No" }),
    trade({ side: "BUY", price: 0.90, size: 100, hoursAgo: 5, outcome: "Yes" }),
    trade({ side: "SELL", price: 0.95, size: 100, hoursAgo: 4, outcome: "Yes" }),
  ]);
  ok(sim.settlement.marked === 1, "the No position survives a Yes exit and settles on its own");
  const noCost = sim.rows
    .filter((r) => r.side === "BUY" && Math.abs(r.price - 0.10) < 1e-6)
    .reduce((s, r) => s + r.amount, 0);
  near(sim.settlement.markedUsd, noCost, 0.05, "and settles at the 10¢ it was bought at");
  ok(
    !sim.rows.some((r) => r.side === "SELL" && r.realized > 0.5),
    "no phantom realized P&L from closing the wrong leg",
  );
}

console.log("\n─ FIFO lots are per leg ─");
{
  const annotated = computeFifoTrades([
    trade({ side: "BUY", price: 0.10, size: 100, hoursAgo: 6, outcome: "No" }),
    trade({ side: "SELL", price: 0.90, size: 100, hoursAgo: 5, outcome: "Yes" }),
  ], []);
  const sell = annotated.find((t) => t.side === "SELL")!;
  ok(!sell.hasBasis, "a Yes exit does not inherit a No entry's cost basis");
  ok(sell.realized === 0, `and books no realized P&L — got ${sell.realized}`);
}

console.log("\n─ settlement: a loser that nobody sold ─");
{
  // The classic shape. The leader buys at 60¢, the market goes against them,
  // and they never trade it again — it expires worthless. The only price the
  // replay ever observed is the 60¢ entry.
  const feed = [trade({ side: "BUY", price: 0.60, size: 100, hoursAgo: 48, outcome: "Yes" })];

  const marked = replay(feed);
  ok(marked.settlement.marked === 1, "with no resolution the leg settles as MARKED");
  ok(marked.netPnl > -1, `and shows no loss — P&L ${marked.netPnl.toFixed(2)}`);

  const truth = replay(feed, { resolved: new Map([[legKey(MKT, "Yes"), 0]]) });
  ok(truth.settlement.resolved === 1, "with a resolution the leg settles as RESOLVED");
  ok(truth.settlement.markedUsd === 0, "and nothing is left to a guess");
  ok(truth.netPnl < 0, `and the loss is finally booked — P&L ${truth.netPnl.toFixed(2)}`);
  ok(
    truth.netPnl < marked.netPnl,
    `resolution truth is strictly worse than the mark here (${truth.netPnl.toFixed(2)} < ${marked.netPnl.toFixed(2)})`,
  );
  ok(
    truth.markers.some((m) => m.label.startsWith("EXPIRED WORTHLESS")),
    "the chart marks it as an expiry, not a redeem",
  );
}

console.log("\n─ settlement: a winner is booked at $1, not at the last print ─");
{
  const feed = [trade({ side: "BUY", price: 0.60, size: 100, hoursAgo: 48, outcome: "Yes" })];
  const sim = replay(feed, { resolved: new Map([[legKey(MKT, "Yes"), 1]]) });
  ok(sim.settlement.resolved === 1, "the winning leg settles as RESOLVED");
  ok(sim.netPnl > 0, `and pays out — P&L ${sim.netPnl.toFixed(2)}`);
  // 60¢ entry → $1: the mirror grows by 2/3 of its cost, whatever the mirror
  // size worked out to.
  const cost = sim.rows.filter((r) => r.side === "BUY").reduce((s, r) => s + r.amount, 0);
  near(sim.netPnl, cost * (1 / 0.6 - 1), 0.05, "payout is the full 60¢ → $1 move");
}

console.log("\n─ a resolved leg beats a stale live price ─");
{
  // data-api keeps serving a currentPrice for redeemable positions. It used to
  // win over everything, which kept dead inventory on the books at its old
  // mark; the resolution has to take precedence.
  const feed = [trade({ side: "BUY", price: 0.60, size: 100, hoursAgo: 48, outcome: "Yes" })];
  const positions: PolymarketPosition[] = [{
    conditionId: MKT, tokenId: "1", market: "Will the coin land heads?", outcome: "Yes",
    size: 100, avgPrice: 0.6, currentPrice: 0.62, value: 62, pnlUsd: 2,
    negRisk: false, redeemable: true,
  }];
  const stale = replay(feed, { positions });
  ok(stale.open.length === 1, "without a resolution the position stays open at its live mark");

  const settled = replay(feed, { positions, resolved: new Map([[legKey(MKT, "Yes"), 0]]) });
  ok(settled.open.length === 0, "with a resolution it is closed out");
  ok(settled.netPnl < 0, `and booked as the loss it was — P&L ${settled.netPnl.toFixed(2)}`);
}

// ── WALK-FORWARD ──────────────────────────────────────────────
// The card check: replay the previous window with the clock wound back, then
// ask whether the window since confirmed it. Both halves only mean anything if
// the wound-back replay is genuinely blind to everything after `asOf` — the
// flow it copies AND the track record it scores that flow with.

console.log("\n─ asOf: a past window sees only its own flow ─");
{
  const feed = [
    // Yesterday's day: one round trip, profitable.
    trade({ side: "BUY", price: 0.40, size: 100, hoursAgo: 40, outcome: "Yes" }),
    trade({ side: "SELL", price: 0.60, size: 100, hoursAgo: 30, outcome: "Yes" }),
    // Today: one round trip, a loser.
    trade({ side: "BUY", price: 0.60, size: 100, hoursAgo: 10, outcome: "Yes" }),
    trade({ side: "SELL", price: 0.45, size: 100, hoursAgo: 4, outcome: "Yes" }),
  ];
  const DAY = 24 * HOUR;
  const now = Date.now();

  const today = replay(feed, { days: 1, asOf: now });
  const prior = replay(feed, { days: 1, asOf: now - DAY });

  ok(today.rows.length > 0 && prior.rows.length > 0, "both windows executed trades");
  ok(
    today.rows.every((r) => r.ts >= now - DAY) && prior.rows.every((r) => r.ts <= now - DAY),
    "no trade appears in a window it didn't happen in",
  );
  ok(prior.netPnl > 0, `the prior day's replay is profitable — P&L ${prior.netPnl.toFixed(2)}`);
  ok(today.netPnl < 0, `today's replay is not — P&L ${today.netPnl.toFixed(2)}`);
  ok(
    forwardVerdict(
      { pnl: prior.netPnl, trades: prior.rows.length },
      { pnl: today.netPnl, trades: today.rows.length },
    ) === "faded",
    "so the card's verdict is FADED — good yesterday, lost it today",
  );
}

console.log("\n─ asOf: the wound-back replay cannot score on future results ─");
{
  // The leader's ONLY track record is a round trip that closes AFTER the prior
  // window ends. The playbook prices edge off closed returns, so a replay
  // standing at the end of the prior window has nothing to score with and must
  // execute nothing. If it trades, the stats window leaked the future.
  const DAY = 24 * HOUR;
  const now = Date.now();
  const later = `0xcccc000000000000000000000000000000000000000000000000000000000001`;
  const record = [
    trade({ side: "BUY", price: 0.50, size: 100, hoursAgo: 12, conditionId: later, outcome: "Yes", market: "recent" }),
    trade({ side: "SELL", price: 0.80, size: 100, hoursAgo: 6, conditionId: later, outcome: "Yes", market: "recent" }),
  ];
  const candidate = [trade({ side: "BUY", price: 0.40, size: 100, hoursAgo: 36, outcome: "Yes" })];

  const prior = replay(candidate, { days: 1, asOf: now - DAY, record });
  ok(prior.rows.length === 0, `the prior window executes nothing — got ${prior.rows.length} trade(s)`);
  ok(
    (prior.funnel.reasons["no scoreable edge"] ?? 0) > 0,
    "and says why: the leader had no closed trades yet at that point in time",
  );
}

console.log("\n─ the walk-forward verdict table ─");
{
  const V = (p: [number, number], n: [number, number]) =>
    forwardVerdict({ pnl: p[0], trades: p[1] }, { pnl: n[0], trades: n[1] });
  ok(V([10, 3], [5, 2]) === "held", "profit → profit is HELD");
  ok(V([10, 3], [-5, 2]) === "faded", "profit → loss is FADED");
  ok(V([-10, 3], [5, 2]) === "recovered", "loss → profit is TURNED UP, not a pass");
  ok(V([-10, 3], [-5, 2]) === "no-edge", "loss → loss is NO EDGE");
  ok(V([10, 3], [0, 0]) === "stalled", "profit → no trades is STALLED, not FADED");
  ok(V([0, 0], [5, 2]) === "untested", "no prior trades is UNTESTED — one window proves nothing");
  ok(V([0, 0], [0, 0]) === "idle", "neither window traded is IDLE");
  ok(V([0, 2], [5, 2]) === "recovered", "break-even is not profitable");
}

console.log("\n─ fills of one leader action collapse into one trade ─");
{
  // A data-api /activity row is a FILL, not an order: walking the book gives
  // one row per price level, all sharing a transaction hash. `id` IS the hash,
  // so anything deduping by id kept the first fill and dropped the rest.
  const fill = (price: number, size: number, over: Partial<PolymarketTrade> = {}) => ({
    ...trade({ side: "BUY", price, size, hoursAgo: 1, outcome: "Yes" }),
    id: "0xdead",
    asset: "tok1",
    usdcSize: price * size,
    ...over,
  });

  const agg = aggregateFills([fill(0.805, 100), fill(0.81, 100), fill(0.815, 106)]);
  ok(agg.length === 1, "three fills of one transaction become one action");
  ok(agg[0].size === 306, `every fill's shares survive — got ${agg[0].size}`);
  near(agg[0].price, 247.89 / 306, 1e-9, "price is the fill-weighted average");
  near(agg[0].usdcSize ?? 0, 247.89, 1e-6, "usdcSize is the sum");
  ok(agg[0].id === "0xdead", "the bare hash stays the id, so stored ids still match");

  // Two tokens in one transaction is two actions — collapsing them would lose
  // one outright.
  const split = aggregateFills([
    fill(0.5, 10, { asset: "tokA" }),
    fill(0.5, 10, { asset: "tokB" }),
    fill(0.5, 10, { asset: "tokA" }),
  ]);
  ok(split.length === 2, `one transaction across two tokens stays two actions — got ${split.length}`);
  ok(split[0].size === 20 && split[1].size === 10, "and each keeps its own fills");
  ok(split[1].id === "0xdead#1", "the extra leg is disambiguated, not dropped");

  // The sell fee lives in the gap between the two.
  const sell = aggregateFills([
    { ...fill(0.81, 20, { side: "SELL" as const }), usdcSize: 16.07688 },
  ]);
  near(sell[0].usdcSize ?? 0, 16.07688, 1e-6, "a SELL keeps the USDC that actually moved");
  near(sell[0].price, 0.81, 1e-9, "while `price` stays the level the leader traded at");
}

// ── MULTI-STRAT DEPOSIT ───────────────────────────────────────
// The two numbers the DEPOSIT panel refuses on. Both are pure arithmetic and
// both were wrong in the obvious implementation: a naive even split loses the
// remainder cents (so "$100 across 3" allocates $99.99), and a budget of free
// cash ALONE refuses to re-arm a strat whose money is already in positions.
{
  console.log("\n─ deposit: an even split is cent-exact ─");
  const cents = (parts: number[]) => Math.round(parts.reduce((a, b) => a + b, 0) * 100);
  ok(cents(evenSplit(100, 3)) === 10_000, `$100 across 3 sums back to $100 — got ${evenSplit(100, 3)}`);
  ok(cents(evenSplit(0.05, 3)) === 5, "and so does a 5¢ split across 3");
  ok(evenSplit(100, 3)[0] === 33.34, "remainder cents go to the earliest row");
  ok(evenSplit(50, 0).length === 0, "nothing selected splits into nothing");
  ok(cents(evenSplit(-10, 2)) === 0, "a negative total can't allocate anything");

  console.log("\n─ deposit: the budget counts a strat's own deployed money ─");
  const row = (id: string, deployed: number): FundRow => ({
    id, name: id, kind: "strat", allocated: 0, deployed, running: true,
  });
  // $20 free, and the strat being re-armed already holds $80 of positions:
  // funding it at $100 is not over-allocating, it IS its current size.
  ok(fundingBudget(20, [row("a", 80)]) === 100, "free cash + the selected strat's own basis");
  ok(fundingBudget(20, []) === 20, "with nothing selected the budget is the free cash");
  ok(fundingBudget(null, [row("a", 80)]) === null, "an unknown balance stays unknown, never 0");
  // Another strat's money is committed to it — it must not inflate this one's
  // budget, which is why only the SELECTED rows are summed.
  ok(fundingBudget(20, [row("a", 80), row("b", 5)]) === 105, "every selected row's basis counts, once");
}

// ── THE ACTIVITY FEED'S FLOOR ─────────────────────────────────
// data-api refuses `/activity` past offset 5000 — a permanent product limit,
// not a blip. The sync used to walk straight into it, throw, cache nothing,
// and (because the proxy dressed the 400 up as a 502) render as a retryable
// outage on precisely the high-frequency traders worth copying. A wallet with
// more rows than the ceiling must come back CAPPED, not FAILED.
// (In a function, not a bare block: this is the one async check in the file
// and tsx compiles to CJS, where top-level await is a build error.)
async function activityCeilingChecks(): Promise<void> {
  console.log("\n─ activity feed: the depth ceiling ends a walk, it doesn't break it ─");
  const realFetch = globalThis.fetch;
  let pagesServed = 0;

  // Stands in for data-api: 500 TRADE rows a page, and the documented 400 the
  // moment a caller asks for offset 5001+.
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://x");
    const offset = Number(url.searchParams.get("offset") || 0);
    if (offset > 5000) {
      return new Response(
        JSON.stringify({ error: "max historical activity offset of 5000 exceeded" }),
        { status: 400, headers: { "content-type": "application/json" } },
      );
    }
    pagesServed++;
    const nowSec = Math.floor(Date.now() / 1000);
    const rows = Array.from({ length: 500 }, (_, i) => ({
      type: "TRADE",
      // Dense and recent — never old enough to reach a 30d cutoff, which is
      // the whole point: only the ceiling can stop this walk.
      timestamp: nowSec - (offset + i) * 60,
      price: 0.5, size: 10, side: "BUY",
      transactionHash: `0xtx${offset + i}`,
      asset: `tok${offset + i}`, conditionId: `0xcid${offset + i}`,
      title: "A MARKET",
    }));
    return new Response(JSON.stringify(rows), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    let last: FetchTradesProgress | null = null;
    const trades = await fetchWalletTradesUntil(
      "0xfeedfacefeedfacefeedfacefeedfacefeedface",
      0,
      (info) => { last = info; },
    );
    const progress = last as FetchTradesProgress | null;
    ok(trades.length > 0, `a capped walk still returns its trades — got ${trades.length}`);
    ok(progress?.depthCapped === true, "and reports the feed as depth-capped, not OK");
    ok(progress?.done === true, "the progress strip finishes rather than hanging at 95%");
    ok(pagesServed === 11, `it stops AT the ceiling — 11 pages served, got ${pagesServed}`);
    ok(
      walkReachedCutoff("0xfeedfacefeedfacefeedfacefeedfacefeedface") === false,
      "coverage records that the requested window was NOT covered",
    );
  } catch (e) {
    ok(false, `a wallet past the ceiling must not throw — got ${String(e)}`);
  } finally {
    globalThis.fetch = realFetch;
  }
}

// ── THE BASKET ────────────────────────────────────────────────────────────
//
// Copying a SET of traders with a different amount against each is not the sum
// of N profile pages, and these checks pin the two ways that bites:
//
//   1. The sleeves must be INDEPENDENT and additive — that is what the desk
//      runs (one allocation, one session, one budget), so a leg's number must
//      not depend on what the other legs did.
//   2. An underfunded leg does not take a small position, it takes NO
//      position: the proportional mirror lands under the order floor, the
//      upscale clamp refuses it, and the money sits in cash. A basket that
//      reports that as "$0.00, breaking even" is the whole failure this
//      screen exists to prevent.

console.log("\n─ the basket: sleeves, splits and floors ─");

const WHALE = "0x2222222222222222222222222222222222222222";
const SMALL = "0x3333333333333333333333333333333333333333";

/** A leader's out-of-window track record. The strat prices its edge off past
    returns, so a leader with no closed trades scores every candidate at zero
    and the replay executes nothing — six profitable round trips make them
    boring enough to then observe doing one interesting thing. */
function record(tag: string): PolymarketTrade[] {
  const out: PolymarketTrade[] = [];
  for (let i = 0; i < 6; i++) {
    const cid = `0x${tag}past${i}`;
    out.push(trade({ side: "BUY", price: 0.5, size: 100, hoursAgo: 24 * (20 - i), conditionId: cid, outcome: "Yes", market: `${tag} past ${i}` }));
    out.push(trade({ side: "SELL", price: 0.7, size: 100, hoursAgo: 24 * (20 - i) - 1, conditionId: cid, outcome: "Yes", market: `${tag} past ${i}` }));
  }
  return out;
}

/** 20 in-window BUYs of $100 each across four markets — $2,000 of flow, which
    is the denominator `flow` sizing divides your allocation by — and then two
    of those markets ridden out at 60¢, so the leg books a real exit rather
    than sitting on marked inventory. */
function whaleFeed(): PolymarketTrade[] {
  const out = record("wh");
  for (let i = 0; i < 20; i++) {
    out.push(trade({
      side: "BUY", price: 0.5, size: 200, hoursAgo: 6 - i * 0.2,
      conditionId: `0xwhmkt${i % 4}`, outcome: "Yes", market: `whale market ${i % 4}`,
    }));
  }
  for (const m of [0, 1]) {
    out.push(trade({
      side: "SELL", price: 0.6, size: 1000, hoursAgo: 1,
      conditionId: `0xwhmkt${m}`, outcome: "Yes", market: `whale market ${m}`,
    }));
  }
  return out;
}

/** A small leader: $100 in, $120 out, on one of the whale's markets (so the
    overlap check has something to find). */
function smallFeed(): PolymarketTrade[] {
  const out = record("sm");
  out.push(trade({ side: "BUY", price: 0.5, size: 200, hoursAgo: 6, conditionId: "0xwhmkt0", outcome: "Yes", market: "whale market 0" }));
  out.push(trade({ side: "SELL", price: 0.6, size: 200, hoursAgo: 3, conditionId: "0xwhmkt0", outcome: "Yes", market: "whale market 0" }));
  return out;
}

const basketFeeds: BasketFeeds = {
  trades: new Map([[WHALE, whaleFeed()], [SMALL, smallFeed()]]),
  positions: new Map([[WHALE, []], [SMALL, []]]),
  // Pinned so the mirror is a fixed fraction of the leader's size and these
  // assertions don't move with anyone's real net worth.
  bankrolls: new Map([[WHALE, 100_000], [SMALL, 5_000]]),
};
const basketOpts = { days: 7 };

{
  const legs: BasketLeg[] = [
    { address: WHALE, allocationUsd: 1000, label: "WHALE" },
    { address: SMALL, allocationUsd: 120, label: "SMALL" },
  ];
  const run = runBasketSim(legs, basketFeeds, basketOpts);

  ok(run.sleeves.length === 2, `both legs replayed — got ${run.sleeves.length}`);
  ok(run.portfolio.capital === 1120, `the basket's capital is the sum of its legs — got ${run.portfolio.capital}`);
  near(
    run.portfolio.net,
    run.sleeves.reduce((s, x) => s + x.net, 0),
    1e-9,
    "the portfolio's P&L is exactly the sum of the sleeves — no pooled wallet, no cross-leg interaction",
  );

  // Each leg on its own capital: replaying one leg ALONE must give the same
  // number it gives inside the basket. This is the property that makes "give
  // this trader $M" mean something on a screen with five other traders on it.
  const alone = replaySleeve(legs[0], basketFeeds, basketOpts);
  near(alone?.sim.netPnl ?? NaN, run.sleeves[0].net, 1e-9,
    "a sleeve's result is identical whether or not other legs are in the basket");

  // The curve starts at the money you funded, not at zero and not at one leg's
  // capital: sleeves that haven't traded yet are held as cash.
  const first = run.portfolio.equity[0];
  near(first.liq + first.pos, 1120, 1.0, "the merged curve opens at the basket's total capital");
  const last = run.portfolio.equity[run.portfolio.equity.length - 1];
  near(last.liq + last.pos, 1120 + run.portfolio.net, 0.01,
    "and closes at capital + net, so the chart and the header agree");

  ok(run.overlap.markets >= 1,
    `two legs trading one market is reported as overlap — got ${run.overlap.markets}`);
}

{
  // A leg gated to a topic its leader never trades observes nothing, copies
  // nothing, and its money never leaves cash. The basket must call that out
  // as IDLE CAPITAL rather than printing a $0 that reads as break-even.
  const legs: BasketLeg[] = [
    { address: WHALE, allocationUsd: 1000, label: "WHALE" },
    { address: SMALL, allocationUsd: 500, label: "GATED", params: { marketQuery: "ethereum" } },
  ];
  const run = runBasketSim(legs, basketFeeds, basketOpts);
  const gated = run.sleeves.find((s) => s.label === "GATED")!;

  ok(gated.trades === 0, `the gated leg placed nothing — got ${gated.trades}`);
  ok(!!gated.note, `and says why: "${gated.note ?? ""}"`);
  ok(run.portfolio.legsTrading === 1, `LEGS TRADING counts only the leg that worked — got ${run.portfolio.legsTrading}`);
  near(run.portfolio.idleUsd, 500, 1e-9, "the idle leg's whole allocation is reported as capital that never traded");
  // A gate that matches nothing is not a sizing problem — no amount fixes it.
  ok(sleeveFloor(legs[1], basketFeeds, basketOpts) === null,
    "a leg that can never trade has no floor, and the search says null rather than guessing");
}

{
  // The sizing floor itself. $2,000 of leader flow spread over 20 trades means
  // a mirror is 1/20th of your allocation: at $10 that's 50¢ against a $2.50
  // minimum order, which the upscale clamp refuses outright (SUB_SCALE).
  const tiny: BasketLeg = { address: WHALE, allocationUsd: 10, label: "TINY" };
  const tinyRun = replaySleeve(tiny, basketFeeds, basketOpts);
  ok(tinyRun !== null && tinyRun.sim.funnel.executed === 0,
    `$10 behind this leader copies nothing — got ${tinyRun?.sim.funnel.executed ?? -1} fills`);

  const floor = sleeveFloor(tiny, basketFeeds, basketOpts);
  ok(floor !== null && floor > 10,
    `and the floor search names an amount that WOULD trade — got ${floor === null ? "null" : `$${floor}`}`);
  const atFloor = replaySleeve({ ...tiny, allocationUsd: floor ?? 0 }, basketFeeds, basketOpts);
  ok((atFloor?.sim.funnel.executed ?? 0) > 0, "the amount it names actually trades");
  const belowFloor = replaySleeve({ ...tiny, allocationUsd: (floor ?? 0) / 2 }, basketFeeds, basketOpts);
  ok((belowFloor?.sim.funnel.executed ?? 0) === 0 || (floor ?? 0) <= 10,
    "and it is the SMALLEST such amount on the ladder — half of it still trades nothing");
}

{
  // Splits are pure arithmetic, but they are the arithmetic the whole screen
  // is about: rescaling must conserve the total and preserve conviction, and
  // an even split must be actually even.
  const legs: BasketLeg[] = [
    { address: WHALE, allocationUsd: 300 },
    { address: SMALL, allocationUsd: 100 },
  ];
  ok(basketTotal(legs) === 400, `the total is the sum of the enabled legs — got ${basketTotal(legs)}`);

  const even = equalSplit(legs, 400);
  ok(even.every((l) => l.allocationUsd === 200), "SPLIT EQUAL gives every leg the same dollars");

  const scaled = weightedSplit(legs, 800);
  near(basketTotal(scaled), 800, 0.02, "RESCALE lands on the total you asked for");
  near(scaled[0].allocationUsd / scaled[1].allocationUsd, 3, 1e-6,
    "and keeps the 3:1 conviction you expressed");

  // Parked legs are not funded and not replayed, but they stay in the roster.
  const parked = equalSplit(
    [...legs, { address: "0x4444444444444444444444444444444444444444", allocationUsd: 50, enabled: false }],
    400,
  );
  ok(parked.length === 3 && parked[2].allocationUsd === 50,
    "a parked leg keeps its amount and takes none of the split");
  ok(parked[0].allocationUsd === 200, "…so the split divides among the ENABLED legs only");
}

{
  // The counterfactual: same names, same total, same window, divided evenly.
  // Whatever it says, `chosen` has to be the number the panel is showing —
  // a comparison against a differently-computed baseline is worse than none.
  const legs: BasketLeg[] = [
    { address: WHALE, allocationUsd: 1000 },
    { address: SMALL, allocationUsd: 120 },
  ];
  const run = runBasketSim(legs, basketFeeds, basketOpts);
  const cmp = compareToEqualSplit(legs, basketFeeds, basketOpts, run.portfolio.net);
  near(cmp.chosen, run.portfolio.net, 1e-9, "the comparison scores the split actually on screen");
  near(cmp.edge, cmp.chosen - cmp.equal, 1e-9, "and the edge is the difference between the two, not a re-derivation");
  ok(cmp.differs === 2, `it also reports how many legs differ from even — got ${cmp.differs}`);

  const equalRun = runBasketSim(equalSplit(legs, basketTotal(legs)), basketFeeds, basketOpts);
  near(cmp.equal, equalRun.portfolio.net, 1e-9, "the baseline IS the equal-split basket, replayed the same way");
}

// ── THE SENTENCE BOX ──────────────────────────────────────────────────────
//
// lib/semanticFilter.ts turns one line of English into a gate. Two things are
// worth pinning: that it READS the sentence the way the chips claim, and that
// what it COMPILES is a query the engine's own matcher (marketQuery.ts, and
// its Rust mirror) agrees with. The second is the load-bearing one — a
// compiled gate that the engine reads differently would copy a different slice
// of the flow than the screen that armed it.

console.log("\n── Semantic filter (semanticFilter.ts) ──");
{
  const q = parseSemanticQuery("big buys on crypto under 30c");
  ok(q.sides === "buy", "…reads a side out of 'buys'");
  near(q.maxPrice ?? -1, 0.3, 1e-9, "…'under 30c' is a PRICE band, not a dollar amount");
  ok(q.minNotional === 500, `…'big' is a size floor — got ${q.minNotional}`);
  ok(q.groups.length === 1 && q.groups[0][0].concept === "crypto",
    "…and 'crypto' survives as a concept, not a literal");
  // The whole point of the lexicon: the word "crypto" is in almost no title.
  const btc = {
    market: "Bitcoin above $110,000 on December 31?", side: "BUY" as const,
    price: 0.2, size: 5000, notional: 1000, timestamp: Date.now(),
  };
  ok(semanticMatch(btc, q).pass, "a BTC market matches 'crypto' though the word never appears");
  ok(!marketMatchesQuery(btc.market, "crypto"),
    "…which the literal matcher it replaces does NOT do (that is the feature)");

  // Every attribute clause actually gates.
  ok(!semanticMatch({ ...btc, side: "SELL" }, q).pass, "…a sell is rejected");
  ok(!semanticMatch({ ...btc, price: 0.55 }, q).pass, "…so is a 55¢ fill");
  ok(!semanticMatch({ ...btc, notional: 20 }, q).pass, "…so is a $20 trade");
}

{
  // Commas are OR, spaces are AND — the same dialect as marketQuery, because
  // that is what the compile step emits.
  const q = parseSemanticQuery("sports, politics");
  ok(q.groups.length === 2, "commas split into OR groups");
  const nba = { market: "Lakers vs Celtics", side: "BUY" as const, price: 0.5, size: 10, timestamp: Date.now() };
  const gov = { market: "Who wins the New Jersey governor race?", side: "BUY" as const, price: 0.5, size: 10, timestamp: Date.now() };
  ok(semanticMatch(nba, q).pass && semanticMatch(gov, q).pass, "…and either side satisfies it");
  ok(!semanticMatch({ ...nba, market: "Will it rain in Paris?" }, q).pass, "…while an unrelated title does not");
}

{
  // Exclusion: screen-only, and it must SAY so rather than pretend.
  const q = parseSemanticQuery("crypto not candles");
  const candle = { market: "Bitcoin Up or Down — 3:45pm ET", side: "BUY" as const, price: 0.5, size: 10, timestamp: Date.now() };
  const dated = { market: "Bitcoin above $110,000 on December 31?", side: "BUY" as const, price: 0.5, size: 10, timestamp: Date.now() };
  ok(!semanticMatch(candle, q).pass, "'not candles' drops the 5-minute Up/Down flow");
  ok(semanticMatch(dated, q).pass, "…and keeps the dated market");
  const gate = compileGate(q);
  ok(gate.viewOnly.some((v) => v.startsWith("NOT ")),
    "…and the compiled gate reports the exclusion as UNENFORCEABLE, never silently drops it");
}

{
  // The compile contract: whatever the compiled marketQuery accepts, the
  // parsed query accepts too. Checked against the engine's own matcher.
  const q = parseSemanticQuery("bitcoin buys");
  const gate = compileGate(q);
  ok(gate.tradeFilters.sides === "buy", "the side lands in tradeFilters, where the engine reads it");
  const titles = [
    "Bitcoin above $110,000 on December 31?",
    "BTC Up or Down — 3:45pm ET",
    "Will Ethereum flip Bitcoin?",
    "Lakers vs Celtics",
    "Who wins the presidency?",
  ];
  let agree = 0;
  for (const market of titles) {
    const byQuery = marketMatchesQuery(market, gate.marketQuery);
    const bySemantic = semanticMatch(
      { market, side: "BUY", price: 0.5, size: 10, timestamp: Date.now() },
      { ...q, sides: undefined },
    ).pass;
    if (byQuery === bySemantic) agree++;
    else console.log(`     (disagreed on ${market}: engine ${byQuery}, screen ${bySemantic})`);
  }
  ok(agree === titles.length, `the engine's matcher and the screen agree on every title (${agree}/${titles.length})`);
}

{
  // The expansion has to SURVIVE the compile. A gate armed as the literal word
  // "crypto" reaches no title at all — that is the failure this checks for.
  const gate = compileGate(parseSemanticQuery("crypto"));
  ok(marketMatchesQuery("Bitcoin above $110,000 on December 31?", gate.marketQuery),
    "a compiled 'crypto' gate matches a Bitcoin market through the engine's own matcher");
  ok(marketMatchesQuery("Will SOL hit $300?", gate.marketQuery), "…and a Solana one");
  ok(!marketMatchesQuery("Who wins the presidency?", gate.marketQuery), "…and not an unrelated one");
  const two = compileGate(parseSemanticQuery("crypto election"));
  ok(two.marketQuery.split(",").length <= 64, `two AND-ed concepts stay inside the ceiling — ${two.marketQuery.split(",").length} groups`);
  ok(marketMatchesQuery("Will Bitcoin be an election issue?", two.marketQuery),
    "…and still match a title about both");
}

{
  // An empty sentence is a no-op, not a wall.
  const q = parseSemanticQuery("   ");
  ok(q.empty, "a blank query is empty");
  const rows = [{ market: "anything", side: "BUY" as const, price: 0.5, size: 1, timestamp: Date.now() }];
  ok(applySemanticQuery(rows, q).rows.length === 1, "…and lets everything through");
  ok(compileGate(q).any === false, "…and arms nothing");
}

// ── MY COPY TRADES ────────────────────────────────────────────────────────
//
// lib/copyTrades.ts joins my fills to the leader trades they mirror. The join
// is inferred (a fill carries no leader tag), so the rules have to be pinned:
// a fill only claims a trade in the SAME market and side that came BEFORE it
// within the window, one trade is claimed at most once, and a fill that
// matches nothing is reported rather than hidden.

console.log("\n── Copy trades (copyTrades.ts) ──");
{
  const T0 = Date.now() - 2 * HOUR;
  const leaderTrade = (o: Partial<PolymarketTrade> & { at: number }): PolymarketTrade => ({
    id: `t${o.at}`, market: "Bitcoin above $110,000?", slug: "btc", conditionId: MKT,
    side: "BUY", price: 0.4, size: 100, usdcSize: 40, pnl: 0, timestamp: o.at,
    ...o,
  }) as PolymarketTrade;

  const leaders = {
    [LEADER]: [
      leaderTrade({ at: T0 }),
      leaderTrade({ at: T0 + 40 * 60_000, side: "SELL", price: 0.6, id: "exit" }),
      leaderTrade({ at: T0 + 80 * 60_000, id: "unmatched" }),
    ],
  };
  const mine = [
    // 90 seconds behind their entry, 2¢ worse.
    leaderTrade({ at: T0 + 90_000, price: 0.42, size: 20, usdcSize: 8.4, id: "mine-1" }),
    // An exit of my own, hours from any leader trade.
    leaderTrade({ at: T0 + 3 * HOUR, side: "SELL", price: 0.9, id: "mine-own" }),
  ];

  const { rows, summary } = buildCopyTrades({ mine, leaders }, { now: Date.now(), windowMs: 7 * 24 * HOUR });
  ok(summary.leader === 3 && summary.mine === 2, `both halves are in the feed — ${summary.leader} theirs, ${summary.mine} mine`);
  ok(summary.copied === 1, `exactly one of their trades was matched — got ${summary.copied}`);
  ok(summary.missed === 2, "…and the other two are MISSED, which is the number that matters");
  near(summary.coverage, 1 / 3, 1e-9, "coverage is copied / theirs");
  ok(summary.medianLagSec === 90, `the lag is measured in seconds — got ${summary.medianLagSec}`);
  ok(summary.avgSlipCents === 2, `…and the slip is signed against the leader — got ${summary.avgSlipCents}`);
  ok(summary.unattributed === 1, "a fill with no leader behind it is REPORTED, not silently attributed");

  const mineRow = rows.find((r) => r.id.includes("mine-own"));
  ok(mineRow?.leader == null, "…and that fill carries no leader");

  // A second fill must not claim a trade already claimed.
  const twice = buildCopyTrades(
    { mine: [...mine, leaderTrade({ at: T0 + 120_000, price: 0.43, id: "mine-2" })], leaders },
    { now: Date.now() },
  );
  ok(twice.summary.copied === 1, "two fills cannot both mirror one leader trade");

  // A fill claims the NEAREST preceding trade inside the window, never an
  // older one it happens to share a market with — an hours-late re-entry
  // reading as a 2-hour lag is how a slow copy looks fine on paper.
  const late = buildCopyTrades(
    { mine: [leaderTrade({ at: T0 + 85 * 60_000, price: 0.42, id: "late" })], leaders },
    { now: Date.now(), matchMs: 30 * 60_000 },
  );
  const lateRow = late.rows.find((r) => r.id.includes("late"));
  ok(lateRow?.lagSec === 300, `it mirrors the 80-minute trade, not the one two hours back — got ${lateRow?.lagSec}s`);
  const older = buildCopyTrades(
    { mine: [leaderTrade({ at: T0 + 45 * 60_000, price: 0.42, id: "way-late" })], leaders },
    { now: Date.now(), matchMs: 30 * 60_000 },
  );
  ok(older.summary.copied === 0 && older.summary.unattributed === 1,
    "and a BUY 45 minutes after the only BUY in range claims nothing at all");

  const byLeader = scoreLeaders(rows);
  ok(byLeader.length === 1 && byLeader[0].trades === 3 && byLeader[0].copied === 1,
    "the per-leader roll-up counts the same trades the summary does");
}

activityCeilingChecks().then(() => {
  console.log(failures === 0 ? "\n✓ all checks passed\n" : `\n✗ ${failures} check(s) failed\n`);
  process.exit(failures === 0 ? 1 - 1 : 1);
});
