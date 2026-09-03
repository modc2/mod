"""
FastAPI application — all REST endpoints for copytensor.
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import bittensor as bt
import requests
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..agent import agent as strat_agent
from ..agent import mcp_server
from ..chain.bt_source import BtSource, BtUnavailable, make_client
from ..chain.client import SubtensorClient, TraderCandidate, is_valid_ss58
from ..chain.snapshot import SnapshotManager
from ..db import Database
from ..engine.backtest import backtest_basket
from ..engine.bt_board import build_bt_leaderboard
from ..engine.copier import CopyConfig, CopyEngine
from ..engine.curve import FLOW_MIN_FRACTION, FLOW_MIN_TAO, build_curve
from ..engine.leaderboard import build_leaderboard
from ..engine.pnl import calculate_pnl
from ..engine.safety import SafetyManager
from .models import (
    AccountResponse,
    AllocationResponse,
    AskRequest,
    BacktestRequest,
    ConfigSetRequest,
    CopyRequest,
    CopyResponse,
    CopyUpdate,
    PlanRowResponse,
    PortfolioPlanResponse,
    SleeveResponse,
    LeaderboardEntryResponse,
    MarketStatsResponse,
    PnlResponse,
    PricePoint,
    SubnetDetailResponse,
    SubnetPnlResponse,
    SubnetResponse,
    StratWrite,
    SubnetValidator,
    TargetTraderInfo,
    TradeResponse,
    WalletSetRequest,
    WatchRequest,
)

log = logging.getLogger("copytensor.api")

# uvicorn only configures its own loggers, so without this every
# copytensor.* INFO line (pool growth, board build times, snapshot passes —
# the things you actually watch during a long warm) is dropped on the floor.
logging.basicConfig(
    level=os.environ.get("COPYTENSOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ── globals (initialized at startup) ────────────────────────────

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

_config: Dict = {}
_client: Optional[SubtensorClient] = None
_db: Optional[Database] = None
_snapshot_mgr: Optional[SnapshotManager] = None
_copy_engine: Optional[CopyEngine] = None
_safety: Optional[SafetyManager] = None
_wallet: Optional[bt.Wallet] = None
# The bt module's index — the source behind every read when it's up.
_bt: Optional[BtSource] = None


def _load_config() -> Dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_config():
    save = {k: v for k, v in _config.items() if k not in ("private_key", "mnemonic")}
    with open(CONFIG_PATH, "w") as f:
        json.dump(save, f, indent=2)


def _mirror_watchlist():
    """Register watched coldkeys with bt's trader index.

    bt is the index of record — the leaderboard ranks what bt tracks — so
    this is what decides how much of the watchlist is actually visible. It
    is also what bt pays for: one chain read per account per refresh pass
    (~1.75s each, measured), so the mirror is capped at `bt_mirror_max` and
    the rest of the pool still resolves through bt on demand. Copy targets
    go first; those are the accounts we size real trades off.

    Only accounts bt does not already have are pushed: `bt_track` snapshots
    on every call, so re-mirroring the same list each restart would spend
    minutes of chain reads to learn nothing.
    """
    if not _bt or not _bt.available() or not _db:
        return
    limit = int(_config.get("bt_mirror_max", 25))
    try:
        known = {r["ss58"] for r in (_bt.traders() or {}).get("rows") or []}
    except BtUnavailable as e:
        log.warning("bt watchlist unavailable (%s) — mirror skipped", e)
        return

    targets = {c["target_ss58"] for c in _db.list_copies()}
    accounts = _db.list_accounts()
    ordered = ([a for a in accounts if a["ss58"] in targets] +
               [a for a in accounts if a["ss58"] not in targets])[:limit]
    missing = [a for a in ordered if a["ss58"] not in known]
    if not missing:
        log.info("bt already indexes all %d mirrored accounts", len(ordered))
        return

    added = 0
    for acct in missing:
        try:
            _bt.track(acct["ss58"], acct.get("label"))
            added += 1
        except BtUnavailable as e:
            log.warning("bt track failed for %s: %s — mirror stopped at %d",
                        acct["ss58"][:8], e, added)
            break
    log.info("watchlist mirrored into bt (+%d, %d of %d accounts indexed)",
             added, len(ordered), len(accounts))


# ── trader pool ──────────────────────────────────────────────────
#
# The leaderboard ranks the accounts we watch, so "how many traders can I
# see" is exactly "how big is the watchlist". The pool is grown from the
# on-chain delegate set (owners + their nominators) — real coldkeys only.

DEFAULT_POOL_SIZE = 250

_pool_state: Dict[str, Any] = {
    "status": "idle",        # idle | discovering | error
    "target": 0,
    "added": 0,
    "known": None,           # size of the on-chain universe, once walked
    "known_validators": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_pool_lock = threading.Lock()


def _pool_size() -> int:
    return int(_config.get("leaderboard_pool_size", DEFAULT_POOL_SIZE))


def _pool_busy() -> bool:
    with _pool_lock:
        return _pool_state["status"] == "discovering"


def _claim_pool(target: int) -> bool:
    """Mark discovery as running. False if another pass already holds it.

    Callers claim before returning to the client, so a POST /pool response
    already reads "discovering" and the UI never sees a false idle in the
    gap before the worker thread starts.
    """
    with _pool_lock:
        if _pool_state["status"] == "discovering":
            return False
        _pool_state.update(status="discovering", target=target, added=0,
                           started_at=time.time(), finished_at=None, error=None)
        return True


def _grow_pool(size: Optional[int] = None, force: bool = False,
               claimed: bool = False) -> Dict[str, Any]:
    """Fill the watchlist up to `size` real coldkeys ranked by stake.

    Runs on a worker thread: the delegate walk is ~30s and the first
    leaderboard pass over a fresh pool is minutes, so nothing here may sit
    on a request. Safe to call repeatedly — only missing accounts are added.
    """
    target = int(size or _pool_size())
    if not claimed and not _claim_pool(target):
        return dict(_pool_state)
    try:
        kinds = tuple(_config.get("pool_kinds", ["validator", "nominator"]))
        candidates = _client.discover_traders(
            n=target, kinds=kinds,
            min_stake_weight=float(_config.get("pool_min_stake_weight", 0.0)),
            force=force)
        sizes = _client.universe_size()
        added = 0
        validators = 0
        for c in candidates:
            if c.kind == "validator":
                validators += 1
            if not is_valid_ss58(c.ss58) or _db.has_account(c.ss58):
                continue
            _db.add_account(c.ss58, label=_pool_label(c, validators))
            added += 1
        with _pool_lock:
            _pool_state.update(status="idle", added=added,
                               known=sizes["total"],
                               known_validators=sizes["validators"],
                               finished_at=time.time())
        log.info("trader pool: +%d accounts (watching %d of %d on-chain)",
                 added, len(_db.list_accounts()), sizes["total"])
    except Exception as e:
        log.error("pool growth failed: %s", e)
        with _pool_lock:
            _pool_state.update(status="error", error=str(e),
                               finished_at=time.time())
    return dict(_pool_state)


def _boot_pool(size: Optional[int] = None, force: bool = False,
               grow: bool = True, claimed: bool = False):
    """Warm every leaderboard horizon and top the trader pool up.

    A bt-priced board ranks bt's index rather than the watchlist, so it owes
    the delegate walk nothing: warm it first and the UI has rows a second
    after boot instead of after the ~35s walk. Without bt the board *is* the
    watchlist, so the walk has to land first or the warm is thrown away.
    """
    bt_first = bool(_bt and _bt.available())
    if bt_first:
        _warm_lb()
    if grow:
        try:
            _grow_pool(size, force=force, claimed=claimed)
        except Exception as e:
            log.warning("pool growth failed: %s", e)
    if not bt_first:
        _warm_lb()


def _pool_label(c: TraderCandidate, validator_rank: int) -> Optional[str]:
    """Validators carry their stake rank among validators; nominators stay
    unlabeled so the UI shows the address rather than inventing a name."""
    if c.kind == "validator":
        return f"Validator #{validator_rank}"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _client, _db, _snapshot_mgr, _copy_engine, _safety, _bt

    _config = _load_config()
    network = _config.get("network", "finney")

    # Reads (subnets, positions, history) are served by the bt module's
    # local index; it falls back to our own RPC pool whenever bt is down.
    # Writes always go out over our wallet + RPC pool.
    _client = make_client(_config)
    _bt = getattr(_client, "bt", None)
    _db = Database()
    _safety = SafetyManager(_config)
    _copy_engine = CopyEngine(_client, _db, _safety)

    snapshot_interval = _config.get("snapshot_interval_sec", 1800)
    _snapshot_mgr = SnapshotManager(
        _client, _db, interval_sec=snapshot_interval,
        workers=int(_config.get("snapshot_workers", 8)),
        hold=_pool_busy)

    # Seed watchlist: user-watched + seeded validators (well-known coldkeys).
    # The seed pool lets the leaderboard render on first boot without anyone
    # adding accounts manually. Users can `unwatch` to clean them up.
    seeded = list(_config.get("watched_accounts", [])) + \
             list(_config.get("seed_validators", []))
    for entry in seeded:
        ss58 = entry.get("ss58") if isinstance(entry, dict) else entry
        label = entry.get("label") if isinstance(entry, dict) else None
        if not ss58 or not is_valid_ss58(ss58):
            log.warning("seed skipped, invalid ss58: %s", ss58)
            continue
        _db.add_account(ss58, label=label)

    # Purge any previously-stored accounts that fail checksum validation —
    # they poison the leaderboard with permanent errors.
    for acct in _db.list_accounts():
        if not is_valid_ss58(acct["ss58"]):
            log.warning("purging invalid watched account %s", acct["ss58"])
            _db.remove_account(acct["ss58"])

    # PnL needs history: start the periodic snapshot loop (first pass runs
    # immediately, off the event loop).
    _snapshot_mgr.start()

    # Grow the watchlist to the configured pool size from the on-chain
    # delegate set, then warm every leaderboard horizon the UI offers so no
    # first click eats the cold multi-account chain walk. Sequential on one
    # worker: warming a pool that is about to triple wastes the work.
    threading.Thread(
        target=_boot_pool,
        kwargs={"grow": bool(_config.get("auto_discover", True))},
        daemon=True).start()

    # Mirror the watchlist into bt so its indexer keeps their history —
    # each track reads the chain once, so do it off the event loop.
    threading.Thread(target=_mirror_watchlist, daemon=True).start()

    # Bring the copy loop back up. Before this, active copies survived a
    # restart in the DB but nothing was polling them: the console showed a
    # live basket that hadn't rebalanced since the last boot. The loop is a
    # no-op until a wallet is set, and reads its membership from the DB every
    # tick, so it is safe to start unconditionally.
    _copy_engine.start_portfolio()
    active = _db.list_copies(status="active")
    if active:
        log.info("portfolio loop resumed: %d sleeve(s), %.4fτ allocated",
                 len(active),
                 sum(float((c.get("config") or {}).get("alloc_tao") or 0)
                     for c in active))

    log.info("copytensor API started (network=%s, watched=%d, reads=%s)",
             network, len(_db.list_accounts()),
             "bt" if (_bt and _bt.available()) else "rpc")
    yield
    if _copy_engine:
        _copy_engine.stop_portfolio()
    if _snapshot_mgr:
        _snapshot_mgr.stop()
    log.info("copytensor API stopped")


app = FastAPI(title="copytensor", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── health ───────────────────────────────────────────────────────

@app.get("/health")
def health():
    return _client.health()


TAO_PRICE_TTL_SEC = 300
_tao_price: Dict[str, Any] = {"ts": 0.0, "usd": None}


@app.get("/tao_price")
def tao_price():
    """Live TAO/USD price for the UI's currency toggle, server-side cached."""
    age = time.time() - _tao_price["ts"]
    if _tao_price["usd"] is not None and age < TAO_PRICE_TTL_SEC:
        return {"usd": _tao_price["usd"], "age_sec": int(age), "stale": False}
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bittensor&vs_currencies=usd",
            timeout=8,
        )
        usd = float(r.json()["bittensor"]["usd"])
        _tao_price.update(ts=time.time(), usd=usd)
        return {"usd": usd, "age_sec": 0, "stale": False}
    except Exception as e:
        if _tao_price["usd"] is not None:
            return {"usd": _tao_price["usd"], "age_sec": int(age), "stale": True}
        return {"error": f"price unavailable: {e}"}


