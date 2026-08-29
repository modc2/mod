// Built-in DEFAULT STRATS — curated starting points shown in the header
// strat picker. They are not stored anywhere: each is a recipe (risk params
// + trade filters + a leaderboard seeding rule), and "forking" one just
// materializes a fresh SavedIndex owned by the user, then asynchronously
// seeds it with the CURRENT top traders matching the recipe. Trader lists
// are resolved at fork time on purpose — a hardcoded address list would rot
// within weeks, while "top 10 crypto traders over the last 7 days" stays
// fresh forever.
//
// COPY TRADES is the shelf's plain-copy card and the one the COPY DESK and
// this gallery share: its params are IMPORTED from `identityStrat.ts`, the
// same template the desk turns each per-trader allocation into. Fork it and
// you get copy trading with the desk's settings as an ordinary strat — one
// you can fund alongside the others from the DEPOSIT screen, backtest on the
// same wall, and edit. Everything else here is that idea with a gate on it.
//
// Deliberately NO sub-hour "Up or Down" COPY template: copying HFT bots on
// 5-min markets with a mirror lag is a structural loss (see the movoaev8
// postmortem). The crypto template leans on a conviction notional floor to
// bias away from that flow. BTC 5-MIN DELTA is the one deliberate exception
// and it does NOT copy anyone — it originates from the live candle's own
// near-live price tape (momentum.candles), with tiny sizing and a single
// position slot precisely because that lane is still HFT turf.
//
// BITCOIN'S DEFAULT IS COPY TRADING. The three BTC cards a user meets first
// (BTC COPY, BTC SWING COPY, BTC SHARPS) all mirror other people's Bitcoin
// flow; the two origination ones are alternatives you have to go looking for.
// That ordering is a measured call, not a preference — the full-wallet audit
// (5,196 fills, June–Aug 2026) split the console's losses as:
//
//     BTC 5-min Up/Down candles   −$639   (−17% ROI, 965 legs)
//     everything else              −$12   (−0.3%,    515 legs)
//
// i.e. copying is roughly break-even and the sub-hour candle lane is the whole
// hole. So every BTC copy template below is built to exclude that ONE lane —
// `minMinutesToClose` above the candle's whole lifetime, a ≥60¢ (or ≥45¢ on
// the swing card) entry band to skip the 40–60¢ coin-flip where late mirroring
// measured −12.4pp, and a notional floor to skip bot dust. What is left is BTC
// markets with hours on the clock, where a 30-second mirror lag is noise.
//
// See `lane` / `isDefault` below for how the hub sorts and badges these.
//
// FILTER is the one template whose watchlist is deliberately WIDE (20): its
// point is that the strat picks the roster itself, every scan, from the
// traders' own realized returns — see `TraderFilter`.
//
// Templates WITHOUT an explicit price band copy their leaders' flow whole:
// there is no implicit floor any more (it silently blocked most entries, which
// read as a dead engine). A band is a choice a template states — TOP 10
// ALL-STARS asks for ≥60¢, LONGSHOT HUNTER for 2–20¢.

import { SavedIndex } from "./types";
import {
  IDENTITY_BACKTEST_DAYS, IDENTITY_MAX_OPEN_POSITIONS, IDENTITY_MAX_PER_CYCLE,
  IDENTITY_MAX_TRADE, IDENTITY_MAX_TRADE_AGE_SEC, IDENTITY_MIN_MINUTES_TO_CLOSE,
  IDENTITY_MIN_TRADE, IDENTITY_POLL_MINUTES, IDENTITY_SIZING, IDENTITY_STOP_LOSS,
  IDENTITY_TAKE_PROFIT, IDENTITY_TURNOVER,
} from "./identityStrat";
import {
  equalWeightTraders,
  saveIndex,
  setActiveIndexId,
  uniqueIndexName,
  updateIndex,
} from "./indexStore";
import { fetchTopTraderAddresses } from "./polymarket";

/** The market lane a template belongs to — how the hub groups the shelf, and
    what "the default strat for X" is answered against. */
export type StratLane = "btc" | "crypto" | "sports" | "politics" | "weather" | "any";

export const LANE_LABEL: Record<StratLane, string> = {
  btc: "BITCOIN",
  crypto: "CRYPTO",
  sports: "SPORTS",
  politics: "POLITICS",
  weather: "WEATHER",
  any: "ALL MARKETS",
};

