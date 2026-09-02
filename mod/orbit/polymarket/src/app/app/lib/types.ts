// Polymarket 8-bit types

import type { SentimentFilter } from "./marketSentiment";
export type { SentimentFilter, SentimentLean, MarketSentiment } from "./marketSentiment";

/** What a mirror is sized proportionally TO — see `copyRatioFor` (strat.ts).
    "bankroll" = the leader's net worth (risk fidelity, needs capital in their
    league); "flow" = the capital they deployed in the window (conviction
    fidelity, placeable on a small account). */
export type SizingModel = "bankroll" | "flow";

export interface PolymarketMarket {
  id: string;
  conditionId: string;
  question: string;
  category: string;
  endDate: string;
  volume: number;
  liquidity: number;
  outcomePrices: number[];
  outcomes: string[];
  active: boolean;
  image?: string;
  description?: string;
  slug?: string;
  clobTokenIds?: string[];
}

export interface PolymarketTrade {
  id: string;
  market: string;
  /// Market slug from the activity row ("btc-updown-5m-1785966300"). Recurring
  /// candle series encode their period + start here, which is the only thing
  /// in a trade that dates the market it was placed in — see
  /// `minutesToCloseFromTrade`. Absent on rows the data-api didn't slug.
  slug?: string;
  conditionId: string;
  /// CTF outcome-token id (the activity row's `asset`) — the Yes leg and the
  /// No leg of one condition are different tokens, which is what makes fill
  /// aggregation able to tell two legs of one transaction apart. Absent on
  /// rows stored before this was carried.
  asset?: string;
  side: "BUY" | "SELL";
  price: number;
  size: number;
  /// USDC that actually moved, as reported by the data-api's `usdcSize`.
  /// NOT `price * size`: Polymarket charges a sell-side fee (observed as
  /// `k · price · (1-price) · shares`, k in 4–7%), so on a SELL the gross and
  /// the proceeds differ — ~46% of a live roster's SELL rows carried one,
  /// worth ~0.5% of sell notional. Absent on rows fetched before this field
  /// existed; callers that need money should prefer it and fall back to
  /// `price * size`.
  usdcSize?: number;
  pnl: number;
  timestamp: number;
  outcome?: string;
  fee?: number;
}

export interface PolymarketPosition {
  conditionId: string;
  /// CTF outcome token id (uint256 decimal string). Required for any SELL
  /// flow that hits /order directly — the data-api returns it as `asset`,
  /// we surface it here so callers don't need a separate markets lookup.
  tokenId: string;
  market: string;
  outcome: string;
  size: number;
  avgPrice: number;
  currentPrice: number;
  value: number;
  pnlUsd: number;
  /// Whether the market lives on the negRisk exchange. SELL orders must
  /// be signed against the matching exchange contract or CLOB rejects them
  /// as "bad signature".
  negRisk: boolean;
  /// Market has RESOLVED — this position can't be SOLD (no order book), only
  /// REDEEMED (winning tokens → USDC) via /redeem. Drives the REDEEM button.
  redeemable: boolean;
}

export interface ClobCredentials {
  apiKey: string;
  secret: string;
  passphrase: string;
}

export interface AuthState {
  connected: boolean;
  address: string | null;
  chainId: number | null;
  clobCreds: ClobCredentials | null;
  authenticated: boolean;
}

export interface IndexTrader {
  address: string;
  weight: number; // 0.0-1.0
  enabled?: boolean; // undefined/true = active, false = hidden
}