@app.get("/status")
def status():
    copies = _db.list_copies() if _db else []
    active = [c for c in copies if c["status"] == "active"]
    accounts = _db.list_accounts() if _db else []
    h = _client.health() if _client else {}
    bt_info = _bt.info() if _bt else None
    return {
        "running": h.get("connected", False),
        "network": _config.get("network", "finney"),
        "block_height": h.get("block", 0),
        "tracked_accounts": len(accounts),
        "active_copies": len(active),
        "allocated_tao": round(sum(
            float((c.get("config") or {}).get("alloc_tao") or 0) for c in active), 6),
        "copy_loop": bool(_copy_engine and _copy_engine.status()["running"]),
        "wallet_set": _wallet is not None,
        "reads": "bt" if bt_info else "rpc",
        "bt": {"url": _bt.url, "available": bt_info is not None,
               "traders": (bt_info or {}).get("traders")} if _bt else None,
    }


# ── subnets ──────────────────────────────────────────────────────

# The screener is a single indexed read on bt's side, but the UI polls it
# from the ticker, the grid and the market strip at once. One short-TTL
# cache in front keeps that to one call per window.
_SUBNETS_TTL = 12
_subnets_cache: Dict[str, Any] = {"ts": 0.0, "rows": []}


def _spark(values: Optional[List], keep: int = 48) -> Optional[List[float]]:
    """Trim + round a price history down to something cheap to ship."""
    if not values:
        return None
    pts = [float(v) for v in values if v is not None]
    if len(pts) > keep:
        step = len(pts) / keep
        pts = [pts[min(len(pts) - 1, int(i * step))] for i in range(keep)]
    return [float(f"{p:.8g}") for p in pts]


def _screener_row(r: Dict) -> SubnetResponse:
    """One bt screener row → the enriched shape the UI renders."""
    return SubnetResponse(
        netuid=r["netuid"],
        name=r.get("name") or f"SN{r['netuid']}",
        alpha_price_tao=r.get("price") or 0.0,
        total_stake_tao=r.get("tao_in") or 0.0,
        tempo=r.get("tempo") or 0,
        emission=r.get("emission") or 0.0,
        symbol=r.get("symbol"),
        market_cap_tao=r.get("market_cap"),
        volume_tao=r.get("volume"),
        vol_24h_tao=r.get("vol_24h"),
        change_1h=r.get("change_1h"),
        change_24h=r.get("change_24h"),
        change_7d=r.get("change_7d"),
        spark=_spark(r.get("spark")),
        alpha_in=r.get("alpha_in"),
        alpha_out=r.get("alpha_out"),
        owner=r.get("owner"),
        registered_at=r.get("registered_at"),
        logo=r.get("logo"),
        description=r.get("description"),
        github=r.get("github"),
        url=r.get("url"),
        discord=r.get("discord"),
    )


def _subnet_rows(force: bool = False) -> List[SubnetResponse]:
    """Every subnet, enriched from bt's index when it's up.

    Falls back to the plain RPC walk (price / stake / tempo only) so the
    grid still renders — the enriched fields simply stay null and the UI
    hides those cells instead of showing invented zeros.
    """
    now = time.time()
    cached = _subnets_cache.get("rows") or []
    if cached and not force and now - _subnets_cache["ts"] < _SUBNETS_TTL:
        return cached

    rows: List[SubnetResponse] = []
    if _bt:
        try:
            rows = [_screener_row(r) for r in _bt.screener(sparks=True)]
        except BtUnavailable as e:
            log.warning("bt screener unavailable (%s) — falling back to RPC", e)
    if not rows:
        rows = [
            SubnetResponse(
                netuid=s.netuid, name=s.name,
                alpha_price_tao=s.alpha_price_tao,
                total_stake_tao=s.total_stake_tao,
                tempo=s.tempo, emission=s.emission,
            )
            for s in _client.get_all_subnet_info()
        ]
    _subnets_cache.update(ts=now, rows=rows)
    return rows


@app.get("/subnets", response_model=List[SubnetResponse])
def list_subnets():
    return _subnet_rows()


