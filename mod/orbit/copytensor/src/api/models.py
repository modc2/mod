"""Pydantic models for API request/response validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ── requests ─────────────────────────────────────────────────────

class WatchRequest(BaseModel):
    ss58: str
    label: Optional[str] = None


class CopyRequest(BaseModel):
    target_ss58: str
    our_hotkey: str
    label: Optional[str] = None
    max_tao_per_tx: Optional[float] = None
    daily_limit_tao: Optional[float] = None
    min_balance_tao: Optional[float] = None
    subnet_allowlist: Optional[List[int]] = None
    subnet_denylist: Optional[List[int]] = None
    rebalance_threshold_pct: Optional[float] = None
    poll_interval_sec: Optional[int] = None


class WalletSetRequest(BaseModel):
    name: str = "copytensor"
    hotkey: str = "default"
    mnemonic: Optional[str] = None
    path: Optional[str] = None


class ConfigSetRequest(BaseModel):
    key: str
    value: Any


# ── responses ────────────────────────────────────────────────────

class SubnetResponse(BaseModel):
    netuid: int
    name: str
    alpha_price_tao: float
    total_stake_tao: float
    tempo: int
    emission: float

    # Everything below comes from the bt index's screener. It stays None when
    # reads fall back to raw RPC (bt down) — the UI hides those cells rather
    # than inventing zeros.
    symbol: Optional[str] = None
    market_cap_tao: Optional[float] = None
    volume_tao: Optional[float] = None
    vol_24h_tao: Optional[float] = None
    change_1h: Optional[float] = None
    change_24h: Optional[float] = None
    change_7d: Optional[float] = None
    spark: Optional[List[float]] = None
    alpha_in: Optional[float] = None
    alpha_out: Optional[float] = None
    owner: Optional[str] = None
    registered_at: Optional[int] = None
    logo: Optional[str] = None
    description: Optional[str] = None
    github: Optional[str] = None
    url: Optional[str] = None
    discord: Optional[str] = None


class MarketStatsResponse(BaseModel):
    """Network-wide totals for the market header strip."""
    subnets: int = 0
    total_market_cap_tao: float = 0
    total_tao_in_pools: float = 0
    volume_24h_tao: float = 0
    block: int = 0
    tao_usd: Optional[float] = None
    updated_at: Optional[int] = None
    source: str = "bt"
    gainers: List[SubnetResponse] = []
    losers: List[SubnetResponse] = []


class SubnetValidator(BaseModel):
    uid: int
    hotkey: str
    coldkey: str
    stake: float = 0
    validator_trust: float = 0
    dividends: float = 0
    incentive: float = 0
    emission: float = 0
    active: bool = True
    validator_permit: bool = False


class SubnetDetailResponse(BaseModel):
    subnet: SubnetResponse
    owner_hotkey: Optional[str] = None
    owner_coldkey: Optional[str] = None
    contact: Optional[str] = None
    neurons: int = 0
    blocks_since_last_step: Optional[int] = None
    pending_alpha_emission: Optional[float] = None
    alpha_out_emission: Optional[float] = None
    moving_price: Optional[float] = None
    validators: List[SubnetValidator] = []


class PricePoint(BaseModel):
    t: int
    price: float
    mcap: Optional[float] = None
    volume: Optional[float] = None


class AllocationResponse(BaseModel):
    netuid: int
    subnet_name: str = ""
    hotkey: str = ""
    alpha_amount: float
    alpha_price_tao: float
    value_tao: float
    pct_of_total: float


class AccountResponse(BaseModel):
    ss58: str
    total_stake_tao: float
    allocations: List[AllocationResponse]
    pnl_tao: float = 0
    pnl_pct: float = 0
    days: int = 7
    baseline: bool = True


class SubnetPnlResponse(BaseModel):
    netuid: int
    subnet_name: str
    alpha_start: float
    alpha_end: float
    price_start_tao: float
    price_end_tao: float
    value_start_tao: float
    value_end_tao: float
    pnl_tao: float
    pnl_pct: float


class PnlResponse(BaseModel):
    ss58: str
    days: int
    block_start: int
    block_end: int
    start_value_tao: float
    end_value_tao: float
    pnl_tao: float
    pnl_pct: float
    baseline: bool = True
    by_subnet: List[SubnetPnlResponse]


class LeaderboardEntryResponse(BaseModel):
    ss58: str
    label: Optional[str]
    total_stake_tao: float
    pnl_tao: float
    pnl_pct: float
    num_subnets: int
    top_subnet: Optional[int]
    top_subnet_pnl: float
    baseline: bool = True


class TargetTraderInfo(BaseModel):
    """Details about the trader being copied — makes it clear WHO you're following."""
    ss58: str
    label: Optional[str] = None
    total_stake_tao: float = 0
    num_subnets: int = 0
    pnl_tao: float = 0
    pnl_pct: float = 0
    pnl_days: int = 7
    top_allocations: List[AllocationResponse] = []


class CopyResponse(BaseModel):
    id: str
    target_ss58: str
    label: Optional[str]
    status: str
    config: Dict
    last_sync_block: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    target_info: Optional[TargetTraderInfo] = None


class TradeResponse(BaseModel):
    id: str
    copy_id: str
    block: Optional[int]
    timestamp: str
    action: str
    netuid: int
    amount_tao: float
    tx_hash: Optional[str]
    status: str
    error: Optional[str]