// ── Trader scoring (Sharpe-like EV) ────────────────────────────
// Used by the copy engine + backtest to rank candidate BUY trades.
// `roi` is realized return per dollar of cash deployed in the window
// (e.g. 0.18 = +18%). `stdev` is the stdev of per-closed-trade
// fractional returns — division yields the unitless Sharpe used as
// the trader's quality multiplier on each candidate's notional.
export interface TraderRoiStats {
  address: string;
  windowDays: number;
  roi: number;
  stdev: number;
  // Closed-trade sample size. Below ~3 the stdev is too noisy to trust;
  // callers should fall back to roi-only or skip the trader.
  sampleSize: number;
  // Sharpe = roi / stdev (0 when stdev is 0 or sampleSize too low).
  sharpe: number;
  // Closed trades that realized a profit (returns > 0) in the window.
  wins: number;
  // Probability-of-success estimate for this trader's next trade:
  // Laplace-smoothed win rate `(wins + 2) / (sampleSize + 4)` — shrinks
  // toward 50% on thin samples, 0.5 exactly when no closed trades exist.
  // Same formula as `stats_from_returns` in live_engine.rs; the parity
  // fixture test asserts the two never drift.
  successProb: number;
  // Cash deployed in the window (sum of BUY notional). Surfaces
  // "is this a real trader" to the UI.
  cashDeployed: number;
  // Ms-epoch timestamp of the trader's most recent in-window trade — the
  // freshness the `maxStaleHours` gate reads. 0/undefined = no trade seen in
  // the window (which the gate treats as stale, not as unknown-but-fine).
  // Mirrors `last_trade_at` on the Rust TraderRoiStats.
  lastTradeAt?: number;
  syncedAt: number;
}

/** Semantic per-trade filters that decide WHICH of a watched trader's fills a
    strat actually mirrors. Distinct from `marketQuery` (the free-text topic
    match on the market title): these gate on the trade's own attributes —
    side, the leader's fill price, the leader's USD size, and the market's
    category bucket. Every active dimension is AND-ed together, and the whole
    set is AND-ed with `marketQuery`. Two strats watching the SAME traders with
    different trade-filters copy different slices of their flow — this is what
    makes a strat unique.

    Mirror of the per-trade gate in `lib/tradeFilters.ts` (TS engine + backtest)
    and `EngineConfig`/`trade_passes_filters` in `src/api/src/live_engine.rs`
    (Rust live engine). Keep the three in sync. */
export interface TradeFilters {
  /** Which sides to mirror. "buy" = entries only, "sell" = exits only,
      "both"/undefined = no side restriction. */
  sides?: "buy" | "sell" | "both";
  /** Leader fill-price band (0–1 probability). e.g.
      {minPrice:0.01,maxPrice:0.2} = longshots only; {minPrice:0.8} =
      favorites only. Undefined ends mean no gate on that end — there is no
      implicit floor, so a strat that sets nothing copies the flow whole. */
  minPrice?: number;
  maxPrice?: number;
  /** Leader trade USD notional band (price × size). A conviction filter:
      skip dust, skip whales. undefined ⇒ no bound on that end. */
  minNotional?: number;
  maxNotional?: number;
  /** Category slugs (politics/crypto/sports/…) the market title must match
      at least ONE of. Empty/undefined ⇒ all categories. Uses the same
      keyword buckets as the leaderboard category filter. */
  categories?: string[];
  /** MARKET SENTIMENT — the only dimension here that is not a property of the
      trade itself but of the MARKET it was taken in: which way the crowd has
      moved the leader's own outcome token over the last few hours.
      `{lean:["bearish"]}` copies only their contrarian entries;
      `{lean:["bullish"], minDrift:0.05}` only the ones taken into odds that
      have already gained 5¢. See lib/marketSentiment.ts — undefined ⇒ no
      sentiment gate and no price-history fetch at all. */
  sentiment?: SentimentFilter;
}

/** Which per-trader number the FILTER ranks on. All four are derived from the
    same `TraderRoiStats` the engine already computes every cycle, so the
    ranking means the same thing live and in backtest.

    - `score`    P(success) × ROI — expected edge per dollar copied. This is
                 `scoreCandidate` with the trade's notional divided out, i.e.
                 the trader half of THE playbook score. Default.
    - `sharpe`   ROI / stdev of per-trade returns — consistency over size.
    - `roi`      raw mean return per closed trade.
    - `winRate`  Laplace-smoothed hit rate (`successProb`). */
export type TraderMetric = "score" | "sharpe" | "roi" | "winRate";