@app.get("/market", response_model=MarketStatsResponse)
def market_stats(movers: int = Query(5, ge=0, le=20)):
    """Network-wide totals + the day's biggest movers — the header strip."""
    rows = _subnet_rows()
    stats: Dict = {}
    source = "rpc"
    if _bt:
        try:
            stats = _bt.stats() or {}
            source = "bt"
        except BtUnavailable as e:
            log.warning("bt stats unavailable (%s)", e)
    if not stats:
        # Derive what we can from whatever the screener/RPC gave us.
        stats = {
            "subnets": len(rows),
            "total_market_cap_tao": sum(r.market_cap_tao or 0 for r in rows),
            "total_tao_in_pools": sum(r.total_stake_tao for r in rows),
            "volume_24h_tao": sum(r.vol_24h_tao or 0 for r in rows),
        }

    # Root (netuid 0) is pegged at 1 τ and never moves — leaving it in the
    # movers list would just waste a slot.
    ranked = sorted(
        (r for r in rows if r.netuid != 0 and r.change_24h is not None),
        key=lambda r: r.change_24h or 0,
    )
    price = tao_price()

    return MarketStatsResponse(
        subnets=int(stats.get("subnets") or len(rows)),
        total_market_cap_tao=float(stats.get("total_market_cap_tao") or 0),
        total_tao_in_pools=float(stats.get("total_tao_in_pools") or 0),
        volume_24h_tao=float(stats.get("volume_24h_tao") or 0),
        block=(_client.get_block() if _client else 0),
        tao_usd=price.get("usd") if isinstance(price, dict) else None,
        updated_at=stats.get("updated_at"),
        source=source,
        gainers=list(reversed(ranked[-movers:])) if movers else [],
        losers=ranked[:movers] if movers else [],
    )


@app.get("/subnets/{netuid}", response_model=SubnetDetailResponse)
def subnet_detail(netuid: int, validators: int = Query(15, ge=0, le=64)):
    """One subnet: pool state, on-chain identity, validator rankings."""
    row = next((r for r in _subnet_rows() if r.netuid == netuid), None)
    if row is None:
        raise HTTPException(404, f"subnet {netuid} not found")

    detail = SubnetDetailResponse(subnet=row)
    if not _bt:
        return detail

    try:
        s = _bt.subnet(netuid) or {}
        ident = s.get("subnet_identity") or {}
        detail.owner_hotkey = s.get("owner_hotkey")
        detail.owner_coldkey = s.get("owner_coldkey")
        detail.contact = ident.get("subnet_contact")
        detail.blocks_since_last_step = s.get("blocks_since_last_step")
        detail.pending_alpha_emission = s.get("pending_alpha_emission")
        detail.alpha_out_emission = s.get("alpha_out_emission")
        detail.moving_price = s.get("moving_price")
    except BtUnavailable as e:
        log.warning("bt subnet %d unavailable (%s)", netuid, e)

    if validators:
        try:
            v = _bt.validators(netuid) or {}
            detail.neurons = int(v.get("neurons") or 0)
            detail.validators = [
                SubnetValidator(
                    uid=x["uid"], hotkey=x.get("hotkey", ""),
                    coldkey=x.get("coldkey", ""),
                    stake=x.get("stake") or 0,
                    validator_trust=x.get("validator_trust") or 0,
                    dividends=x.get("dividends") or 0,
                    incentive=x.get("incentive") or 0,
                    emission=x.get("emission") or 0,
                    active=bool(x.get("active", True)),
                    validator_permit=bool(x.get("validator_permit", False)),
                )
                for x in (v.get("top") or [])[:validators]
            ]
        except BtUnavailable as e:
            log.warning("bt validators %d unavailable (%s)", netuid, e)

    return detail


@app.get("/subnets/{netuid}/history", response_model=List[PricePoint])
def subnet_history(netuid: int, hours: int = Query(168, ge=1, le=8760)):
    """Indexed price / mcap / volume series — the detail-page chart."""
    if not _bt:
        return []
    try:
        out = _bt.history(netuid, hours=hours) or {}
    except BtUnavailable as e:
        log.warning("bt history %d unavailable (%s)", netuid, e)
        return []
    return [
        PricePoint(t=int(p["t"]), price=float(p.get("price") or 0),
                   mcap=p.get("mcap"), volume=p.get("volume"))
        for p in (out.get("series") or []) if p.get("t")
    ]


# ── accounts ─────────────────────────────────────────────────────

@app.get("/account/{ss58}", response_model=AccountResponse)
def get_account(ss58: str, days: int = Query(7, ge=0, le=365)):
    days = _win(days)
    positions = _client.get_stake_for_coldkey(ss58)

    # Get subnet names
    subnet_names = {}
    try:
        for s in _client.get_all_subnet_info():
            subnet_names[s.netuid] = s.name
    except Exception:
        pass

    total = max(positions.total_value_tao, 0.001)
    allocations = [
        AllocationResponse(
            netuid=p.netuid,
            subnet_name=subnet_names.get(p.netuid, f"SN{p.netuid}"),
            hotkey=p.hotkey,
            alpha_amount=p.alpha_amount,
            alpha_price_tao=p.alpha_price_tao,
            value_tao=p.value_tao,
            pct_of_total=p.value_tao / total * 100,
        )
        for p in positions.positions
    ]

    # Calculate PnL
    pnl_tao = 0.0
    pnl_pct = 0.0
    baseline = False
    try:
        pnl = calculate_pnl(_client, _db, ss58, days)
        pnl_tao = pnl.pnl_tao
        pnl_pct = pnl.pnl_pct
        baseline = pnl.baseline
    except Exception as e:
        log.warning("pnl calc failed for %s: %s", ss58[:8], e)

    return AccountResponse(
        ss58=ss58,
        total_stake_tao=positions.total_value_tao,
        allocations=allocations,
        pnl_tao=pnl_tao,
        pnl_pct=pnl_pct,
        days=days,
        baseline=baseline,
    )


@app.get("/account/{ss58}/pnl", response_model=PnlResponse)
def get_account_pnl(ss58: str, days: int = Query(7, ge=0, le=365)):
    days = _win(days)
    pnl = calculate_pnl(_client, _db, ss58, days)
    return PnlResponse(
        ss58=pnl.ss58,
        days=pnl.days,
        block_start=pnl.block_start,
        block_end=pnl.block_end,
        start_value_tao=pnl.start_value_tao,
        end_value_tao=pnl.end_value_tao,
        pnl_tao=pnl.pnl_tao,
        pnl_pct=pnl.pnl_pct,
        baseline=pnl.baseline,
        by_subnet=[
            SubnetPnlResponse(
                netuid=s.netuid, subnet_name=s.subnet_name,
                alpha_start=s.alpha_start, alpha_end=s.alpha_end,
                price_start_tao=s.price_start_tao, price_end_tao=s.price_end_tao,
                value_start_tao=s.value_start_tao, value_end_tao=s.value_end_tao,
                pnl_tao=s.pnl_tao, pnl_pct=s.pnl_pct,
            )
            for s in pnl.by_subnet
        ],
    )


@app.get("/account/{ss58}/history")
def get_account_history(ss58: str, limit: int = Query(50, ge=1, le=500)):
    snapshots = _db.get_snapshots(ss58, limit=limit)
    return {"snapshots": snapshots}


@app.get("/account/{ss58}/curve")
def get_account_curve(ss58: str, days: int = Query(7, ge=0, le=365),
                      min_tao: float = Query(FLOW_MIN_TAO, ge=0.0),
                      min_frac: float = Query(FLOW_MIN_FRACTION, ge=0.0, le=1.0),
                      points: int = Query(400, ge=20, le=2000)):
    """Portfolio value / cumulative PnL over time, with trades on the curve.

    Built entirely from the local snapshot record — every point is a real
    block, and each trade carries the curve value at its timestamp so the
    chart can pin the marker exactly on the line.
    """
    days = _win(days)
    if not is_valid_ss58(ss58):
        raise HTTPException(400, f"invalid ss58 address: {ss58}")

    subnet_names: Dict[int, str] = {}
    try:
        for s in _client.get_all_subnet_info():
            subnet_names[s.netuid] = s.name
    except Exception as e:
        log.warning("subnet names unavailable for curve: %s", e)

    try:
        current_block = _client.get_block()
    except Exception:
        current_block = 0

    return build_curve(_db, ss58, days, current_block,
                       subnet_names=subnet_names, min_tao=min_tao,
                       min_frac=min_frac, max_points=points)


