"""
FastAPI application — all REST endpoints for copytensor.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import bittensor as bt
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ..chain.client import SubtensorClient
from ..chain.snapshot import SnapshotManager
from ..db import Database
from ..engine.copier import CopyConfig, CopyEngine
from ..engine.leaderboard import build_leaderboard
from ..engine.pnl import calculate_pnl
from ..engine.safety import SafetyManager
from .models import (
    AccountResponse,
    AllocationResponse,
    ConfigSetRequest,
    CopyRequest,
    CopyResponse,
    LeaderboardEntryResponse,
    PnlResponse,
    SubnetPnlResponse,
    SubnetResponse,
    TargetTraderInfo,
    TradeResponse,
    WalletSetRequest,
    WatchRequest,
)

log = logging.getLogger("copytensor.api")

# ── globals (initialized at startup) ────────────────────────────

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

_config: Dict = {}
_client: Optional[SubtensorClient] = None
_db: Optional[Database] = None
_snapshot_mgr: Optional[SnapshotManager] = None
_copy_engine: Optional[CopyEngine] = None
_safety: Optional[SafetyManager] = None
_wallet: Optional[bt.wallet] = None


def _load_config() -> Dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_config():
    save = {k: v for k, v in _config.items() if k not in ("private_key", "mnemonic")}
    with open(CONFIG_PATH, "w") as f:
        json.dump(save, f, indent=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _client, _db, _snapshot_mgr, _copy_engine, _safety

    _config = _load_config()
    network = _config.get("network", "finney")
    endpoint = _config.get("subtensor_endpoint")

    _client = SubtensorClient(network=network, endpoint=endpoint)
    _db = Database()
    _safety = SafetyManager(_config)
    _copy_engine = CopyEngine(_client, _db, _safety)

    snapshot_interval = _config.get("snapshot_interval_sec", 1800)
    _snapshot_mgr = SnapshotManager(_client, _db, interval_sec=snapshot_interval)

    # Seed watchlist: user-watched + seeded validators (well-known coldkeys).
    # The seed pool lets the leaderboard render on first boot without anyone
    # adding accounts manually. Users can `unwatch` to clean them up.
    seeded = list(_config.get("watched_accounts", [])) + \
             list(_config.get("seed_validators", []))
    for entry in seeded:
        if isinstance(entry, dict):
            _db.add_account(entry.get("ss58"), label=entry.get("label"))
        elif isinstance(entry, str):
            _db.add_account(entry)

    log.info("copytensor API started (network=%s, watched=%d)",
             network, len(seeded))
    yield
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


@app.get("/status")
def status():
    copies = _db.list_copies() if _db else []
    active = [c for c in copies if c["status"] == "active"]
    accounts = _db.list_accounts() if _db else []
    h = _client.health() if _client else {}
    return {
        "running": h.get("connected", False),
        "network": _config.get("network", "finney"),
        "block_height": h.get("block", 0),
        "tracked_accounts": len(accounts),
        "active_copies": len(active),
        "wallet_set": _wallet is not None,
    }


# ── subnets ──────────────────────────────────────────────────────

@app.get("/subnets", response_model=List[SubnetResponse])
def list_subnets():
    infos = _client.get_all_subnet_info()
    return [
        SubnetResponse(
            netuid=s.netuid, name=s.name,
            alpha_price_tao=s.alpha_price_tao,
            total_stake_tao=s.total_stake_tao,
            tempo=s.tempo, emission=s.emission,
        )
        for s in infos
    ]


# ── accounts ─────────────────────────────────────────────────────

@app.get("/account/{ss58}", response_model=AccountResponse)
def get_account(ss58: str, days: int = Query(7, ge=1, le=365)):
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
    try:
        pnl = calculate_pnl(_client, _db, ss58, days)
        pnl_tao = pnl.pnl_tao
        pnl_pct = pnl.pnl_pct
    except Exception as e:
        log.warning("pnl calc failed for %s: %s", ss58[:8], e)

    return AccountResponse(
        ss58=ss58,
        total_stake_tao=positions.total_value_tao,
        allocations=allocations,
        pnl_tao=pnl_tao,
        pnl_pct=pnl_pct,
        days=days,
    )


@app.get("/account/{ss58}/pnl", response_model=PnlResponse)
def get_account_pnl(ss58: str, days: int = Query(7, ge=1, le=365)):
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


# ── trader details ───────────────────────────────────────────────

@app.get("/trader/{ss58}")
def get_trader_details(ss58: str, days: int = Query(7, ge=1, le=365)):
    """Full trader profile — allocations, PnL breakdown, performance."""
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

@app.get("/leaderboard", response_model=List[LeaderboardEntryResponse])
def leaderboard(days: int = Query(7, ge=1, le=365),
                top: int = Query(50, ge=1, le=500),
                min_subnets: int = Query(0, ge=0)):
    entries = build_leaderboard(_client, _db, days=days, top=top,
                                min_subnets=min_subnets)
    return [
        LeaderboardEntryResponse(
            ss58=e.ss58, label=e.label,
            total_stake_tao=e.total_stake_tao,
            pnl_tao=e.pnl_tao, pnl_pct=e.pnl_pct,
            num_subnets=e.num_subnets,
            top_subnet=e.top_subnet,
            top_subnet_pnl=e.top_subnet_pnl,
        )
        for e in entries
    ]


# ── watchlist ────────────────────────────────────────────────────

@app.post("/watch")
def watch(req: WatchRequest):
    _db.add_account(req.ss58, req.label)
    # Also persist to config
    watched = _config.setdefault("watched_accounts", [])
    if req.ss58 not in watched:
        watched.append(req.ss58)
        _save_config()
    # Take initial snapshot
    if _snapshot_mgr:
        _snapshot_mgr.take_snapshot(req.ss58)
    total = len(_db.list_accounts())
    return {"watched": req.ss58, "total": total}


@app.delete("/watch/{ss58}")
def unwatch(ss58: str):
    _db.remove_account(ss58)
    watched = _config.get("watched_accounts", [])
    if ss58 in watched:
        watched.remove(ss58)
        _save_config()
    total = len(_db.list_accounts())
    return {"unwatched": ss58, "total": total}


@app.get("/watches")
def list_watches():
    return {"accounts": _db.list_accounts()}


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
    return CopyResponse(**copy, target_info=target_info)


# ── copy trading ─────────────────────────────────────────────────

@app.post("/copy", response_model=CopyResponse)
def create_copy(req: CopyRequest):
    if not _wallet:
        raise HTTPException(400, "wallet not set — POST /wallet/set first")

    copy_config = {
        "our_hotkey": req.our_hotkey,
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

    # Start the copy loop
    cc = CopyConfig(
        id=copy_id,
        target_ss58=req.target_ss58,
        our_hotkey=req.our_hotkey,
        rebalance_threshold_pct=copy_config["rebalance_threshold_pct"],
        poll_interval_sec=copy_config["poll_interval_sec"],
    )
    _copy_engine.start_copy(cc)

    copy = _db.get_copy(copy_id)
    return _enrich_copy(copy)


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
    cc = CopyConfig(
        id=copy_id,
        target_ss58=copy["target_ss58"],
        our_hotkey=copy["config"]["our_hotkey"],
        rebalance_threshold_pct=copy["config"].get("rebalance_threshold_pct", 5),
        poll_interval_sec=copy["config"].get("poll_interval_sec", 300),
    )
    _copy_engine.start_copy(cc)
    return {"id": copy_id, "status": "active"}


@app.delete("/copy/{copy_id}")
def delete_copy(copy_id: str):
    _copy_engine.stop_copy(copy_id)
    _db.delete_copy(copy_id)
    return {"deleted": True, "id": copy_id}


@app.post("/copy/{copy_id}/sync")
def sync_copy(copy_id: str):
    copy = _db.get_copy(copy_id)
    if not copy:
        raise HTTPException(404, "copy not found")
    if not _wallet:
        raise HTTPException(400, "wallet not set")

    cc = CopyConfig(
        id=copy_id,
        target_ss58=copy["target_ss58"],
        our_hotkey=copy["config"]["our_hotkey"],
        rebalance_threshold_pct=copy["config"].get("rebalance_threshold_pct", 5),
    )
    trades = _copy_engine.sync_once(cc)
    return {
        "synced": True,
        "trades": [
            {"action": t.action, "netuid": t.netuid,
             "amount_tao": t.amount_tao, "status": t.status,
             "error": t.error}
            for t in trades
        ],
    }


# ── trades ───────────────────────────────────────────────────────

@app.get("/trades", response_model=List[TradeResponse])
def list_trades(copy_id: Optional[str] = None,
                limit: int = Query(50, ge=1, le=500)):
    trades = _db.get_trades(copy_id=copy_id, limit=limit)
    return [TradeResponse(**t) for t in trades]


# ── wallet ───────────────────────────────────────────────────────

@app.post("/wallet/set")
def set_wallet(req: WalletSetRequest):
    global _wallet
    try:
        if req.mnemonic:
            w = bt.wallet(name=req.name, hotkey=req.hotkey)
            w.regenerate_coldkey(mnemonic=req.mnemonic, use_password=False,
                                overwrite=False, suppress=True)
            _wallet = w
        elif req.path:
            _wallet = bt.wallet(name=req.name, hotkey=req.hotkey, path=req.path)
        else:
            _wallet = bt.wallet(name=req.name, hotkey=req.hotkey)

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