/** Keep only the BEST traders on the watchlist — the strat-level counterpart
    to `TradeFilters` (which gates individual trades). Every cycle the engine
    ranks the enabled watchlist by `metric` and mirrors only the top `topN`;
    everyone else is observed but never copied.

    Why it exists: a watchlist seeded from a leaderboard rots. A trader who
    was top-10 last month can be the one bleeding this week, and an
    equal-weight copy strat keeps mirroring them until a human notices. The
    filter re-ranks continuously from the traders' OWN realized returns in the
    strat's slice of the market, so the strat follows the score rather than
    the roster.

    Mirror of `TraderFilter` / `select_top_traders` in
    `src/api/src/live_engine.rs` — pinned across both languages by
    parity.fixture.json `traderFilterCases`. */
export interface TraderFilter {
  /** Ranking metric. Default "score". */
  metric?: TraderMetric;
  /** Keep the top N traders by that metric. 0 ⇒ no rank cut (only the
      thresholds below apply). Default 5. */
  topN?: number;
  /** Hard floor on the metric — a trader inside the top N but below this is
      cut too. Undefined ⇒ no floor. `0` on the default metric means "must be
      expected-profitable". */
  minScore?: number;
  /** Traders with fewer closed trades than this in the 30d window are
      unrankable and cut (their stats are noise). Default 0 = off. */
  minSamples?: number;
  /** FRESHNESS gate — drop any trader whose most recent trade is older than
      this many hours. Undefined/0 = off.

      Unlike `minScore`/`minSamples` (quality thresholds applied AFTER the
      ranking), staleness is an ELIGIBILITY gate: stale traders sort below
      every fresh one, so they never occupy a top-N slot that a trader who is
      still trading could fill. A watchlist where the four best 30d scores
      went quiet a week ago would otherwise copy nobody.

      A trader with no known last trade (`lastTradeAt` 0/absent) counts as
      stale — asking for freshness and getting silence is the same answer. */
  maxStaleHours?: number;
}

/** Price-momentum origination params. Setting `momentum` (even `{}`) turns
    origination ON: each cycle the engine fetches CLOB price history for
    active markets matching `query` (default: the strat's marketQuery, else
    "bitcoin") and the strat BUYs the outcome whose price ROSE by at least
    `minRiseCents` over the lookback — "the odds of BTC-up went 50¢ → 60¢,
    ride it" — and SELLs a held outcome once its price momentum flips down
    by `exitDropCents`. Lives in types.ts (not strat.ts) so SavedIndex can
    reference it without an import cycle. */
export interface MomentumParams {
  /** Market search query for candidate markets. Default: the strat's
      `marketQuery`, else "bitcoin".

      Comma/pipe-separated groups are searched SEPARATELY and the results
      merged (deduped by condition id) — "bitcoin, ethereum, solana" is three
      searches, not one. Gamma ranks a multi-word query by whole-phrase
      relevance, so asking it for five coins at once returns the one event
      family that happens to NAME five coins ("Will X be the top performing
      crypto this week?") and not one of the coins' own price markets.
      Measured on the five-coin query: 50 markets whose top 20 by volume total
      ~$315k, against 250 markets / ~$53M for the same five as separate
      searches, with ZERO overlap between the two sets. Groups past
      `MAX_MOMENTUM_QUERIES` are dropped. */
  query?: string;
  /** Window (minutes) the rise is measured over. Default 60. */
  lookbackMinutes?: number;
  /** Minimum rise in CENTS of probability over the lookback before an
      entry fires (10 = the 50¢→60¢ example). Default 5. */
  minRiseCents?: number;
  /** Exit: sell a held outcome once its price FALLS this many cents over
      the lookback. Default = minRiseCents. */
  exitDropCents?: number;
  /** ENTRY confirmation window (minutes): the rise must still be intact over
      the last `confirmMinutes`, i.e. the outcome may not have given ground
      inside that sub-window. 0 / undefined ⇒ off (the single-lookback
      behavior).

      Why it exists: `minRiseCents` compares two points — the price now
      against the price one lookback ago — and says nothing about the shape in
      between. A market that ran 55¢ → 70¢ in the first ten minutes of the
      hour and has been sliding back since still reads as "+10¢ over 60m" and
      gets bought at the worst moment of the move. Confirming against a short
      recent window is the cheapest way to tell a move that is still going
      from one that already happened.

      Only ENTRIES are confirmed. Exits stay on the raw lookback: a gate whose
      job is to make us slower to buy must never make us slower to sell. */
  confirmMinutes?: number;
  /** Entry price band — don't chase near-resolved (or dead) markets.
      Defaults 0.5 / 0.85: entries stick to the likely-to-win side by
      default (momentum rides the favorite side; the copy path has no floor);
      set an explicit minPrice (e.g. 0.15) to allow cheaper entries. */
  minPrice?: number;
  maxPrice?: number;
  /** Max simultaneous open positions momentum may hold. Default 5. */
  maxPositions?: number;
  /** How many top-volume matching markets to track per cycle. Default 12. */
  maxMarkets?: number;
  /** Skip markets resolving sooner than this (minutes). Sub-hour Up/Down
      markets are HFT-bot turf where a polling strat structurally loses —
      see the movoaev8 postmortem. Default 90. */
  minMinutesToClose?: number;
  /** Opt-in short-CANDLE feed: instead of market search, track the ONE
      live candle of a recurring sub-hour series (e.g. BTC "Up or Down"
      5-minute markets) by its deterministic slug
      (`<slugPrefix>-<candle start unix seconds>`), at 1-minute price
      fidelity plus a near-live midpoint. Deliberately opt-in: it targets
      exactly the sub-hour lane `minMinutesToClose` exists to avoid, so a
      strat using it must also set `minMinutesToClose` low (e.g. 1) or no
      entry can ever fire. */
  candles?: {
    /** Series slug prefix. Default "btc-updown-5m". */
    slugPrefix?: string;
    /** Candle length in minutes. Default 5. */
    periodMinutes?: number;
  };
}

