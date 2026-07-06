export interface TokenStats {
  symbol: string;
  volume_usd: number;
  net_flow_usd: number;
  swap_count: number;
}

export interface PoolStats {
  pool_id: string;
  token0: string;
  token1: string;
  fee_tier: number;
  volume_usd: number;
  swap_count: number;
  pnl_usd: number;
}

export interface MevIndicators {
  sandwich_count: number;
  arb_count: number;
  avg_swaps_per_day: number;
  min_swap_interval_sec: number;
  high_volume_pool_ratio: number;
}

export interface Trader {
  address: string;
  chain: string;
  total_volume_usd: number;
  buy_volume_usd: number;
  sell_volume_usd: number;
  swap_count: number;
  active_days: number;
  avg_trade_size: number;
  realized_pnl_usd: number;
  win_rate: number;
  pnl_curve: number[];
  volume_curve: number[];
  top_tokens: TokenStats[];
  token_concentration: number;
  pools_traded: PoolStats[];
  unique_pools: number;
  pool_diversity_score: number;
  is_mev_bot: boolean;
  mev_indicators: MevIndicators;
  composite_score: number;
}

export interface ScrapeProgress {
  type: "progress";
  phase: string;
  chain: string;
  done: number;
  total: number;
  kept?: number;
}

export type Chain = "ethereum" | "arbitrum" | "base" | "polygon" | "optimism";

export const CHAINS: { id: Chain; label: string; color: string }[] = [
  { id: "ethereum", label: "Ethereum", color: "#627eea" },
  { id: "arbitrum", label: "Arbitrum", color: "#28a0f0" },
  { id: "base", label: "Base", color: "#0052ff" },
  { id: "polygon", label: "Polygon", color: "#8247e5" },
  { id: "optimism", label: "Optimism", color: "#ff0420" },
];

export const TIME_WINDOWS = [
  { days: 30, label: "30d" },
  { days: 14, label: "14d" },
  { days: 7, label: "7d" },
  { days: 1, label: "24h" },
];
