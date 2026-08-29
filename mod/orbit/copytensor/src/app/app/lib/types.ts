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
  /** The TAO behind this trader — lifted out of `config`. */
  alloc_tao: number;
  target_info?: {
    ss58: string;
    label: string | null;
    total_stake_tao: number;
    num_subnets: number;
    pnl_tao: number;
    pnl_pct: number;
  } | null;
};

// ── portfolio: every sleeve, one book ──
// Copies do not each own a portfolio. They blend into ONE desired book, and
// this is that book — what each trader asked for, and the trades that would
// close the gap.

export type Sleeve = {
  copy_id: string;
  target_ss58: string;
  label: string | null;
  alloc_tao: number;
  /** After scaling for what the wallet can actually back. */
  effective_tao: number;
  pct_of_book: number;
  subnets: number;
  stale: boolean;
  note: string | null;
};

export type PlanRow = {
  netuid: number;
  subnet_name: string;
  action: "stake" | "unstake" | "hold";
  desired_tao: number;
  current_tao: number;
  amount_tao: number;
  drift_tao: number;
  /** copy_id -> TAO this sleeve wants here. */
  contributors: Record<string, number>;
  reason: string;
};

export type PortfolioPlan = {
  our_ss58: string | null;
  staked_tao: number;
  free_tao: number;
  requested_tao: number;
  deployable_tao: number;
  /** <1 when the sleeves ask for more than the wallet can back. */
  scale: number;
  band_tao: number;
  threshold_pct: number;
  sleeves: Sleeve[];
  rows: PlanRow[];
  trades: number;
  /** Set when the pass must not trade at all; every row is held. */
  blocked: string | null;
  notes: string[];
  executed: boolean;
  results: {
    action: string;
    netuid: number;
    amount_tao: number;
    status: string;
    tx_hash?: string | null;
    error?: string | null;
  }[];
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
  /** Which sleeves paid for this move — one trade can serve several copies. */
  contributors?: Record<string, number> | null;
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
  /**
   * Absolute TAO behind this trader. When set it IS the allocation — the
   * live engine sizes the sleeve by it and the backtest weights by it — and
   * `weight` is kept in step for baskets still sized by share.
   */
  alloc_tao?: number | null;
};

export type SavedIndex = {
  id: string;
  name: string;
  traders: IndexTrader[];
  our_hotkey?: string;
  /** Per-trader TAO, rather than weight × capital. */
  sizing?: "split" | "tao";
  max_tao_per_tx?: number;
  daily_limit_tao?: number;
  rebalance_threshold_pct?: number;
  poll_interval_sec?: number;
  liveCopyIds?: string[]; // server copy_ids when running
  thesis?: string; // set when the strat agent wrote it
  createdAt: number;
  updatedAt: number;
};

// ── Strat agent ──────────────────────────────────────────────────

export type AgentStatus = {
  ready: boolean;
  method: string | null;
  hint: string | null;
  model: string;
  max_turns: number;
  timeout_sec: number;
  tools: string[];
  api: string;
};

/** One pick in a proposed basket, already priced off the tracked board. */
export type ProposedTrader = {
  ss58: string;
  label?: string | null;
  weight: number;
  /** The TAO behind this trader — always resolved, whichever way it was sized. */
  alloc_tao: number;
  share_pct: number;
  why?: string | null;
  tracked: boolean;
  total_tao?: number | null;
  change_7d?: number | null;
  pnl_7d?: number | null;
  subnets?: number | null;
};

/** The agent's deliverable — a basket, not yet saved and never live. */
export type StratProposal = {
  name: string;
  thesis: string;
  traders: ProposedTrader[];
  sizing?: "split" | "tao";
  capital_tao: number;
  max_tao_per_tx: number;
  rebalance_threshold_pct: number;
  poll_interval_sec: number;
  warning?: string;
};

/** Server-sent events from POST /agent/ask. */
export type AgentEvent =
  | { type: "start"; model: string; session_id: string; tools: number }
  | { type: "text"; text: string }
  | { type: "tool"; name: string; args: Record<string, unknown> }
  | { type: "tool_done"; name: string; error: boolean }
  | { type: "strat"; strat: StratProposal }
  | {
      type: "done";
      answer: string;
      session_id: string;
      turns: number;
      ms: number;
      cost_usd: number | null;
    }
  | { type: "error"; error: string };

// ── strats on the server (owned, shareable) ──────────────────────

export type StratVisibility = "private" | "public" | "whitelist";

/** A strat as the API returns it: the basket plus who may see it. */
export type ServerStrat = {
  id: string;
  name: string;
  visibility: StratVisibility;
  whitelist: string[];
  /** True when this browser's owner key created it. */
  mine: boolean;
  owner_fingerprint: string | null;
  traders: IndexTrader[];
  our_hotkey?: string | null;
  sizing?: "split" | "tao";
  max_tao_per_tx?: number | null;
  daily_limit_tao?: number | null;
  rebalance_threshold_pct?: number | null;
  poll_interval_sec?: number | null;
  thesis?: string | null;
  live_copy_ids?: string[];
  cloned_from?: string;
  created_at: number;
  updated_at: number;
};

/** What create/update send. Same shape, minus the server's own fields. */
export type StratWrite = {
  name: string;
  traders: IndexTrader[];
  visibility?: StratVisibility;
  whitelist?: string[];
  our_hotkey?: string | null;
  sizing?: "split" | "tao";
  max_tao_per_tx?: number | null;
  daily_limit_tao?: number | null;
  rebalance_threshold_pct?: number | null;
  poll_interval_sec?: number | null;
  thesis?: string | null;
  live_copy_ids?: string[];
};

/** Replay of a basket over a past window (POST /strats/backtest). */
export type Backtest = {
  ok: boolean;
  note: string | null;
  capital_tao: number;
  requested_hours: number;
  covered_hours: number;
  from_ts: number | null;
  to_ts: number | null;
  step_sec: number;
  points: number;
  /** Too few points for the stats to mean much — say so, don't hide it. */
  thin: boolean;
  curve: Array<{ t: number; equity_tao: number }>;
  stats: {
    total_return_pct?: number;
    end_tao?: number;
    pnl_tao?: number;
    max_drawdown_pct?: number;
    apy_pct?: number | null;
    sharpe?: number | null;
    best_step_pct?: number;
    worst_step_pct?: number;
  };
  per_trader: Array<{
    ss58: string;
    label?: string | null;
    weight: number;
    /** This leg's own return over the window. */
    return_pct: number;
    /** Its share of the basket's PnL. These sum to the basket's return. */
    contribution_tao: number;
    contribution_pct: number;
  }>;
  skipped: Array<{ ss58: string; reason: string }>;
  /** Set when the basket was too wide to replay whole — never silent. */
  truncated: { kept: number; dropped: number } | null;
  assumptions: string[];
};