export interface SavedIndex {
  id: string;
  name: string;
  traders: IndexTrader[];
  backtestDays?: number;
  capital?: number; // simulation capital in USD (default 1000)
  // Backtest funds source: "SIM" sizes the replay with `capital` (paper —
  // works with no wallet and no deposit); "WALLET" mirrors the deposit
  // wallet's live USDC balance so the preview matches what would actually
  // deploy. Default SIM.
  fundsMode?: "SIM" | "WALLET";
  minTrade?: number; // minimum trade size in USD (default 5)
  maxTrade?: number; // maximum trade size in USD (default 100)
  /** @deprecated Never enforced by the live engine or the backtest — kept
      only so previously saved strats still deserialize. Rate control is
      `maxPerCycle` × poll cadence. */
  maxTradesPerHour?: number;
  // Max concurrent open positions (default 10). The live engine skips a
  // mirror BUY that would open a NEW token while this many are already held;
  // topping up an existing hold still goes through.
  maxOpenPositions?: number;
  // Per-position stop-loss: the fraction of avg entry price to defend (0–1).
  // e.g. 0.75 ⇒ sell the whole position once its market price decays to ≤ 75%
  // of entry — caps a market trending toward 0 at a known loss instead of
  // riding it down. Enforced live by the Rust engine (`EngineConfig.stopLoss`,
  // checked against the book bid every cycle) and replayed by the BACKTEST
  // sim so the preview predicts the exits. undefined ⇒ the 0.75 default
  // applies (protection on unless deliberately disabled); explicit 0 ⇒ off.
  stopLoss?: number;
  // Per-position take-profit: the ABSOLUTE price level (0–1) at which a held
  // position is fully liquidated at the bid. A market that has run to ~100%
  // is decided — ≤1¢ left to earn, capital dead until resolution — so the
  // engine sells instead of waiting for auto-redeem. Levels above 0.99 clamp
  // to 0.99 (the book's top tick; a 1.00 bid never prints). undefined ⇒ the
  // 0.99 default applies (liquidate everything that runs to the top);
  // explicit 0 ⇒ off.
  takeProfit?: number;
  rebalancePeriod?: number; // rebalance period in hours (default 24)
  rebalanceHour?: number; // hour of day to rebalance 0-23 (default 0 = midnight)
  rebalanceMinutes?: number; // BACKTEST-only poll cadence (historical sim aggregation)
  livePollMinutes?: number; // LIVE engine scan interval in minutes (default 1)
  liveEnabled?: boolean; // whether live copy-trading is active
  // Top-N sampling: per cycle, score every observed BUY candidate as
  // score = trader_sharpe_30d * trade_notional, then copy only the top N.
  // Suppresses fee-burn from spamming every observed trade.
  maxPerCycle?: number; // default 3
  // Free-text market-topic filter (e.g. "price of bitcoin"). When set, the
  // strat only acts on markets whose title matches the query — backtest, live
  // mirror, and catch-up all honor it. Empty/undefined ⇒ all markets.
  marketQuery?: string;
  // Semantic per-trade filters (side / price band / size band / category).
  // AND-ed with marketQuery to carve a unique slice of the watched flow.
  // Empty/undefined ⇒ no per-trade gating beyond marketQuery.
  tradeFilters?: TradeFilters;
  // Trader-quality gate: re-rank the watchlist every cycle and only copy the
  // top scorers (see TraderFilter). Absent ⇒ every enabled trader is copied.
  filter?: TraderFilter;
  // Opt-in price-momentum ORIGINATION (no watchlist needed): the engine
  // feeds the strat CLOB price history for markets matching the query and
  // the strat buys the outcome whose odds are RISING (e.g. 50¢ → 60¢),
  // exits when the momentum flips. Absent ⇒ pure copy strat.
  momentum?: MomentumParams;
  // ── Proportional-copy fidelity ──
  // How far a mirror may be upsized past its proportional notional when that
  // notional lands under the order floor. 2 ⇒ "never place more than 2× what
  // proportionality asked for"; smaller intents are skipped (SUB_SCALE)
  // instead of all being placed at the same minimum. undefined ⇒ 2; explicit
  // 0/null ⇒ legacy unbounded clamp-to-floor.
  maxUpscale?: number | null;
  // What mirrors are sized proportionally TO (see copyRatioFor).
  // "bankroll" (default) copies the leader's RISK — the fraction of net worth
  // they staked — and needs capital in their league to clear the order floor.
  // "flow" copies their CONVICTION: our allocation split across the capital
  // they deployed that window, so a $223 strat still places real orders on
  // the trades its FILTER selected instead of SUB_SCALE-skipping them.
  sizing?: SizingModel;
  // "flow" only — how many times the allocation may be deployed across one
  // window of leader flow. undefined ⇒ 1.
  turnover?: number;
  // Don't mirror a leader BUY in a market resolving sooner than this many
  // minutes — sub-hour Up/Down candles resolve before a poller can react.
  // undefined ⇒ 60; explicit 0 ⇒ off.
  minMinutesToClose?: number;
  // Don't mirror a leader BUY older than this many seconds — a backlog after
  // a fetch outage enters at prices the leader never paid. OPT-IN:
  // undefined ⇒ off (no age gate at all), explicit 0 ⇒ off.
  maxTradeAgeSec?: number;
  // Strat this one was forked from (id of a SavedIndex, or the slug of a
  // built-in template). Lineage only — a fork is a full, independent copy.
  forkedFrom?: string;
  // IDENTITY strat: this strat mirrors exactly ONE leader — the address here.
  // The watchlist is that single trader at weight 1; the field marks the
  // strat's class so the UI can badge it and keep the roster single-trader.
  identity?: string;
  // Sharing class. Absent/"private" = only this account sees it (the
  // default for every strat); "public" = also published, PLAINTEXT, to the
  // server's community gallery where anyone can view and fork it. Forks and
  // imports always come back private.
  visibility?: "private" | "public";
  // EOA that published this strat — gallery attribution, set at publish
  // time. Meaningless (and stripped) on private strats.
  owner?: string;
  // Capital the CAPITAL PLAN last recommended for this strat (USD) and when
  // it was computed. Written by the strat editor, which is the only place
  // with the watchlist's trade history loaded; read by the funding sidebar so
  // "how much should I run this with" is answerable without re-fetching.
  suggestedCapital?: number;
  suggestedCapitalAt?: number;
  createdAt: number;
  updatedAt: number;
  // Cached backtest snapshot (updated each time backtest runs)
  lastPnl?: number;
  lastPnlAfterCosts?: number;
  lastRoi1k?: number;
  lastTradeCount?: number;
  lastBacktestAt?: number;
}
