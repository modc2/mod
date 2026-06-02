export type SubnetInfo = {
  netuid: number;
  name: string;
  alpha_price_tao: number;
  total_stake_tao: number;
  tempo: number;
  emission: number;
};

export type Allocation = {
  netuid: number;
  subnet_name: string;
  hotkey: string;
  alpha_amount: number;
  alpha_price_tao: number;
  value_tao: number;
  pct_of_total: number;
};

export type AccountData = {
  ss58: string;
  total_stake_tao: number;
  allocations: Allocation[];
  pnl_tao: number;
  pnl_pct: number;
  days: number;
};

export type SubnetPnl = {
  netuid: number;
  subnet_name: string;
  alpha_start: number;
  alpha_end: number;
  price_start_tao: number;
  price_end_tao: number;
  value_start_tao: number;
  value_end_tao: number;
  pnl_tao: number;
  pnl_pct: number;
};

export type PnlData = {
  ss58: string;
  days: number;
  block_start: number;
  block_end: number;
  start_value_tao: number;
  end_value_tao: number;
  pnl_tao: number;
  pnl_pct: number;
  by_subnet: SubnetPnl[];
};

export type LeaderboardEntry = {
  ss58: string;
  label: string | null;
  total_stake_tao: number;
  pnl_tao: number;
  pnl_pct: number;
  num_subnets: number;
  top_subnet: number | null;
  top_subnet_pnl: number;
};

export type CopyConfig = {
  id: string;
  target_ss58: string;
  label: string | null;
  status: string;
  config: Record<string, any>;
  last_sync_block: number | null;
  created_at: string | null;
  updated_at: string | null;
};

export type Trade = {
  id: string;
  copy_id: string;
  block: number | null;
  timestamp: string;
  action: string;
  netuid: number;
  amount_tao: number;
  tx_hash: string | null;
  status: string;
  error: string | null;
};

export type AccountWatch = {
  ss58: string;
  label: string | null;
  added_at: string | null;
};

// ── Index of traders (basket copy, polymarket-style) ─────────────
export type IndexTrader = {
  ss58: string;
  label?: string | null;
  weight: number; // any positive number; UI normalizes to sum=1
  enabled?: boolean; // default true
};

export type SavedIndex = {
  id: string;
  name: string;
  traders: IndexTrader[];
  our_hotkey?: string;
  max_tao_per_tx?: number;
  daily_limit_tao?: number;
  rebalance_threshold_pct?: number;
  poll_interval_sec?: number;
  liveCopyIds?: string[]; // server copy_ids when running
  createdAt: number;
  updatedAt: number;
};
