#!/usr/bin/env python3
"""orbit/stake API — stake BlocTime (BLOC) on Registry-registered apps.

One port (:50840): the console page, a JSON read API over the AppStaking
contract on Base Sepolia, and server-signed write endpoints for CLI use
(the console signs in the browser instead and never sends a key here).
"""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from web3 import Web3

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
NET = CFG["contracts"]["testnet"]
RPC = os.environ.get("STAKE_RPC", NET["rpc"])
CHAIN_ID = NET["chainId"]
E = 10**18

STAKING_ABI = json.loads((HERE / "artifacts" / "AppStaking.json").read_text())["abi"]
REGISTRY_ABI = json.loads("""[
 {"name":"nextModId","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
 {"name":"getMod","type":"function","stateMutability":"view","inputs":[{"name":"id","type":"uint256"}],
  "outputs":[{"name":"owner","type":"address"},{"name":"name","type":"string"},{"name":"data","type":"string"}]}
]""")
ERC20_ABI = json.loads("""[
 {"name":"balanceOf","type":"function","stateMutability":"view","inputs":[{"type":"address"}],"outputs":[{"type":"uint256"}]},
 {"name":"allowance","type":"function","stateMutability":"view","inputs":[{"type":"address"},{"type":"address"}],"outputs":[{"type":"uint256"}]},
 {"name":"approve","type":"function","stateMutability":"nonpayable","inputs":[{"type":"address"},{"type":"uint256"}],"outputs":[{"type":"bool"}]},
 {"name":"totalSupply","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]}
]""")

_w3 = None


def w3() -> Web3:
    global _w3
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
    return _w3


def staking():
    return w3().eth.contract(address=NET["AppStaking"], abi=STAKING_ABI)


def registry():
    return w3().eth.contract(address=NET["Registry"], abi=REGISTRY_ABI)


def bloc():
    return w3().eth.contract(address=NET["BlocTime"], abi=ERC20_ABI)


def read(fn, retries=3):
    """sepolia.base.org's load balancer drops single requests — retry reads."""
    last = None
    for _ in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1)
    raise last


def fmt(wei: int) -> float:
    return round(wei / E, 6)


# ---------------------------------------------------------------- app catalog

_cache = {"apps": None, "ts": 0.0}
_cache_lock = threading.Lock()
CACHE_TTL = 30


def _catalog_meta() -> dict:
    """Descriptions/versions from the local web catalog; best-effort.

    Goes through the activator (:9000) first so a sleeping web module gets
    woken instead of the direct port hanging.
    """
    import urllib.request
    for url in ("http://localhost:9000/api/web/mods?limit=500",
                "http://localhost:50420/mods?limit=500"):
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read())
            mods = data if isinstance(data, list) else data.get("mods", [])
            return {m.get("name"): {"description": m.get("description", ""),
                                    "version": m.get("version", "")} for m in mods if m.get("name")}
        except Exception:
            continue
    return {}


def load_apps() -> list:
    with _cache_lock:
        if _cache["apps"] is not None and time.time() - _cache["ts"] < CACHE_TTL:
            return _cache["apps"]

    reg = registry()
    next_id = read(lambda: reg.functions.nextModId().call())
    apps = []
    for app_id in range(1, next_id):
        owner, name, data = read(lambda i=app_id: reg.functions.getMod(i).call())
        if int(owner, 16) == 0:
            continue  # removed slot
        apps.append({"id": app_id, "name": name, "owner": owner, "data": data})

    ids = [a["id"] for a in apps]
    if ids:
        totals, rewards, staker_counts = read(lambda: staking().functions.getTotals(ids).call())
        for a, t, r, s in zip(apps, totals, rewards, staker_counts):
            a.update({"staked": fmt(t), "staked_wei": str(t),
                      "rewarded": fmt(r), "stakers": s})

    meta = _catalog_meta()
    for a in apps:
        a.update(meta.get(a["name"], {}))
    apps.sort(key=lambda a: (-float(a.get("staked", 0)), a["id"]))

    with _cache_lock:
        _cache.update(apps=apps, ts=time.time())
    return apps


def invalidate():
    with _cache_lock:
        _cache["apps"] = None


# --------------------------------------------------------------------- server

app = FastAPI(title="stake", description=CFG["description"])


@app.get("/health")
def health():
    return {"ok": True, "chain_id": CHAIN_ID, "contract": NET["AppStaking"]}


@app.get("/info")
def info():
    return {"name": "stake", "description": CFG["description"],
            "network": "base-sepolia", "chain_id": CHAIN_ID,
            "contracts": {k: NET[k] for k in ("AppStaking", "BlocTime", "Registry")},
            "rpc": RPC}


@app.get("/apps")
def apps():
    apps = load_apps()
    return {"apps": apps, "total_staked": round(sum(a.get("staked", 0) for a in apps), 6),
            "count": len(apps)}


@app.get("/apps/{app_id}")
def app_detail(app_id: int):
    match = next((a for a in load_apps() if a["id"] == app_id), None)
    if not match:
        raise HTTPException(404, "app not registered")
    stakers, amounts = read(lambda: staking().functions.getAppStakers(app_id).call())
    book = sorted(({"address": s, "staked": fmt(a)} for s, a in zip(stakers, amounts) if a > 0),
                  key=lambda x: -x["staked"])
    return {**match, "book": book}


