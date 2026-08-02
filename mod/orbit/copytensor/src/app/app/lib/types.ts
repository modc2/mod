export type SubnetInfo = {
  netuid: number;
  name: string;
  alpha_price_tao: number;
  total_stake_tao: number;
  tempo: number;
  emission: number;

  // Enriched by the bt index. Null when reads fall back to raw RPC — render
  // an em-dash rather than a zero when these are missing.
  symbol?: string | null;
  market_cap_tao?: number | null;
  volume_tao?: number | null;
  vol_24h_tao?: number | null;
  change_1h?: number | null;
  change_24h?: number | null;
  change_7d?: number | null;
  spark?: number[] | null;
  alpha_in?: number | null;
  alpha_out?: number | null;
  owner?: string | null;
  registered_at?: number | null;
  logo?: string | null;
  description?: string | null;
  github?: string | null;
  url?: string | null;
  discord?: string | null;
};

export type MarketStats = {
  subnets: number;
  total_market_cap_tao: number;
  total_tao_in_pools: number;
  volume_24h_tao: number;
  block: number;
  tao_usd: number | null;
  updated_at: number | null;
  source: string;
  gainers: SubnetInfo[];
  losers: SubnetInfo[];
};

export type SubnetValidator = {
  uid: number;
  hotkey: string;
  coldkey: string;
  stake: number;
  validator_trust: number;
  dividends: number;
  incentive: number;
  emission: number;
  active: boolean;
  validator_permit: boolean;
};

export type SubnetDetail = {
  subnet: SubnetInfo;
  owner_hotkey: string | null;
  owner_coldkey: string | null;
  contact: string | null;
  neurons: number;
  blocks_since_last_step: number | null;
  pending_alpha_emission: number | null;
  alpha_out_emission: number | null;
  moving_price: number | null;
  validators: SubnetValidator[];
};

export type PricePoint = {
  t: number;
  price: number;
  mcap: number | null;
  volume: number | null;
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

// ── equity / PnL curve with trades pinned to it ──────────────────
export type CurvePoint = {
  ts: number;              // unix seconds
  block: number;
  value_tao: number;       // portfolio value at this snapshot
  pnl_tao: number;         // cumulative PnL vs the first point
  pnl_pct: number;
  market_tao: number;      // cumulative PnL from price moves alone
  flow_tao: number;        // cumulative net stake added/removed
  trades: number;          // legs inferred at this point
};

export type CurveTrade = {
  ts: number;
  block: number;
  netuid: number;
  subnet_name: string;
  side: "buy" | "sell";
  alpha: number;
  price_tao: number;
  tao_value: number;
  value_tao: number;       // where this trade sits on the value curve
  pnl_tao: number;         // …and on the PnL curve
};

export type CurveEvent = {
  ts: number;
  block: number;
  buy_tao: number;
  sell_tao: number;
  net_tao: number;
  legs: number;
  side: "buy" | "sell";
  value_tao: number;
  pnl_tao: number;
  top: { netuid: number; subnet_name: string; side: string; tao_value: number }[];
};

export type CurveData = {
  ss58: string;
  days: number;
  block_start?: number;
  block_end?: number;
  from_ts?: number;
  to_ts?: number;
  points: CurvePoint[];
  trades: CurveTrade[];
  events: CurveEvent[];
  warming?: boolean;
  totals: {
    start_value_tao: number;
    end_value_tao: number;
    pnl_tao: number;
    pnl_pct: number;
    market_pnl_tao: number;
    flow_tao: number;
    buy_tao: number;
    sell_tao: number;
    trades: number;
    events: number;
  };
  coverage: {
    snapshots: number;
    requested_days: number;
    actual_days: number;
    interval_sec: number | null;
    window_short: boolean;
    truncated: boolean;
  };
  note: string;
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
  baseline?: boolean;
  window_days: number;       // days of history this PnL actually covers
  // PnL split: price move on the book held vs stake deposited/withdrawn.
  market_pnl_tao?: number;
  market_pct?: number;
  flow_tao?: number;
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

// A row of bt's trader index: live value + windowed change, straight from
// its local snapshots. Everything windowed is null until enough snapshots
// have aged — render "warming", never a zero.
export type TrackedTrader = {
  ss58: string;
  label: string | null;
  free_tao: number;
  staked_tao: number;
  total_tao: number;
  subnets: number;
  top_subnets: { netuid: number; value_tao: number; alpha: number }[];
  snapshots: number;
  last_ts: number | null;
  change_24h: number | null;
  pnl_24h: number | null;
  change_7d: number | null;
  pnl_7d: number | null;
  spark: number[] | null;
};

export type TraderBoard = {
  count: number;
  updated_at: number | null;
  rows: TrackedTrader[];
};

// How many traders the leaderboard ranks, against how many the chain has.
export type Universe = {
  // Which horizons are priced, which are still building, which engine priced
  // each ("bt" = bt's index, "rpc" = the fallback chain walk), and how many
  // coldkeys bt indexes — a bt-priced board ranks that set, not the watchlist.
  board: {
    warm: number[];
    building: number[];
    rows: Record<string, number>;
    source?: Record<string, "bt" | "rpc">;
    indexed?: number | null;
  };
  watched: number;
  pool_size: number;
  auto_discover: boolean;
  known: number | null;             // coldkeys in the on-chain universe
  known_validators: number | null;
  status: "idle" | "discovering" | "error";
  target: number;
  added: number;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
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
