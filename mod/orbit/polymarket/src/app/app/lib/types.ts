// Polymarket 8-bit types

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
  conditionId: string;
  side: "BUY" | "SELL";
  price: number;
  size: number;
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
  // Cash deployed in the window (sum of BUY notional). Surfaces
  // "is this a real trader" to the UI.
  cashDeployed: number;
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
  /** Leader fill-price band (0–1 probability). undefined ⇒ no bound on that
      end. e.g. {minPrice:0.01,maxPrice:0.2} = longshots only;
      {minPrice:0.8} = favorites only. */
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
  maxTradesPerHour?: number; // maximum trades per hour (default 10)
  // Max concurrent open positions (default 10). The live engine skips a
  // mirror BUY that would open a NEW token while this many are already held;
  // topping up an existing hold still goes through.
  maxOpenPositions?: number;
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
  createdAt: number;
  updatedAt: number;
  // Cached backtest snapshot (updated each time backtest runs)
  lastPnl?: number;
  lastPnlAfterCosts?: number;
  lastRoi1k?: number;
  lastTradeCount?: number;
  lastBacktestAt?: number;
}