export interface StratTemplate {
  slug: string;
  name: string;
  /** One-liner shown under the name in the picker gallery. */
  description: string;
  /** Which market lane this recipe is for. Absent ⇒ "any". */
  lane?: StratLane;
  /** The recipe the shelf leads its lane with. At most one per lane — the hub
      sorts these to the front and badges them DEFAULT, and
      `defaultTemplateForLane` resolves them for anything that needs to ask
      "what do we suggest for Bitcoin?" in code. */
  isDefault?: boolean;
  /** Leaderboard query used to seed traders at fork time. */
  seed: {
    days?: number;
    minPerDay?: number;
    category?: string;
    marketQuery?: string;
    count?: number;
  };
  /** Strat fields layered over the blank-strat defaults. */
  params: Partial<SavedIndex>;
}

/** True when the recipe trades a market's own price tape rather than anyone
    else's flow. The inverse — everything else here — is copy trading. */
export function templateOriginates(t: StratTemplate): boolean {
  return !!t.params.momentum;
}

export const DEFAULT_STRATS: StratTemplate[] = [
  {
    slug: "copy-trades",
    name: "COPY TRADES",
    lane: "any",
    isDefault: true,
    description:
      "Plain copy trading, the COPY DESK's own settings: mirror the 5 best traders of the last week trade for trade — their buys AND their sells, so their exits are your exits — sized to the conviction behind each trade rather than to their net worth. Nothing resolving inside the hour, nothing older than five minutes.",
    seed: { days: 7, count: 5 },
    // Every number here is IMPORTED from the copy desk's identity template
    // (lib/identityStrat.ts), not retyped: the desk, the server's
    // `copy.rs::identity_strat` and this card have to be the same strategy, or
    // "copy trading" would mean one thing on /copy and another in the gallery.
    // The one thing that differs is the roster — the desk gives each leader
    // their own allocation, this is one strat over five of them.
    params: {
      // CONVICTION, not bankroll: our allocation spread across the capital the
      // leader actually deployed that window. `bankroll` sizing needs an
      // account in the leader's league to clear the $1 order floor, which is
      // how a small wallet ends up copying a whale and placing nothing.
      sizing: IDENTITY_SIZING,
      turnover: IDENTITY_TURNOVER,
      minTrade: IDENTITY_MIN_TRADE,
      maxTrade: IDENTITY_MAX_TRADE,
      maxPerCycle: IDENTITY_MAX_PER_CYCLE,
      maxOpenPositions: IDENTITY_MAX_OPEN_POSITIONS,
      stopLoss: IDENTITY_STOP_LOSS,
      takeProfit: IDENTITY_TAKE_PROFIT,
      // The two gates that make late mirroring survivable: no sub-hour Up/Down
      // candle (it resolves before a poller can react) and no stale fill from
      // a fetch backlog (it enters at a price the leader never paid).
      minMinutesToClose: IDENTITY_MIN_MINUTES_TO_CLOSE,
      maxTradeAgeSec: IDENTITY_MAX_TRADE_AGE_SEC,
      backtestDays: IDENTITY_BACKTEST_DAYS,
      rebalanceMinutes: IDENTITY_POLL_MINUTES,
      livePollMinutes: IDENTITY_POLL_MINUTES,
      // No `tradeFilters` on purpose. Copying is the whole strategy here: a
      // side or price band would drop the leader's exits (SELLS) and leave
      // this strat holding positions its leaders have already closed. The
      // filtered variants are the other cards on the shelf.
    },
  },
  // ── THE BITCOIN LANE ──
  // Three copy strats, then (further down) the two origination ones. Bitcoin
  // is the lane the console has the most measured history in and the only one
  // where a template can be wrong in a way that costs $600, so each of these
  // says out loud which slice of BTC flow it takes and which it refuses.
  {
    slug: "btc-copy",
    name: "BTC COPY",
    lane: "btc",
    isDefault: true,
    description:
      "Copy the best Bitcoin traders of the week, trade for trade — but only their BTC positions with an hour or more left on the clock, taken at 60¢ or better with real money behind them. Their sells are your sells. The 5-minute candle lane, which is where every measured dollar of copy-trading loss came from, is excluded by construction.",
    // `btc` is the bitcoin-only leaderboard bucket, not `crypto` — seeding from
    // crypto fills the roster with alt-coin traders whose BTC sample is empty.
    seed: { days: 7, category: "btc", count: 8 },
    params: {
      // Both the market slice AND (via TraderRoiStats) what "a good BTC
      // trader" is measured on. Two groups, comma-separated: a market titled
      // "BTC above $120k" never says "bitcoin" and vice versa.
      marketQuery: "bitcoin, btc",
      // $1000, not the $100 a first deposit suggests. Measured: at $100 the
      // proportional mirror of a BTC leader lands under the CLOB's $1 order
      // floor and is refused as SUB_SCALE — 0 entries copied out of 1287, with
      // the time-to-close gate switched OFF. Account size is the binding
      // constraint in this lane, not the gates.
      capital: 1000,
      sizing: IDENTITY_SIZING,
      turnover: IDENTITY_TURNOVER,
      minTrade: IDENTITY_MIN_TRADE,
      maxTrade: 50,
      // BTC leaders fire in bursts; 3/cycle deferred most of a burst to the
      // next poll, by which time the price has moved.
      maxPerCycle: 5,
      maxOpenPositions: 8,
      stopLoss: IDENTITY_STOP_LOSS,
      takeProfit: IDENTITY_TAKE_PROFIT,
      // THE gate this template exists for. A 5-minute candle can never have 60
      // minutes left, so 100% of that series is vetoed before sizing. Applies
      // to BUYs only — a leader SELL of something we hold is never gated, or
      // the strat would buy and never sell.
      minMinutesToClose: 60,
      // Tighter than the 300s house default: BTC reprices in seconds, so a
      // mirror from a five-minute-old fetch backlog enters at a price the
      // leader never paid.
      maxTradeAgeSec: 120,
      backtestDays: IDENTITY_BACKTEST_DAYS,
      rebalanceMinutes: IDENTITY_POLL_MINUTES,
      livePollMinutes: IDENTITY_POLL_MINUTES,
      tradeFilters: {
        sides: "buy",
        categories: ["btc"],
        // ≥60¢ is where the measured edge stops being negative: the 40–60¢
        // band lost −12.4pp over 458 trades (paid 53.4¢, settled 41.0¢) —
        // mirroring a leader late into a near-coin-flip is systematically
        // overpaying — while the 60¢+ bands came out roughly fair. The floor
        // also disposes of the sub-10¢ tails that went 0-for-145.
        minPrice: 0.6,
        // Skip bot dust. A leader putting $25 on it meant it.
        minNotional: 25,
      },
    },
  },
  {
    slug: "btc-swing-copy",
    name: "BTC SWING COPY",
    lane: "btc",
    description:
      "The same Bitcoin leaders, but only their multi-day positions — nothing resolving within six hours. On that horizon a 30-second mirror lag is nothing, so this one is allowed down to 45¢ and into bigger size: it's the lane where copying can actually be early rather than late.",
    // A longer look-back for the roster: someone who holds BTC views for days
    // shows up in a 14-day window and can be invisible in a 7-day one.
    seed: { days: 14, category: "btc", count: 10 },
    params: {
      marketQuery: "bitcoin, btc",
      capital: 1000,
      sizing: IDENTITY_SIZING,
      turnover: IDENTITY_TURNOVER,
      minTrade: IDENTITY_MIN_TRADE,
      maxTrade: 100,
      maxPerCycle: 3,
      // Fewer, larger, longer-held — the whole point of the horizon.
      maxOpenPositions: 5,
      stopLoss: IDENTITY_STOP_LOSS,
      takeProfit: IDENTITY_TAKE_PROFIT,
      // SIX HOURS. The candle lane is gone at 60 minutes; this goes further and
      // drops the whole intraday book, leaving the daily/weekly BTC price
      // markets. That is the only horizon on which "copy their entry" and
      // "enter where they entered" are the same sentence.
      minMinutesToClose: 360,
      maxTradeAgeSec: 300,
      backtestDays: 14,
      rebalanceMinutes: IDENTITY_POLL_MINUTES,
      livePollMinutes: IDENTITY_POLL_MINUTES,
      tradeFilters: {
        sides: "buy",
        categories: ["btc"],
        // Opened below the 60¢ floor the other BTC cards hold, deliberately.
        // The −12.4pp on the 40–60¢ band is a LATE-FILL artifact and it was
        // measured worst inside the sub-5m candles (50–60¢: −15.6pp) — the one
        // lane a 6-hour horizon has already excluded. 45¢ is as far down as
        // that argument reaches; below it the entry is a coin flip on its own
        // merits, not because we were slow.
        minPrice: 0.45,
        maxPrice: 0.92,
        minNotional: 50,
      },
    },
  },
  {
    slug: "filter",
    name: "FILTER",
    lane: "any",
    description:
      "Watches 20 traders, copies only the 5 with the best score right now — re-ranked every scan on their own realized returns, so a leader who starts bleeding is dropped automatically. Anyone who hasn't traded in 24h is dropped too.",
    // A wide net on purpose: the filter is what makes the roster small, and
    // it can only pick from who it can see. 20 in, 5 copied.
    seed: { days: 14, count: 20 },
    params: {
      filter: {
        metric: "score",
        topN: 5,
        // Positive expected edge required — a trader who is top-5 on a
        // watchlist where everyone is losing still isn't worth copying.
        minScore: 0,
        // 3 closed trades is where the stats stop being noise (see
        // TraderRoiStats.sampleSize).
        minSamples: 3,
        // Bleeding is one way a leader goes bad; stopping is the other, and
        // it's the one a returns-based score can't see (a dormant trader's
        // 30d numbers stay excellent). A day of silence sinks them below
        // every active name, so the top-5 slot goes to someone still trading.
        maxStaleHours: 24,
      },
      tradeFilters: { sides: "buy", minPrice: 0.6 },
    },
  },
  {
    slug: "top-allstars",
    name: "TOP 10 ALL-STARS",
    lane: "any",
    description:
      "Equal-weight the 10 best PnL traders of the last 7 days. Likely winners only (≥60¢) — skips the longshot / 5-min bot flow that a mirror lag can't win.",
    seed: { days: 7, count: 10 },
    // Favorite-side floor: never copy a sub-60¢ longshot buy. This is the
    // single most damaging leak on an unfiltered "all markets" strat — the
    // leader's 7–38¢ 5-min "Up or Down" bot buys, mirrored late, decay
    // straight to -100% (see the movoaev8 postmortem). Stated here rather
    // than imposed engine-wide, so the picker shows what it's doing.
    params: {
      tradeFilters: { sides: "buy", minPrice: 0.6 },
    },
  },
  {
    slug: "conservative-favorites",
    name: "CONSERVATIVE FAVORITES",
    lane: "any",
    description: "Copy only 65–95¢ favorite buys with ≥$50 conviction. Small, slow, steady.",
    seed: { days: 14, count: 10 },
    params: {
      maxTrade: 25,
      maxPerCycle: 1,
      maxOpenPositions: 5,
      tradeFilters: { sides: "buy", minPrice: 0.65, maxPrice: 0.95, minNotional: 50 },
    },
  },
  {
    slug: "longshot-hunter",
    name: "LONGSHOT HUNTER",
    lane: "any",
    description: "2–20¢ longshot buys only, tiny sizing — high variance, capped downside.",
    seed: { days: 30, count: 10 },
    params: {
      maxTrade: 10,
      maxPerCycle: 2,
      tradeFilters: { sides: "buy", minPrice: 0.02, maxPrice: 0.2 },
    },
  },
  {
    slug: "crypto-majors",
    name: "CRYPTO MAJORS",
    lane: "crypto",
    isDefault: true,
    description: "Top crypto traders, crypto markets only; $100+ conviction trades to skip Up/Down bot noise.",
    seed: { days: 7, category: "crypto", count: 10 },
    params: {
      tradeFilters: { sides: "buy", categories: ["crypto"], minNotional: 100, minPrice: 0.1, maxPrice: 0.9 },
    },
  },
  {
    slug: "btc-sharps",
    name: "BTC SHARPS",
    lane: "btc",
    description:
      "Bitcoin markets only, ranked by Sharpe — consistency rather than the one whale who got a big print right. Copies the 3 steadiest of 20, and only while they're still trading (≤6h).",
    // `btc` is the bitcoin-only bucket, not `crypto`: seeding from crypto
    // fills the roster with alt-coin traders whose BTC sample is empty, and
    // the filter then ranks them on returns earned somewhere else entirely.
    seed: { days: 14, category: "btc", count: 20 },
    params: {
      // The stats behind the ranking are computed on the strat's own market
      // slice, so this query is doing double duty: it picks the markets AND
      // it defines what "a good, still-active trader" is measured on.
      marketQuery: "bitcoin, btc",
      filter: {
        metric: "sharpe",
        topN: 3,
        // Sharpe is only defined at n ≥ 3 (below that stdev is noise and the
        // metric reads a flat 0), so the sample floor isn't optional here.
        minSamples: 3,
        // BTC markets turn over in minutes, not days: a trader who has been
        // quiet for six hours has no read on the current tape.
        maxStaleHours: 6,
      },
      // Skip the 5-minute Up/Down bot flow a mirror lag can never win —
      // the measured 0-for-145 on sub-5¢ late fills.
      tradeFilters: { sides: "buy", categories: ["btc"], minPrice: 0.6, minNotional: 50 },
    },
  },
  {
    slug: "sports-sharps",
    name: "SPORTS SHARPS",
    lane: "sports",
    isDefault: true,
    description: "Top sports bettors, sports markets only.",
    seed: { days: 7, category: "sports", count: 10 },
    params: {
      tradeFilters: { categories: ["sports"] },
    },
  },
  {
    slug: "politics-desk",
    name: "POLITICS DESK",
    lane: "politics",
    isDefault: true,
    description: "Top politics traders over 14 days, politics markets only.",
    seed: { days: 14, category: "politics", count: 10 },
    params: {
      tradeFilters: { categories: ["politics"] },
    },
  },
  {
    slug: "btc-momentum",
    name: "BTC MOMENTUM",
    lane: "btc",
    description:
      "No copying — watches Bitcoin markets' own odds and buys the side that's rising (50¢→60¢ = ride it), sells when the move flips or the position is down 20%. Skips sub-90-min markets.",
    // Origination-only: no watchlist to seed. The strat trades straight
    // from CLOB price history, so it runs with zero traders enrolled.
    seed: { count: 0 },
    params: {
      marketQuery: "bitcoin",
      maxPerCycle: 2,
      maxTrade: 25,
      // Same −20% floor as BTC 5-MIN DELTA: momentum entries are bought while
      // the odds are already high, so the downside leg is the long one.
      stopLoss: 0.8,
      momentum: {
        lookbackMinutes: 60,
        minRiseCents: 5,
        maxPositions: 5,
      },
    },
  },
  {
    slug: "btc-5min-delta",
    name: "BTC 5-MIN DELTA",
    lane: "btc",
    description:
      "Watches the LIVE Bitcoin 5-minute Up/Down candle: buys the ≥60¢ side while its odds are still climbing (+5¢ over 2m), sells the moment the delta flips −5¢ or the position is down 20%, winners auto-redeem. Tiny fixed sizes — this lane is HFT turf.",
    // Origination-only: trades the candle's own price tape, zero traders.
    seed: { count: 0 },
    params: {
      capital: 100,
      maxPerCycle: 1,
      minTrade: 1,
      maxTrade: 10,
      // Hard stop at −20% of entry (defend 80% of the buy-in), tighter than
      // the 25% house default. A 5-minute candle that turns against a ≥60¢
      // entry does not come back inside the candle — the delta-flip exit only
      // fires when the tape is still printing, and a gap straight down would
      // otherwise ride the position to 0 at resolution. Enforced live every
      // scan (live_engine `check_stop_losses`, against the book bid) and
      // replayed by the backtest sim.
      stopLoss: 0.8,
      // Wants 15s cycles — a 5-minute candle only lives ~20 of them and the
      // exit flip has to be seen with time left to act on it. The engine's
      // 30s rate-limit floor (MIN_POLL_MINUTES) clamps it up, so this strat
      // actually sees ~10 cycles per candle.
      livePollMinutes: 0.25,
      momentum: {
        candles: { slugPrefix: "btc-updown-5m", periodMinutes: 5 },
        // The user's rule, verbatim: ≥60¢ side, ±5¢ delta band.
        lookbackMinutes: 2,
        minRiseCents: 5,
        exitDropCents: 5,
        minPrice: 0.6,
        maxPrice: 0.9,
        maxPositions: 1,
        // Entries need ≥1 minute of candle left; the default 90 would veto
        // every sub-hour entry outright.
        minMinutesToClose: 1,
      },
    },
  },
  {
    slug: "crypto-convergence",
    name: "CRYPTO CONVERGENCE",
    lane: "crypto",
    description:
      "No copying — scans Bitcoin, Ethereum, Solana, XRP and Dogecoin markets for odds that are climbing, and buys the rising side while it's still 55–85¢ so there's room left to run. Sells into the convergence at 95¢ rather than waiting for the last nickel, or the moment the climb reverses.",
    // Origination-only: the signal is the market's own price tape, so there is
    // no watchlist to seed and the strat runs with zero traders enrolled.
    seed: { count: 0 },
    params: {
      // FIVE searches, not one. `momentum.query` inherits this, and every
      // discovery site splits it on commas and searches each coin separately
      // (see MAX_MOMENTUM_QUERIES). The commas are load-bearing: searched as
      // one phrase these five names return the "top performing crypto this
      // week" event family and none of the coins' own price markets — 50
      // markets against 250, with no overlap at all.
      marketQuery: "bitcoin, ethereum, solana, xrp, dogecoin",
      maxPerCycle: 2,
      maxTrade: 25,
      // The thesis, on the exit side: a market that has run to 95¢ is decided.
      // The last 5¢ is a 5% return for holding to resolution with 100% of the
      // downside still live, so the position is sold at 95 instead. This is
      // also why the entry band stops at 85¢ — the two numbers are the same
      // trade seen from both ends, and a 10¢ gap is what pays for it.
      takeProfit: 0.95,
      // Momentum entries are bought while the odds are already high, so the
      // losing leg is the long one — cut it at −20% of entry rather than
      // riding a reversal down to resolution.
      stopLoss: 0.8,
      momentum: {
        // An hour of context: long enough that a real repricing shows up,
        // short enough that it's still happening.
        lookbackMinutes: 60,
        minRiseCents: 6,
        // ...but the last 15 minutes may not have given any of it back. This
        // is the whole difference between "converging" and "already
        // converged and rolling over" — without it a market that ran 55¢→70¢
        // in the first ten minutes of the hour and has been sliding since
        // still reads as +15¢ and gets bought at the top of the move.
        confirmMinutes: 15,
        // Exit on a reversal of the same size as the entry signal.
        exitDropCents: 6,
        // The band IS the "before it converges to 100" rule: below 55¢ the
        // move hasn't established a favorite yet, above 85¢ the market has
        // already made its decision and there are only 15¢ left to win
        // against 85¢ of risk.
        minPrice: 0.55,
        maxPrice: 0.85,
        maxPositions: 4,
        // A wider net than the 12-market default, because it's now spread
        // across five assets rather than concentrated in one.
        maxMarkets: 20,
        // Stay out of the sub-hour Up/Down candle lane — that's HFT turf a
        // polling strat structurally loses (the movoaev8 postmortem). Four
        // hours also gives a 60-minute lookback room to mean something.
        minMinutesToClose: 240,
      },
    },
  },
  {
    slug: "weather-edge",
    name: "WEATHER EDGE",
    lane: "weather",
    isDefault: true,
    description: "Traders active on temperature markets — a niche where models beat vibes.",
    seed: { days: 14, marketQuery: "temperature", count: 10 },
    params: {
      marketQuery: "temperature",
    },
  },
];