@app.get("/positions")
def positions(address: str):
    addr = Web3.to_checksum_address(address)
    ids, amounts, claimable = read(lambda: staking().functions.getPositions(addr).call())
    names = {a["id"]: a["name"] for a in load_apps()}
    pos = [{"id": i, "name": names.get(i, f"app #{i}"), "staked": fmt(a), "claimable": fmt(c)}
           for i, a, c in zip(ids, amounts, claimable) if a > 0 or c > 0]
    balance = read(lambda: bloc().functions.balanceOf(addr).call())
    allowance = read(lambda: bloc().functions.allowance(addr, NET["AppStaking"]).call())
    return {"address": addr, "bloc_balance": fmt(balance), "allowance": fmt(allowance),
            "positions": pos,
            "total_staked": round(sum(p["staked"] for p in pos), 6),
            "total_claimable": round(sum(p["claimable"] for p in pos), 6)}


@app.get("/contract")
def contract_info():
    return {"chain_id": CHAIN_ID, "rpc": RPC,
            "AppStaking": {"address": NET["AppStaking"], "abi": STAKING_ABI},
            "BlocTime": {"address": NET["BlocTime"], "abi": ERC20_ABI},
            "explorer": f"https://sepolia.basescan.org/address/{NET['AppStaking']}"}


# ------------------------------------------------------- server-signed writes

class WriteReq(BaseModel):
    app_id: int
    amount: float = 0        # BLOC (ether units); 0 on unstake = everything
    key: str = "test"        # named key under ~/.mod/key/<name>/ecdsa


def _account(name: str):
    from eth_account import Account
    key_dir = Path.home() / ".mod" / "key" / name / "ecdsa"
    try:
        key_file = next(key_dir.glob("0x*.json"))
    except StopIteration:
        raise HTTPException(400, f"no key named '{name}'")
    return Account.from_key(json.loads(key_file.read_text())["data"]["private_key"])


def _send(acct, fn, gas=400_000):
    latest = read(lambda: w3().eth.get_block("latest"))
    base_fee = latest.get("baseFeePerGas") or Web3.to_wei(0.01, "gwei")
    tip = Web3.to_wei(0.001, "gwei")
    tx = fn.build_transaction({
        "from": acct.address, "chainId": CHAIN_ID, "gas": gas,
        "nonce": w3().eth.get_transaction_count(acct.address, "pending"),
        "maxFeePerGas": base_fee * 2 + tip, "maxPriorityFeePerGas": tip,
    })
    tx_hash = w3().eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    rcpt = w3().eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if rcpt.status != 1:
        raise HTTPException(502, f"tx reverted: {tx_hash.hex()}")
    return tx_hash.hex()


def _ensure_allowance(acct, amount_wei: int) -> list:
    allowance = read(lambda: bloc().functions.allowance(acct.address, NET["AppStaking"]).call())
    if allowance >= amount_wei:
        return []
    return [_send(acct, bloc().functions.approve(NET["AppStaking"], 2**256 - 1), gas=100_000)]


@app.post("/stake")
def do_stake(req: WriteReq):
    if req.amount <= 0:
        raise HTTPException(400, "amount must be > 0")
    acct = _account(req.key)
    amount = int(req.amount * E)
    txs = _ensure_allowance(acct, amount)
    txs.append(_send(acct, staking().functions.stake(req.app_id, amount)))
    invalidate()
    return {"ok": True, "txs": txs, "staked": req.amount, "app_id": req.app_id,
            "signer": acct.address}


@app.post("/unstake")
def do_unstake(req: WriteReq):
    acct = _account(req.key)
    tx = _send(acct, staking().functions.unstake(req.app_id, int(req.amount * E)))
    invalidate()
    return {"ok": True, "txs": [tx], "app_id": req.app_id, "signer": acct.address}


@app.post("/reward")
def do_reward(req: WriteReq):
    if req.amount <= 0:
        raise HTTPException(400, "amount must be > 0")
    acct = _account(req.key)
    amount = int(req.amount * E)
    txs = _ensure_allowance(acct, amount)
    txs.append(_send(acct, staking().functions.reward(req.app_id, amount)))
    invalidate()
    return {"ok": True, "txs": txs, "rewarded": req.amount, "app_id": req.app_id,
            "signer": acct.address}


@app.post("/claim")
def do_claim(req: WriteReq):
    acct = _account(req.key)
    tx = _send(acct, staking().functions.claim(req.app_id))
    return {"ok": True, "txs": [tx], "app_id": req.app_id, "signer": acct.address}


# -------------------------------------------------------------------- console

@app.get("/")
def console():
    return FileResponse(HERE / "console.html", media_type="text/html")


@app.get("/static/ethers.umd.min.js")
def ethers_js():
    return FileResponse(HERE / "static" / "ethers.umd.min.js",
                        media_type="application/javascript")


def serve(port: int = None):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(port or CFG["port"]))


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else None)
