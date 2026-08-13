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
import { forwardVerdict } from "./hubReplay";
import { Strat } from "./strats/strat";
import { legKey } from "./leg";
import { computeFifoTrades } from "./pnlEngine";
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

console.log(failures === 0 ? "\n✓ all checks passed\n" : `\n✗ ${failures} check(s) failed\n`);
process.exit(failures === 0 ? 1 - 1 : 1);