// ── SHELF ORDER ──
//
// The array above is grouped for reading; this is the order the recipes are
// MET in. Bitcoin leads because it is the lane with the most measured history
// behind it, and inside every lane the copy strats come before the origination
// ones — that is the "copy trading is the default for BTC markets" rule,
// expressed once, in data, rather than re-decided by each screen that renders
// a shelf.

export const SHELF_ORDER: StratLane[] = ["btc", "any", "crypto", "sports", "politics", "weather"];

export function laneOf(t: StratTemplate): StratLane {
  return t.lane ?? "any";
}

/** Within a lane: the default first, then the other copy strats, then the
    origination ones. Ties keep their order in DEFAULT_STRATS. */
function rankInLane(t: StratTemplate): number {
  if (t.isDefault) return 0;
  return templateOriginates(t) ? 2 : 1;
}

/** Every recipe, lane by lane, each lane's default first. */
export function orderedTemplates(templates: StratTemplate[] = DEFAULT_STRATS): StratTemplate[] {
  return [...templates].sort((a, b) => {
    const lane = SHELF_ORDER.indexOf(laneOf(a)) - SHELF_ORDER.indexOf(laneOf(b));
    if (lane !== 0) return lane;
    const rank = rankInLane(a) - rankInLane(b);
    if (rank !== 0) return rank;
    return templates.indexOf(a) - templates.indexOf(b);
  });
}

