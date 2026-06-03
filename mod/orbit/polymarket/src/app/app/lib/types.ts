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

export interface SavedIndex {
  id: string;
  name: string;
  traders: IndexTrader[];
  backtestDays?: number;
  capital?: number; // simulation capital in USD (default 1000)
  minTrade?: number; // minimum trade size in USD (default 1)
  maxTrade?: number; // maximum trade size in USD (default 100)
  maxTradesPerHour?: number; // maximum trades per hour (default 10)
  rebalancePeriod?: number; // rebalance period in hours (default 24)
  rebalanceHour?: number; // hour of day to rebalance 0-23 (default 0 = midnight)
  rebalanceMinutes?: number; // BACKTEST-only poll cadence (historical sim aggregation)
  livePollMinutes?: number; // LIVE engine scan interval in minutes (default 1)
  liveEnabled?: boolean; // whether live copy-trading is active
  createdAt: number;
  updatedAt: number;
  // Cached backtest snapshot (updated each time backtest runs)
  lastPnl?: number;
  lastPnlAfterCosts?: number;
  lastRoi1k?: number;
  lastTradeCount?: number;
  lastBacktestAt?: number;
}