# ── trader details ───────────────────────────────────────────────

@app.get("/trader/{ss58}")
def get_trader_details(ss58: str, days: int = Query(7, ge=0, le=365)):
    """Full trader profile — allocations, PnL breakdown, performance."""
    days = _win(days)
    positions = _client.get_stake_for_coldkey(ss58)

    subnet_names = {}
    try:
        for s in _client.get_all_subnet_info():
            subnet_names[s.netuid] = s.name
    except Exception:
        pass

    total = max(positions.total_value_tao, 0.001)
    allocations = sorted(
        [
            {
                "netuid": p.netuid,
                "subnet_name": subnet_names.get(p.netuid, f"SN{p.netuid}"),
                "hotkey": p.hotkey,
                "alpha_amount": round(p.alpha_amount, 4),
                "alpha_price_tao": round(p.alpha_price_tao, 6),
                "value_tao": round(p.value_tao, 4),
                "pct_of_total": round(p.value_tao / total * 100, 2),
            }
            for p in positions.positions
        ],
        key=lambda a: a["value_tao"],
        reverse=True,
    )

    # PnL
    pnl_data = {}
    try:
        pnl = calculate_pnl(_client, _db, ss58, days)
        pnl_data = {
            "pnl_tao": round(pnl.pnl_tao, 4),
            "pnl_pct": round(pnl.pnl_pct, 2),
            "baseline": pnl.baseline,
            "start_value_tao": round(pnl.start_value_tao, 4),
            "end_value_tao": round(pnl.end_value_tao, 4),
            "block_start": pnl.block_start,
            "block_end": pnl.block_end,
            "by_subnet": [
                {
                    "netuid": s.netuid,
                    "subnet_name": s.subnet_name,
                    "pnl_tao": round(s.pnl_tao, 4),
                    "pnl_pct": round(s.pnl_pct, 2),
                    "value_start_tao": round(s.value_start_tao, 4),
                    "value_end_tao": round(s.value_end_tao, 4),
                }
                for s in pnl.by_subnet
            ],
        }
    except Exception as e:
        pnl_data = {"error": str(e)}

    # Label from watchlist
    label = None
    for a in (_db.list_accounts() if _db else []):
        if a["ss58"] == ss58:
            label = a.get("label")
            break

    return {
        "ss58": ss58,
        "label": label,
        "total_stake_tao": round(positions.total_value_tao, 4),
        "num_subnets": len(allocations),
        "days": days,
        "pnl": pnl_data,
        "allocations": allocations,
    }


# ── leaderboard ──────────────────────────────────────────────────

# A full leaderboard build walks every watched account against the chain
# (historical + live queries) — tens of seconds cold. Cache the FULL board
# per horizon (`days`) and slice/filter per request, so top=50 and top=100
# hit the same entry; serve stale + refresh in the background so the UI
# never waits once a horizon is warm.
LEADERBOARD_TTL_SEC = 120
# `days=0` on /leaderboard means "every day of history there is". It maps to
# one fixed horizon rather than to the measured depth so the cache key stays
# put — the depth grows by a day every day, and keying on it would rebuild
# the board from scratch each time it ticked over.
ALL_DAYS = 365
# Warm order, not just a set: the UI opens on 7d, so price that first and
# let the rest fill in behind it. ALL_DAYS comes last and doubles as the
# measurement /coverage reads its depth off.
LEADERBOARD_HORIZONS = [7, 1, 3, 14, 30, ALL_DAYS]
# The horizons the console offers as buttons. /coverage reports, for each,
# how many indexed traders actually have that much history behind them.
WINDOW_CHOICES = [1, 3, 7, 14, 30]


def _win(days: int) -> int:
    """`days=0` on any windowed read means "all the history there is".

    The console keeps one horizon across every page, so the all-history
    window has to be spelled the same way to /account and /leaderboard
    alike — otherwise picking ALL on the board 422s the trader page.
    """
    return ALL_DAYS if not days else days
_lb_cache: Dict[int, tuple] = {}             # days -> (ts, entries)
_lb_refreshing: set = set()
_lb_build_sec: Dict[int, float] = {}         # days -> last build duration
_lb_source: Dict[int, str] = {}              # days -> "bt" | "rpc"
# Reentrant: the staleness check reads build timings while already holding it.
_lb_lock = threading.RLock()
# Serializes the builds themselves (they all queue on the same archive pool).
_lb_build_gate = threading.Lock()


def _bt_indexed() -> Optional[int]:
    """How many coldkeys bt keeps history for — the size of a bt-priced board."""
    if not _bt:
        return None
    info = _bt.info() or {}
    tracked = (info.get("traders") or {}).get("tracked")
    return int(tracked) if tracked is not None else None


def _lb_ttl(days: int) -> float:
    """Never spend more time rebuilding a horizon than living off it.

    A pool of hundreds is a minutes-long walk over public RPCs, so a flat
    2-minute TTL would mean rebuilding forever. Refresh at most once per
    three build-times.
    """
    with _lb_lock:
        last = _lb_build_sec.get(days, 0.0)
    return max(LEADERBOARD_TTL_SEC, 3 * last)


def _build_lb(days: int, allow_rpc: bool = True):
    # Mark here, not only at the request that triggered it: the startup warm
    # calls straight in, and /universe must report those builds too or the
    # UI shows an empty board with nothing apparently happening.
    with _lb_lock:
        _lb_refreshing.add(days)
    started = time.time()
    workers = int(_config.get("leaderboard_workers", 8))
    source = "bt"
    try:
        # bt's index prices the whole board from local SQLite in about a
        # second and shares nothing with the archive pool, so it runs outside
        # the gate — queueing it behind a chain walk is why a board stayed
        # empty for minutes after bt came back up mid-walk.
        try:
            if not _bt:
                raise BtUnavailable("no bt source configured")
            entries = build_bt_leaderboard(_bt, days=days, top=2000)
        except BtUnavailable as e:
            if not allow_rpc:
                raise
            log.warning("leaderboard %dd from bt failed (%s) — walking the "
                        "chain instead", days, e)
            source = "rpc"
            # One horizon at a time down here. Every walk is bottlenecked on
            # the same handful of archive sockets, so running two
            # concurrently doesn't finish either sooner — it delays both.
            with _lb_build_gate:
                entries = build_leaderboard(_client, _db, days=days, top=2000,
                                            workers=workers)
    except Exception:
        with _lb_lock:
            _lb_refreshing.discard(days)
        raise
    took = time.time() - started
    with _lb_lock:
        _lb_cache[days] = (time.time(), entries)
        _lb_build_sec[days] = took
        _lb_refreshing.discard(days)
        _lb_source[days] = source
    log.info("leaderboard %dd: %d traders priced in %.1fs (from %s)",
             days, len(entries), took, source)
    return entries


def _warm_lb():
    for d in LEADERBOARD_HORIZONS:
        try:
            _build_lb(d)
        except Exception as e:
            log.warning("leaderboard warm failed for %dd: %s", d, e)
            with _lb_lock:
                _lb_refreshing.discard(d)


def _leaderboard_cached(days: int):
    """Whatever we have for this horizon, immediately.

    A cold horizon out of bt's index is a second of local reads, so it is
    priced on the request thread — the first person to open the page gets
    rows, not an empty board. Only the archive walk (bt down, hundreds of
    accounts, minutes) is pushed onto a background thread; then the caller
    gets [] plus `building` in /universe and the rows land on the next poll.
    """
    # A bt-priced board is local SQLite over HTTP, so it neither waits for
    # nor slows the delegate walk. Only the chain walk has to stand aside.
    bt_ready = bool(_bt and _bt.available())
    with _lb_lock:
        cached = _lb_cache.get(days)
        stale = cached and time.time() - cached[0] >= _lb_ttl(days)
        kick = (cached is None or stale) and days not in _lb_refreshing
        # A chain walk started while bt was down holds its horizon for
        # minutes, and until it lands the board is empty. Once bt answers
        # again there is nothing to wait for — price the cold horizon off the
        # index now and let the walk overwrite it whenever it finishes.
        if cached is None and bt_ready:
            kick = True
        # Not while the pool is being discovered. The delegate walk decodes
        # tens of thousands of entries in-process; racing a board build
        # against it just makes both slow.
        if _pool_busy() and not bt_ready:
            kick = False
        if kick:
            _lb_refreshing.add(days)
    if kick:
        if cached is None and bt_ready:
            try:
                return _build_lb(days, allow_rpc=False)
            except Exception as e:
                # bt went away between the ping and the build — leave the
                # horizon cold and let the next poll start the chain walk.
                log.warning("cold leaderboard %dd from bt failed: %s", days, e)
                with _lb_lock:
                    _lb_refreshing.discard(days)
                return []
        threading.Thread(target=_build_lb, args=(days,), daemon=True).start()
    return cached[1] if cached else []