/** The recipes grouped by lane, in shelf order, empty lanes dropped. Pass a
    filtered list (the hub passes its search results) and the grouping follows
    the filter. */
export function templatesByLane(
  templates: StratTemplate[] = DEFAULT_STRATS,
): { lane: StratLane; label: string; templates: StratTemplate[] }[] {
  const ordered = orderedTemplates(templates);
  return SHELF_ORDER.map((lane) => ({
    lane,
    label: LANE_LABEL[lane],
    templates: ordered.filter((t) => laneOf(t) === lane),
  })).filter((g) => g.templates.length > 0);
}

/** What this console suggests for a lane. `defaultTemplateForLane("btc")` is
    BTC COPY — a copy-trading strat — and that is load-bearing: anything asking
    "what should a Bitcoin strat be?" gets the same answer as the shelf. */
export function defaultTemplateForLane(lane: StratLane): StratTemplate | undefined {
  return DEFAULT_STRATS.find((t) => laneOf(t) === lane && t.isDefault);
}

/// Leaderboard rosters are cached briefly: the strat browser resolves one per
/// template to backtest it, and a fork made moments later must get the SAME
/// traders — otherwise the card's number describes a strat you didn't get.
const ROSTER_PREFIX = "poly_tpl_roster_";
const ROSTER_TTL_MS = 3 * 3600_000;

