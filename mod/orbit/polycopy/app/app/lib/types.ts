// ── Trade Types ──────────────────────────────────────────────

export interface Trade {
  txHash: string;
  tradeId: number;
  timestamp: number;
  chainId: number;
  tokenIn: string;
  tokenOut: string;
  amountIn: string;
  amountOut: string;
  proxyAddress: string;
  pnl?: number;
  pnlPct?: number;
}

// ── Portfolio ────────────────────────────────────────────────

export interface PortfolioSnapshot {
  timestamp: number;
  totalValueUSD: number;
}

// ── Trader Classification ────────────────────────────────────

export type TraderType = "whale" | "scalper" | "swing" | "sniper" | "degen";

export const TRADER_TYPE_LABELS: Record<TraderType, string> = {
  whale: "WHALE",
  scalper: "SCALPER",
  swing: "SWING",
  sniper: "SNIPER",
  degen: "DEGEN",
};

export const TRADER_TYPE_COLORS: Record<TraderType, string> = {
  whale: "#4589ff",
  scalper: "#08bdba",
  swing: "#f1c21b",
  sniper: "#42be65",
  degen: "#fa4d56",
};

// ── Strategy Performance ─────────────────────────────────────

export interface StrategyPerformance {
  address: string;
  label: string;
  chainId: number;
  totalPnlUSD: number;
  totalPnlPct: number;
  winRate: number;
  totalTrades: number;
  profitableTrades: number;
  totalVolume: number;
  apr1d: number;
  apr30d: number;
  sharpeRatio: number;
  maxDrawdown: number;
  history: PortfolioSnapshot[];
  traderType: TraderType;
  avgTradeSize: number;
  tradesPerDay: number;
}

// ── Polymarket ───────────────────────────────────────────────

export interface PolymarketMarket {
  id: string;
  question: string;
  outcomePrices: number[];
  volume: number;
  liquidity: number;
  endDate?: string;
  category?: string;
}

export interface PolymarketPosition {
  conditionId: string;
  market: string;
  outcome: string;
  size: number;
  avgPrice: number;
  currentPrice: number;
  value: number;
  pnlUsd: number;
  pnlPct: number;
}

// ── Wallet ───────────────────────────────────────────────────

export type WalletType = "metamask" | "phantom";

export interface WalletState {
  connected: boolean;
  address: string | null;
  chainId: number | null;
  walletType: WalletType | null;
  balance: string;
}

// ── Backtest ─────────────────────────────────────────────────

export interface BacktestConfig {
  scoreFn: string;
  threshold: number;
  startDate: number;
  endDate: number;
  initialCapital: number;
  positionPct: number;
}

export interface BacktestTrade {
  action: "COPY" | "SKIP";
  timestamp: number;
  wallet: string;
  score: number;
  tokenIn: string;
  tokenOut: string;
  amountUsd: number;
  pnl: number;
  portfolioValue: number;
}

export interface BacktestResult {
  totalPnlUsd: number;
  totalPnlPct: number;
  winRate: number;
  totalTrades: number;
  sharpeRatio: number;
  maxDrawdown: number;
  avgTradeSize: number;
  equityCurve: PortfolioSnapshot[];
  trades: BacktestTrade[];
}