@app.get("/leaderboard", response_model=List[LeaderboardEntryResponse])
def leaderboard(days: int = Query(7, ge=0, le=365),
                top: int = Query(50, ge=1, le=2000),
                min_subnets: int = Query(0, ge=0)):
    # days=0 = "as far back as the index goes". Every row then reports its
    # own window_days, which is the honest answer when traders were indexed
    # on different days.
    days = _win(days)
    entries = [e for e in _leaderboard_cached(days)
               if e.num_subnets >= min_subnets][:top]
    return [
        LeaderboardEntryResponse(
            ss58=e.ss58, label=e.label,
            total_stake_tao=e.total_stake_tao,
            pnl_tao=e.pnl_tao, pnl_pct=e.pnl_pct,
            num_subnets=e.num_subnets,
            top_subnet=e.top_subnet,
            top_subnet_pnl=e.top_subnet_pnl,
            baseline=e.baseline,
            window_days=e.window_days,
            market_pnl_tao=e.market_pnl_tao,
            market_pct=e.market_pct,
            flow_tao=e.flow_tao,
        )
        for e in entries
    ]


# ── how far back the numbers go ───────────────────────────────────

# Coverage is derived from one deep board, so it costs a board build at
# most once every few minutes. It only moves when bt indexes a new trader
# or another day accumulates.
COVERAGE_TTL_SEC = 300
_coverage_cache: Optional[tuple] = None      # (ts, payload)
_coverage_lock = threading.Lock()