/// The traders a template would seed itself with right now.
export async function templateRoster(t: StratTemplate): Promise<string[]> {
  const count = t.seed.count ?? 10;
  if (count === 0) return [];
  const key = ROSTER_PREFIX + t.slug;
  if (typeof window !== "undefined") {
    try {
      const raw = localStorage.getItem(key);
      if (raw) {
        const c = JSON.parse(raw) as { addrs: string[]; ts: number };
        if (Date.now() - c.ts < ROSTER_TTL_MS && c.addrs?.length > 0) return c.addrs;
      }
    } catch {
      // Unreadable entry — fall through and re-query.
    }
  }
  const addrs = await fetchTopTraderAddresses(
    {
      days: t.seed.days,
      minPerDay: t.seed.minPerDay,
      category: t.seed.category,
      marketQuery: t.seed.marketQuery,
    },
    count,
  );
  if (typeof window !== "undefined" && addrs.length > 0) {
    try {
      localStorage.setItem(key, JSON.stringify({ addrs, ts: Date.now() }));
    } catch {
      // Quota — a re-query is cheap enough.
    }
  }
  return addrs;
}

/// A template as the SavedIndex forking it produces — the recipe's params
/// layered over the blank-strat defaults, with a resolved watchlist.
///
/// Both callers go through here: `forkDefaultStrat` (which then persists it)
/// and the strat browser's backtest (`lib/stratScan.ts`, which never persists
/// anything). That's deliberate — a template's card has to describe the strat
/// you'd actually get, so the two can't be allowed to define it differently.
export function templateIndex(t: StratTemplate, addrs: string[] = [], now = Date.now()): SavedIndex {
  return {
    id: now.toString(36),
    name: uniqueIndexName(t.name),
    forkedFrom: t.slug,
    traders: addrs.length > 0 ? equalWeightTraders(addrs) : [],
    backtestDays: 7,
    rebalanceMinutes: 0.5,
    livePollMinutes: 0.5,
    capital: 1000,
    minTrade: 1,
    maxTrade: 100,
    maxPerCycle: 3,
    ...t.params,
    createdAt: now,
    updatedAt: now,
  };
}

/// Fork a template into a real, user-owned strat and make it active.
/// Returns the new strat immediately (empty trader list); trader seeding
/// runs async and reports back through `onSeeded` so callers can re-render
/// and push the seeded version to the server.
export function forkDefaultStrat(
  t: StratTemplate,
  onSeeded?: (seeded: SavedIndex) => void,
): SavedIndex {
  const idx = templateIndex(t);
  saveIndex(idx);
  setActiveIndexId(idx.id);

  // Origination-only templates (seed.count: 0) trade from market data and
  // deliberately start with an empty watchlist — nothing to seed.
  if ((t.seed.count ?? 10) === 0) return idx;

  templateRoster(t).then((addrs) => {
    if (addrs.length === 0) return;
    const traders = equalWeightTraders(addrs);
    updateIndex(idx.id, { traders, updatedAt: Date.now() });
    onSeeded?.({ ...idx, traders });
  });

  return idx;
}