def _coverage() -> Dict:
    """What history actually exists behind the horizons the UI offers.

    Every row of the deepest board carries `window_days` — the span bt has
    really indexed for that coldkey — so one board answers both questions:
    how far back we can go at all, and how many traders survive each
    horizon. Without this the console happily offered a 30-day window over
    an index that was 12 days old and nothing said so; the rows just came
    back quietly measured over less.
    """
    global _coverage_cache
    with _coverage_lock:
        if _coverage_cache and time.time() - _coverage_cache[0] < COVERAGE_TTL_SEC:
            return _coverage_cache[1]

    rows = _leaderboard_cached(ALL_DAYS)
    spans = sorted(e.window_days for e in rows
                   if e.baseline is not False and (e.window_days or 0) > 0)
    depth = spans[-1] if spans else 0.0
    median = spans[len(spans) // 2] if spans else 0.0

    def bucket(w: int) -> Dict:
        # 90% of the horizon counts as covering it: snapshots land on an
        # interval, so a "30 day" trader is indexed at 29.5 and demanding
        # the full span would call every row short.
        full = [s for s in spans if s >= w * 0.9]
        return {
            "days": w,
            "covered": len(full),
            "pct": round(100.0 * len(full) / len(spans), 1) if spans else 0.0,
            # Offer it while anyone can answer it; the count says how thin.
            "ok": bool(full),
        }

    payload = {
        # Whole days, rounded down — claiming 37 when the deepest row is
        # 36.8 is the kind of rounding that makes a window look covered.
        "depth_days": int(depth),
        "depth_days_exact": round(depth, 2),
        "median_days": round(median, 2),
        "oldest_ts": int(time.time() - depth * 86400) if depth else None,
        "traders": len(rows),
        "priced": len(spans),
        "indexed": _bt_indexed(),
        "windows": [bucket(w) for w in WINDOW_CHOICES],
        "updated_at": int(time.time()),
    }
    with _coverage_lock:
        _coverage_cache = (time.time(), payload)
    return payload


@app.get("/coverage")
def coverage():
    """How deep the trader index is, and which horizons it can honestly fill."""
    return _coverage()


@app.post("/discover")
def discover(top: int = Query(8, ge=1, le=200),
             kind: str = Query("validator", pattern="^(validator|nominator|all)$")):
    """Watch the top-N coldkeys of the on-chain universe by stake.

    Heavy (~30s cold): walks the full delegate set. Every account it adds is
    real and checksum-valid — no invented seed lists. Synchronous, and it
    snapshots what it adds; for a large pool use POST /pool instead.
    """
    kinds = ("validator", "nominator") if kind == "all" else (kind,)
    found = _client.discover_traders(n=top, kinds=kinds)
    added = []
    validators = 0
    for c in found:
        if c.kind == "validator":
            validators += 1
        if not _db.has_account(c.ss58):
            label = _pool_label(c, validators)
            _db.add_account(c.ss58, label=label)
            added.append({**c.as_dict(), "label": label})
        if _snapshot_mgr:
            _snapshot_mgr.take_snapshot(c.ss58)
    return {"discovered": [c.as_dict() for c in found], "added": added,
            "total_watched": len(_db.list_accounts())}


@app.get("/universe")
def universe():
    """How many traders we rank, and how many exist on-chain to rank."""
    watched = len(_db.list_accounts()) if _db else 0
    with _pool_lock:
        state = dict(_pool_state)
    with _lb_lock:
        board = {
            "warm": sorted(_lb_cache.keys()),
            "building": sorted(_lb_refreshing),
            "rows": {str(d): len(v[1]) for d, v in _lb_cache.items()},
            # Which engine priced each horizon: bt's index, or the archive
            # walk it fell back to. "watched" counts the whole watchlist;
            # a bt-priced board only ranks the slice bt indexes.
            "source": dict(_lb_source),
            "indexed": _bt_indexed(),
        }
    return {
        "board": board,
        "watched": watched,
        "pool_size": _pool_size(),
        "auto_discover": bool(_config.get("auto_discover", True)),
        "known": state.get("known"),
        "known_validators": state.get("known_validators"),
        "status": state.get("status"),
        "target": state.get("target"),
        "added": state.get("added"),
        "error": state.get("error"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
    }


@app.post("/pool")
def set_pool(size: int = Query(..., ge=1, le=2000),
             refresh: bool = Query(False)):
    """Resize the trader pool: watch the top `size` coldkeys by stake.

    Returns immediately — discovery and the leaderboard rebuild run in the
    background; poll GET /universe for progress. Shrinking is not automatic:
    accounts already watched stay watched (unwatch removes them).
    """
    _config["leaderboard_pool_size"] = int(size)
    _save_config()
    # Claim before answering so the response already says "discovering".
    queued = _claim_pool(int(size))
    if queued:
        threading.Thread(
            target=_boot_pool,
            kwargs={"size": int(size), "force": refresh, "claimed": True},
            daemon=True).start()
    return {**universe(), "queued": queued}


# ── watchlist ────────────────────────────────────────────────────

@app.post("/watch")
def watch(req: WatchRequest):
    if not is_valid_ss58(req.ss58):
        raise HTTPException(400, f"invalid ss58 address: {req.ss58}")
    _db.add_account(req.ss58, req.label)
    # Also persist to config
    watched = _config.setdefault("watched_accounts", [])
    if req.ss58 not in watched:
        watched.append(req.ss58)
        _save_config()
    # Hand it to bt's indexer (which snapshots it now), then keep our own
    # copy of the snapshot for the local PnL baseline.
    indexed = None
    if _bt:
        try:
            indexed = _bt.track(req.ss58, req.label)
        except BtUnavailable as e:
            log.warning("bt track failed for %s: %s", req.ss58[:8], e)
    if _snapshot_mgr:
        _snapshot_mgr.take_snapshot(req.ss58)
    total = len(_db.list_accounts())
    return {"watched": req.ss58, "total": total,
            "bt_indexed": bool(indexed and indexed.get("tracked"))}


@app.delete("/watch/{ss58}")
def unwatch(ss58: str):
    _db.remove_account(ss58)
    if _bt:
        try:
            _bt.untrack(ss58)   # bt keeps the recorded history
        except BtUnavailable as e:
            log.warning("bt untrack failed for %s: %s", ss58[:8], e)
    watched = _config.get("watched_accounts", [])
    if ss58 in watched:
        watched.remove(ss58)
        _save_config()
    total = len(_db.list_accounts())
    return {"unwatched": ss58, "total": total}


@app.get("/watches")
def list_watches():
    return {"accounts": _db.list_accounts()}


# ── tracked traders (served by bt's index) ───────────────────────

def _require_bt() -> BtSource:
    if not _bt or not _bt.available():
        raise HTTPException(
            503, "trader tracking needs the bt module — start it "
                 f"({_bt.url if _bt else 'http://localhost:50280'}) or set "
                 "COPYTENSOR_BT_URL")
    return _bt


@app.get("/traders")
def list_traders(sort_by: str = Query("total_tao")):
    """Every tracked trader with live value, allocation and windowed PnL.

    Instant: bt answers from its local index, no chain walk.
    """
    try:
        return _require_bt().traders(sort_by=sort_by)
    except BtUnavailable as e:
        raise HTTPException(502, str(e))


@app.get("/traders/{ss58}")
def trader_profile(ss58: str, hours: int = Query(168, ge=1, le=24 * 365)):
    """Full indexed profile — positions, equity curve, inferred trades."""
    try:
        return _require_bt().trader(ss58, hours=hours)
    except BtUnavailable as e:
        raise HTTPException(502, str(e))


@app.get("/traders/{ss58}/history")
def trader_history(ss58: str, hours: int = Query(168, ge=1, le=24 * 365)):
    """Portfolio value over time (free / staked / total)."""
    try:
        return _require_bt().trader_history(ss58, hours=hours)
    except BtUnavailable as e:
        raise HTTPException(502, str(e))


@app.get("/traders/{ss58}/flows")
def trader_flows(ss58: str, hours: int = Query(168, ge=1, le=24 * 365),
                 limit: int = Query(100, ge=1, le=1000)):
    """Buys/sells inferred from how this trader's book changed."""
    try:
        return _require_bt().trader_flows(ss58, hours=hours, limit=limit)
    except BtUnavailable as e:
        raise HTTPException(502, str(e))


@app.get("/flows")
def all_flows(hours: int = Query(168, ge=1, le=24 * 365),
              limit: int = Query(100, ge=1, le=1000)):
    """The tape across every tracked trader."""
    try:
        return _require_bt().trader_flows(None, hours=hours, limit=limit)
    except BtUnavailable as e:
        raise HTTPException(502, str(e))


# ── helpers ──────────────────────────────────────────────────────

def _get_target_info(target_ss58: str, days: int = 7) -> Optional[TargetTraderInfo]:
    """Fetch live trader details for the copy target account."""
    try:
        positions = _client.get_stake_for_coldkey(target_ss58)
        subnet_names = {}
        try:
            for s in _client.get_all_subnet_info():
                subnet_names[s.netuid] = s.name
        except Exception:
            pass

        total = max(positions.total_value_tao, 0.001)
        allocations = sorted(
            [
                AllocationResponse(
                    netuid=p.netuid,
                    subnet_name=subnet_names.get(p.netuid, f"SN{p.netuid}"),
                    hotkey=p.hotkey,
                    alpha_amount=p.alpha_amount,
                    alpha_price_tao=p.alpha_price_tao,
                    value_tao=p.value_tao,
                    pct_of_total=p.value_tao / total * 100,
                )
                for p in positions.positions
            ],
            key=lambda a: a.value_tao,
            reverse=True,
        )

        pnl_tao = 0.0
        pnl_pct = 0.0
        try:
            pnl = calculate_pnl(_client, _db, target_ss58, days)
            pnl_tao = pnl.pnl_tao
            pnl_pct = pnl.pnl_pct
        except Exception:
            pass

        # Get label from watchlist if available
        label = None
        acct_list = _db.list_accounts() if _db else []
        for a in acct_list:
            if a["ss58"] == target_ss58:
                label = a.get("label")
                break

        return TargetTraderInfo(
            ss58=target_ss58,
            label=label,
            total_stake_tao=positions.total_value_tao,
            num_subnets=len(allocations),
            pnl_tao=pnl_tao,
            pnl_pct=pnl_pct,
            pnl_days=days,
            top_allocations=allocations[:10],
        )
    except Exception as e:
        log.warning("failed to fetch target info for %s: %s", target_ss58[:8], e)
        return None


def _enrich_copy(copy: Dict) -> CopyResponse:
    """Build CopyResponse with embedded target trader details."""
    target_info = _get_target_info(copy["target_ss58"])
    return CopyResponse(
        **copy,
        target_info=target_info,
        alloc_tao=float((copy.get("config") or {}).get("alloc_tao") or 0.0),
    )


# ── copy trading ─────────────────────────────────────────────────

@app.post("/copy", response_model=CopyResponse)
def create_copy(req: CopyRequest):
    if not _wallet:
        raise HTTPException(400, "wallet not set — POST /wallet/set first")
    if not is_valid_ss58(req.target_ss58):
        raise HTTPException(400, f"not a valid ss58 address: {req.target_ss58}")

    # `alloc_tao` is the whole point of a copy: it's the money behind this
    # trader. Old clients sent only a daily spend cap, which the engine never
    # used for sizing — fall back to it so they keep working, and so the
    # number they meant as "spend" at least becomes the sleeve they intended.
    alloc = req.alloc_tao
    if alloc is None:
        alloc = req.daily_limit_tao
    if alloc is None or alloc <= 0:
        raise HTTPException(
            400, "alloc_tao required — how much TAO should follow this trader?")

    copy_config = {
        "our_hotkey": req.our_hotkey,
        "alloc_tao": float(alloc),
        "max_tao_per_tx": req.max_tao_per_tx or _config.get("max_tao_per_tx", 10),
        "daily_limit_tao": req.daily_limit_tao or _config.get("daily_limit_tao", 100),
        "min_balance_tao": req.min_balance_tao or _config.get("min_balance_tao", 1),
        "subnet_allowlist": req.subnet_allowlist or _config.get("subnet_allowlist"),
        "subnet_denylist": req.subnet_denylist or _config.get("subnet_denylist", []),
        "rebalance_threshold_pct": req.rebalance_threshold_pct or _config.get("rebalance_threshold_pct", 5),
        "poll_interval_sec": req.poll_interval_sec or _config.get("poll_interval_sec", 300),
    }

    copy_id = _db.insert_copy(
        target_ss58=req.target_ss58,
        config=copy_config,
        label=req.label,
    )

    # One loop covers every sleeve — it reads the active set from the DB on
    # each tick, so this just makes sure it's up.
    _copy_engine.start_portfolio()

    copy = _db.get_copy(copy_id)
    return _enrich_copy(copy)


@app.put("/copy/{copy_id}", response_model=CopyResponse)
def update_copy(copy_id: str, req: CopyUpdate):
    """Re-size a live copy. Changing `alloc_tao` re-weights the blended book
    on the next pass — no stop/start, no re-entering the position."""
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")

    cfg = dict(copy["config"])
    fields = req.model_dump(exclude_none=True) if hasattr(req, "model_dump") \
        else req.dict(exclude_none=True)
    label = fields.pop("label", None)
    if "alloc_tao" in fields and fields["alloc_tao"] < 0:
        raise HTTPException(400, "alloc_tao cannot be negative")
    cfg.update(fields)
    _db.update_copy_config(copy_id, cfg)
    if label is not None:
        _db.update_copy(copy_id, label=label)
    return _enrich_copy(_db.get_copy(copy_id))


@app.get("/copies", response_model=List[CopyResponse])
def list_copies():
    copies = _db.list_copies()
    return [_enrich_copy(c) for c in copies]


@app.get("/copy/{copy_id}", response_model=CopyResponse)
def get_copy(copy_id: str):
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")
    return _enrich_copy(copy)


@app.post("/copy/{copy_id}/pause")
def pause_copy(copy_id: str):
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")
    _copy_engine.stop_copy(copy_id)
    _db.update_copy(copy_id, status="paused")
    return {"id": copy_id, "status": "paused"}


@app.post("/copy/{copy_id}/resume")
def resume_copy(copy_id: str):
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")
    _db.update_copy(copy_id, status="active")
    _copy_engine.start_portfolio()
    return {"id": copy_id, "status": "active"}


@app.delete("/copy/{copy_id}")
def delete_copy(copy_id: str):
    _copy_engine.stop_copy(copy_id)
    _db.delete_copy(copy_id)
    return {"deleted": True, "id": copy_id}


@app.post("/copy/{copy_id}/sync")
def sync_copy(copy_id: str):
    """Apply now. Sleeves only add up when they're diffed against the book
    together, so this runs the whole portfolio — syncing one copy alone would
    drag every other trader's money with it."""
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")
    if not _wallet:
        raise HTTPException(400, "wallet not set")

    trades = _copy_engine.sync_portfolio()
    return {
        "synced": True,
        "scope": "portfolio",
        "trades": [
            {"action": t.action, "netuid": t.netuid,
             "amount_tao": t.amount_tao, "status": t.status,
             "error": t.error}
            for t in trades
        ],
    }


# ── portfolio: every sleeve, one book ────────────────────────────

def _plan_response(plan, executed: bool = False, results=None) -> PortfolioPlanResponse:
    """Shape a Plan for the wire, naming subnets so the rows read as trades
    rather than as netuids."""
    names: Dict[int, str] = {}
    try:
        for sn in _client.get_all_subnet_info():
            names[sn.netuid] = sn.name
    except Exception:
        pass

    total_effective = sum(s.alloc_tao * plan.scale for s in plan.sleeves if s.live)
    sleeves = []
    for s in plan.sleeves:
        eff = s.alloc_tao * plan.scale if s.live else 0.0
        sleeves.append(SleeveResponse(
            copy_id=s.copy_id, target_ss58=s.target_ss58, label=s.label,
            alloc_tao=round(s.alloc_tao, 6), effective_tao=round(eff, 6),
            pct_of_book=round(eff / total_effective * 100, 4) if total_effective else 0.0,
            subnets=len(s.shares), stale=s.stale, note=s.error,
        ))

    book = _copy_engine.our_book()
    return PortfolioPlanResponse(
        our_ss58=book.get("ss58"),
        staked_tao=round(float(book.get("staked_tao") or 0), 6),
        free_tao=round(float(book.get("free_tao") or 0), 6),
        requested_tao=plan.requested_tao,
        deployable_tao=plan.deployable_tao,
        scale=plan.scale,
        band_tao=plan.band_tao,
        sleeves=sleeves,
        rows=[PlanRowResponse(
            netuid=r.netuid, subnet_name=names.get(r.netuid, f"SN{r.netuid}"),
            action=r.action, desired_tao=round(r.desired_tao, 6),
            current_tao=round(r.current_tao, 6), amount_tao=round(r.amount_tao, 6),
            drift_tao=round(r.drift_tao, 6), contributors=r.contributors,
            reason=r.reason,
        ) for r in plan.rows],
        trades=len(plan.trades),
        blocked=plan.blocked,
        notes=plan.notes,
        executed=executed,
        results=results or [],
    )


@app.get("/portfolio", response_model=PortfolioPlanResponse)
def portfolio_plan():
    """The blended book: every sleeve, what it asks for, and the trades that
    would close the gap. Pure read — nothing is sent to the chain."""
    return _plan_response(_copy_engine.plan_portfolio())


@app.post("/portfolio/sync", response_model=PortfolioPlanResponse)
def portfolio_sync(dry_run: bool = Query(False)):
    """Run a portfolio pass now. `dry_run=true` is identical to GET /portfolio
    — the same plan object is what gets executed, so the preview cannot drift
    from the thing it previews."""
    if dry_run:
        return _plan_response(_copy_engine.plan_portfolio())
    if not _wallet:
        raise HTTPException(400, "wallet not set — POST /wallet/set first")
    results = _copy_engine.sync_portfolio()
    plan = _copy_engine._last_plan or _copy_engine.plan_portfolio()
    return _plan_response(plan, executed=True, results=[
        {"action": t.action, "netuid": t.netuid, "amount_tao": t.amount_tao,
         "status": t.status, "tx_hash": t.tx_hash, "error": t.error}
        for t in results
    ])


@app.get("/portfolio/status")
def portfolio_status():
    """Is the loop up, how many sleeves, how much τ allocated."""
    st = _copy_engine.status()
    st["limits"] = _safety.get_limits()
    return st


# ── trades ───────────────────────────────────────────────────────

@app.get("/trades", response_model=List[TradeResponse])
def list_trades(copy_id: Optional[str] = None,
                limit: int = Query(50, ge=1, le=500)):
    trades = _db.get_trades(copy_id=copy_id, limit=limit)
    return [TradeResponse(**t) for t in trades]


# ── strats: backtest, store, sharing ─────────────────────────────
#
# A strat is a basket of traders you'd mirror. Two things it needs that a
# localStorage list can't give it: an honest replay of what the basket would
# have done, and an owner — so several strats can coexist, stay private by
# default, and be published or handed to named people on purpose.
#
# Identity is an owner KEY the browser generates and keeps (X-Owner-Key).
# The server only ever stores its SHA-256, and the first 16 hex of that hash
# is your public FINGERPRINT — the thing you hand someone so they can put
# you on a strat's whitelist. No account, no password, no wallet needed;
# lose the key and you lose write access to your strats, which is the honest
# trade for having no sign-up.

VISIBILITIES = ("private", "public", "whitelist")


def _owner_hash(key: Optional[str]) -> Optional[str]:
    if not key or not key.strip():
        return None
    return hashlib.sha256(key.strip().encode()).hexdigest()


def _fingerprint(owner_hash: Optional[str]) -> Optional[str]:
    return owner_hash[:16] if owner_hash else None


def _strat_out(s: Dict, owner_hash: Optional[str]) -> Dict:
    """Public shape of a strat row: the owner's hash never leaves, only
    whether it's yours and the fingerprint others would whitelist."""
    out = {k: v for k, v in s.items() if k != "owner_hash"}
    out["mine"] = bool(owner_hash) and s.get("owner_hash") == owner_hash
    out["owner_fingerprint"] = _fingerprint(s.get("owner_hash"))
    return out


def _require_strat(strat_id: str, owner_hash: Optional[str],
                   write: bool = False) -> Dict:
    s = _db.get_strat(strat_id)
    if not s:
        raise HTTPException(404, "no such strat")
    mine = bool(owner_hash) and s["owner_hash"] == owner_hash
    if write and not mine:
        raise HTTPException(403, "only the owner of this strat can change it")
    listed = bool(owner_hash) and _fingerprint(owner_hash) in (s.get("whitelist") or [])
    if not (mine or listed or s["visibility"] == "public"):
        raise HTTPException(404, "no such strat")
    return s


def _strat_payload(req: StratWrite) -> Dict:
    return {
        "traders": [t.model_dump() for t in req.traders],
        "our_hotkey": req.our_hotkey,
        "sizing": req.sizing,
        "max_tao_per_tx": req.max_tao_per_tx,
        "daily_limit_tao": req.daily_limit_tao,
        "rebalance_threshold_pct": req.rebalance_threshold_pct,
        "poll_interval_sec": req.poll_interval_sec,
        "thesis": req.thesis,
        "live_copy_ids": req.live_copy_ids or [],
    }


@app.get("/whoami")
def whoami(x_owner_key: Optional[str] = Header(None)):
    """Your fingerprint — the id someone else puts on a whitelist to let you
    in. Anonymous callers get null and see public strats only."""
    oh = _owner_hash(x_owner_key)
    return {"fingerprint": _fingerprint(oh), "anonymous": oh is None}


@app.post("/strats/backtest")
def backtest_strat(req: BacktestRequest):
    """Replay a basket over the last `days`. Takes the basket inline so the
    picker can re-run it on every edit, saved or not."""
    hours = max(1, min(int(req.days), 365)) * 24
    rows = [t.model_dump() for t in req.traders]
    # If the basket is sized in τ, the capital IS the sum of the sleeves —
    # replaying 40τ+10τ against a leftover 100τ default would misreport every
    # number in money terms.
    sleeved = sum(float(t.get("alloc_tao") or 0) for t in rows
                  if t.get("enabled") is not False)
    capital = sleeved if sleeved > 0 else req.capital_tao
    return backtest_basket(
        rows,
        hours=hours,
        fetch_history=lambda ss58, h: _require_bt().trader_history(ss58, hours=h),
        capital_tao=capital,
    )


@app.get("/strats")
def list_strats(x_owner_key: Optional[str] = Header(None)):
    """Yours, plus every public one, plus anything whitelisting you."""
    oh = _owner_hash(x_owner_key)
    rows = _db.list_strats(owner_hash=oh, fingerprint=_fingerprint(oh))
    return {
        "fingerprint": _fingerprint(oh),
        "strats": [_strat_out(s, oh) for s in rows],
    }


@app.get("/strats/hub")
def hub_strats(x_owner_key: Optional[str] = Header(None)):
    """The public shelf — what anyone can read and clone."""
    oh = _owner_hash(x_owner_key)
    return {"strats": [_strat_out(s, oh) for s in _db.list_public_strats()]}


@app.post("/strats")
def create_strat(req: StratWrite, x_owner_key: Optional[str] = Header(None)):
    oh = _owner_hash(x_owner_key)
    if not oh:
        raise HTTPException(401, "send an X-Owner-Key header — it's the only "
                                 "thing that says this strat is yours")
    vis = req.visibility or "private"
    if vis not in VISIBILITIES:
        raise HTTPException(400, f"visibility must be one of {VISIBILITIES}")
    s = _db.create_strat(oh, req.name.strip() or "Untitled", _strat_payload(req),
                         visibility=vis, whitelist=req.whitelist or [])
    return _strat_out(s, oh)


@app.get("/strats/{strat_id}")
def get_strat(strat_id: str, x_owner_key: Optional[str] = Header(None)):
    oh = _owner_hash(x_owner_key)
    return _strat_out(_require_strat(strat_id, oh), oh)


@app.put("/strats/{strat_id}")
def update_strat(strat_id: str, req: StratWrite,
                 x_owner_key: Optional[str] = Header(None)):
    oh = _owner_hash(x_owner_key)
    _require_strat(strat_id, oh, write=True)
    if req.visibility and req.visibility not in VISIBILITIES:
        raise HTTPException(400, f"visibility must be one of {VISIBILITIES}")
    s = _db.update_strat(
        strat_id,
        name=req.name.strip() or "Untitled",
        payload=_strat_payload(req),
        visibility=req.visibility,
        whitelist=req.whitelist,
    )
    return _strat_out(s, oh)


@app.delete("/strats/{strat_id}")
def delete_strat(strat_id: str, x_owner_key: Optional[str] = Header(None)):
    oh = _owner_hash(x_owner_key)
    _require_strat(strat_id, oh, write=True)
    _db.delete_strat(strat_id)
    return {"deleted": strat_id}


@app.post("/strats/{strat_id}/clone")
def clone_strat(strat_id: str, x_owner_key: Optional[str] = Header(None)):
    """Copy someone's public strat onto your own shelf, private."""
    oh = _owner_hash(x_owner_key)
    if not oh:
        raise HTTPException(401, "send an X-Owner-Key header to own the copy")
    src = _require_strat(strat_id, oh)
    payload = {k: v for k, v in src.items()
               if k not in ("id", "owner_hash", "name", "visibility",
                            "whitelist", "created_at", "updated_at")}
    payload["live_copy_ids"] = []          # a clone starts flat, never live
    payload["cloned_from"] = strat_id
    s = _db.create_strat(oh, f"{src['name']} (copy)", payload)
    return _strat_out(s, oh)


# ── strat agent ──────────────────────────────────────────────────

@app.get("/agent")
def agent_status():
    """Whether the strat agent can run, and what it can reach."""
    return strat_agent.status()


@app.post("/agent/ask")
def agent_ask(req: AskRequest):
    """Talk to the strat agent. Streams the run as SSE.

    Events: start | text | tool | tool_done | strat | done | error. `strat`
    carries a validated basket — the console renders it as a card. Nothing
    the agent does can stake; activating is still a human click.
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(400, "question required")

    def gen():
        for ev in strat_agent.ask(question, req.session_id):
            yield "data: " + json.dumps(ev, default=str) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache",
                                      "x-accel-buffering": "no"})


# ── MCP over HTTP ────────────────────────────────────────────────
#
# The same dispatcher the stdio server runs, mounted on the API port so any
# MCP client (Claude Code, the fleet's mcp hub, a DAG) connects with one URL:
#
#     claude mcp add --transport http copytensor http://localhost:50150/mcp
#
# Every tool is a loopback call to this API's own REST routes — that is why
# the dispatch runs in the threadpool: a blocking requests.get to ourselves
# from the event loop would wait on the loop it is blocking.

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": "parse error"}})
    replies = await run_in_threadpool(mcp_server.handle_batch, body)
    if replies is None:
        return Response(status_code=202)
    return replies


@app.get("/mcp")
def mcp_get():
    return JSONResponse(status_code=405, content={
        "error": "POST JSON-RPC here (MCP streamable HTTP); SSE stream not "
                 "offered. GET /mcp/schema lists the tools."})


@app.get("/mcp/schema")
def mcp_schema():
    """Transports, scope and the full tool list — read this before connecting."""
    out = mcp_server.schema()
    port = int(os.environ.get("COPYTENSOR_API_PORT", _config.get("port", 50150)))
    out["connect"] = {
        "http": f"http://localhost:{port}/mcp",
        "gateway": "/api/copytensor/mcp on the mod gateway (:3001 / caddy :3000)",
        "claude": f"claude mcp add --transport http copytensor http://localhost:{port}/mcp",
        "stdio": {"command": "python3", "args": ["-m", "src.agent.mcp_server"],
                  "cwd": os.path.dirname(os.path.dirname(os.path.dirname(
                      os.path.abspath(__file__))))},
    }
    return out


# ── wallet ───────────────────────────────────────────────────────

@app.post("/wallet/set")
def set_wallet(req: WalletSetRequest):
    global _wallet
    try:
        if req.mnemonic:
            w = bt.Wallet(name=req.name, hotkey=req.hotkey)
            w.regenerate_coldkey(mnemonic=req.mnemonic, use_password=False,
                                overwrite=False, suppress=True)
            _wallet = w
        elif req.path:
            _wallet = bt.Wallet(name=req.name, hotkey=req.hotkey, path=req.path)
        else:
            _wallet = bt.Wallet(name=req.name, hotkey=req.hotkey)

        _copy_engine.set_wallet(_wallet)
        ss58 = _wallet.coldkey.ss58_address
        return {"wallet_set": True, "ss58": ss58}
    except Exception as e:
        raise HTTPException(400, f"wallet setup failed: {e}")


@app.get("/wallet/balance")
def wallet_balance():
    if not _wallet:
        raise HTTPException(400, "wallet not set")
    ss58 = _wallet.coldkey.ss58_address
    balance = _client.get_balance(ss58)
    return {"ss58": ss58, "balance_tao": balance}


# ── config ───────────────────────────────────────────────────────

@app.get("/config")
def get_config():
    safe = {k: v for k, v in _config.items()
            if k not in ("private_key", "mnemonic")}
    return safe


@app.post("/config")
def set_config(req: ConfigSetRequest):
    if req.key in ("private_key", "mnemonic"):
        raise HTTPException(400, "cannot set secrets via config endpoint")
    _config[req.key] = req.value
    _save_config()
    return {"set": req.key, "value": req.value}


# ── snapshots ────────────────────────────────────────────────────

@app.post("/snapshots/start")
def start_snapshots():
    if _snapshot_mgr:
        _snapshot_mgr.start()
        return {"started": True}
    raise HTTPException(500, "snapshot manager not initialized")


@app.post("/snapshots/stop")
def stop_snapshots():
    if _snapshot_mgr:
        _snapshot_mgr.stop()
        return {"stopped": True}
    raise HTTPException(500, "snapshot manager not initialized")


@app.post("/snapshots/now")
def snapshot_now():
    if _snapshot_mgr:
        results = _snapshot_mgr.snapshot_all()
        return {"snapshots": len(results), "results": results}
    raise HTTPException(500, "snapshot manager not initialized")
