"""Chain Hub API — FastAPI backend for chain deployment and module orchestration."""

import json
import os
import re
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import requests

app = FastAPI(title="Chain Hub API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_chains = {}

MOD_NAMES = ['token', 'oracle', 'registry', 'perms', 'tokengate',
             'bloctime', 'treasury', 'market', 'debit', 'safe', 'bridge']

PORTS = {
    'token': {'api': 8810, 'app': 8811},
    'oracle': {'api': 8812, 'app': 8813},
    'registry': {'api': 8814, 'app': 8815},
    'perms': {'api': 8816, 'app': 8817},
    'tokengate': {'api': 8818, 'app': 8819},
    'bloctime': {'api': 8820, 'app': 8821},
    'treasury': {'api': 8822, 'app': 8823},
    'market': {'api': 8824, 'app': 8825},
    'debit': {'api': 8826, 'app': 8827},
    'safe': {'api': 8828, 'app': 8829},
    'bridge': {'api': 8830, 'app': 8831},
}


def get_chain(network='testnet', key=None):
    """Get (and cache) a chain orchestrator per network, optionally switching key."""
    global _chains
    if network not in _chains:
        import mod as m
        _chains[network] = m.mod('chain')(network=network)
    chain = _chains[network]
    if key:
        try:
            chain.set_key(key)
        except Exception:
            pass
    return chain


def _serialize(result):
    """Best-effort JSON-safe serialization for tx receipts / arbitrary results."""
    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    if hasattr(result, 'transactionHash'):
        return {
            "tx_hash": result.transactionHash.hex(),
            "status": "success" if result.get('status', 1) == 1 else "failed",
        }
    if isinstance(result, bytes):
        return '0x' + result.hex()
    if isinstance(result, dict):
        return {k: _serialize(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [_serialize(v) for v in result]
    return str(result)


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "module": "chain-hub"}


# ── Module Info ───────────────────────────────────────────────────────────

@app.get("/mods")
async def list_mods():
    """List all available contract modules with their ports and status."""
    results = []
    for name in MOD_NAMES:
        ports = PORTS.get(name, {})
        api_url = f"http://localhost:{ports.get('api', 0)}"
        alive = False
        try:
            r = requests.get(f"{api_url}/health", timeout=1)
            alive = r.status_code == 200
        except Exception:
            pass
        results.append({
            "name": name,
            "api_port": ports.get("api"),
            "app_port": ports.get("app"),
            "api_url": api_url,
            "app_url": f"http://localhost:{ports.get('app', 0)}",
            "alive": alive,
        })
    return {"mods": results}


@app.get("/status")
async def status():
    """Get chain deployment status across networks."""
    chain = get_chain()
    config = chain.config
    deployments = config.get("deployments", {})
    result = {}
    for network, info in deployments.items():
        contracts = info.get("contracts", {})
        result[network] = {
            "chainId": info.get("chainId"),
            "deployer": info.get("deployer"),
            "url": info.get("url"),
            "contract_count": len(contracts),
            "contracts": {k: v.get("address", "") for k, v in contracts.items()},
        }
    return {"deployments": result}


def _require_mainnet_confirm(network: str, confirm: bool):
    """Guard against accidental real-money mainnet writes from the UI.

    Any route that can spend gas / move funds on mainnet must pass its
    `confirm` flag through here; everything else (testnet/ganache/localhost)
    is unaffected.
    """
    if network == "mainnet" and not confirm:
        raise HTTPException(status_code=400,
                            detail="mainnet requires confirm=true")


# ── Deploy ────────────────────────────────────────────────────────────────

class DeployReq(BaseModel):
    network: str = "testnet"
    mods: Optional[List[str]] = None
    key: Optional[str] = None
    confirm: bool = False

@app.post("/deploy")
async def deploy(req: DeployReq):
    """Deploy contracts (synchronous)."""
    _require_mainnet_confirm(req.network, req.confirm)
    chain = get_chain(req.network)
    try:
        result = chain.deploy(
            network=req.network,
            mods=req.mods,
        )
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Contracts ─────────────────────────────────────────────────────────────

class NetworkReq(BaseModel):
    network: str = "testnet"

@app.post("/contracts")
async def contracts(req: NetworkReq):
    """Get deployed contract addresses for a network."""
    chain = get_chain()
    config = chain.config
    deployment = config.get("deployments", {}).get(req.network, {})
    return {"contracts": deployment.get("contracts", {})}


@app.post("/config")
async def config(req: NetworkReq):
    """Get full deployment config for a network."""
    chain = get_chain()
    config = chain.config
    return {"config": config.get("deployments", {}).get(req.network, {})}


# ── Contract source browsing (module-agnostic) ──────────────────────────────

def _module_dir(mod_name: str):
    """Resolve a module's root directory on disk (any core/orbit module)."""
    import mod as m
    try:
        return m.dp(mod_name)
    except Exception:
        return None


def _contracts_dir(mod_name: str = "chain"):
    """Resolve the Solidity contracts directory for any module, or None."""
    base = _module_dir(mod_name)
    if not base:
        return None
    for cand in (os.path.join(base, "src", "contracts"),
                 os.path.join(base, "contracts")):
        if os.path.isdir(cand):
            return cand
    return None


def _deployed_index(mod_name: str, network: str):
    """Map contract source name (e.g. 'Market') -> deployed address.

    Reads the target module's own config deployments, so it works for any
    module that publishes a deployments map (not just chain).
    """
    import mod as m
    try:
        cfg = m.config(mod_name)
    except Exception:
        return {}
    deployment = (cfg or {}).get("deployments", {}).get(network, {})
    index = {}
    for _name, info in deployment.get("contracts", {}).items():
        if isinstance(info, dict):
            c = info.get("contract")
            if c:
                index.setdefault(c, info.get("address", ""))
    return index


def _has_solidity(root: str) -> bool:
    for _dp, _dn, files in os.walk(root):
        if any(f.endswith(".sol") for f in files):
            return True
    return False


@app.get("/contracts/mods")
async def contract_mods():
    """Discover all modules (core + orbit) that ship Solidity contracts."""
    base = _module_dir("chain")
    found = []
    if base:
        mods_root = os.path.dirname(os.path.dirname(base))  # .../mod
        for group in ("core", "orbit"):
            gdir = os.path.join(mods_root, group)
            if not os.path.isdir(gdir):
                continue
            for name in sorted(os.listdir(gdir)):
                cdir = _contracts_dir_at(os.path.join(gdir, name))
                if cdir and _has_solidity(cdir):
                    found.append(name)
    if "chain" not in found:
        found.insert(0, "chain")
    return {"mods": sorted(set(found))}


def _contracts_dir_at(base: str):
    for cand in (os.path.join(base, "src", "contracts"),
                 os.path.join(base, "contracts")):
        if os.path.isdir(cand):
            return cand
    return None


@app.get("/contracts/source")
async def contract_source(file: Optional[str] = None, mod: str = "chain",
                          network: str = "testnet"):
    """List Solidity contracts for a module, or return one source via ?file=.

    `mod` selects the module (default: chain). Test files are excluded from the
    listing. `file` is sandboxed to the module's contracts dir and must end .sol.
    """
    root = _contracts_dir(mod)
    if not root:
        raise HTTPException(status_code=404,
                            detail=f"No contracts directory for module '{mod}'")

    if file:
        rel = file.lstrip("/")
        target = os.path.realpath(os.path.join(root, rel))
        if not target.startswith(os.path.realpath(root) + os.sep) or not target.endswith(".sol"):
            raise HTTPException(status_code=400, detail="Invalid contract path")
        if not os.path.isfile(target):
            raise HTTPException(status_code=404, detail=f"Contract not found: {file}")
        with open(target, "r") as f:
            source = f.read()
        return {
            "file": rel,
            "name": os.path.basename(target)[:-4],
            "lines": source.count("\n") + 1,
            "source": source,
        }

    deployed = _deployed_index(mod, network)
    contracts = []
    for dirpath, _, files in os.walk(root):
        if os.sep + "test" in dirpath + os.sep:
            continue
        for fn in files:
            if not fn.endswith(".sol"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            parts = rel.split(os.sep)
            group = parts[0] if len(parts) > 1 else mod
            name = fn[:-4]
            try:
                lines = sum(1 for _ in open(full))
            except Exception:
                lines = 0
            contracts.append({
                "name": name,
                "mod": group,
                "file": rel.replace(os.sep, "/"),
                "lines": lines,
                "address": deployed.get(name, ""),
            })
    contracts.sort(key=lambda c: (c["mod"], c["name"]))
    return {"contracts": contracts, "count": len(contracts), "module": mod}


# ── Contract ABIs + sources, stored in localfs by CID ────────────────────────
#
# ABIs and Solidity sources are pinned into the content-addressable localfs
# store; their CIDs are recorded per-contract in config.json ("abi" / "src"),
# same pattern the deploy pipeline already uses. Serving prefers the pinned
# CID; hardhat artifacts are only read to pin content the first time.

_artifact_cache = {}

def _localfs():
    try:
        import mod as m
        return m.mod('localfs')()
    except Exception:
        return None


def _load_artifact(contract_type: str):
    """Load a compiled hardhat artifact ({abi, sourceName, ...}) for a contract type."""
    if contract_type in _artifact_cache:
        return _artifact_cache[contract_type]
    artifact = None
    base = _module_dir("chain")
    root = os.path.join(base, "artifacts", "src", "contracts") if base else ""
    if os.path.isdir(root):
        want_dir, want_file = f"{contract_type}.sol", f"{contract_type}.json"
        for dirpath, _dn, files in os.walk(root):
            if os.path.basename(dirpath) == want_dir and want_file in files:
                try:
                    with open(os.path.join(dirpath, want_file)) as f:
                        artifact = json.load(f)
                except Exception:
                    artifact = None
                break
    _artifact_cache[contract_type] = artifact
    return artifact


def _lf_get_json(lf, cid):
    """localfs.get that tolerates bytes payloads; returns parsed JSON or None."""
    try:
        data = lf.get(cid)
        if isinstance(data, bytes):
            data = json.loads(data.decode("utf-8"))
        return data
    except Exception:
        return None


@app.get("/contracts/abis")
async def contract_abis(network: str = "testnet"):
    """Deployed contracts with addresses + ABIs (localfs-pinned), for wallet-side calls."""
    base = _module_dir("chain")
    cfg_path = os.path.join(base, "config.json") if base else None
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = get_chain().config or {}

    deployment = cfg.get("deployments", {}).get(network, {})
    contracts = deployment.get("contracts") or {}
    lf = _localfs()
    out, dirty = [], False

    for name, info in contracts.items():
        if not isinstance(info, dict) or not info.get("address"):
            continue
        ctype = info.get("contract") or name
        abi, abi_cid, src_cid = None, info.get("abi"), info.get("src")

        # 1) pinned ABI CID from config
        if lf and abi_cid:
            abi = _lf_get_json(lf, abi_cid)

        # 2) fall back to the hardhat artifact — and pin it for next time
        if abi is None:
            artifact = _load_artifact(ctype)
            abi = (artifact or {}).get("abi")
            if lf and abi:
                try:
                    abi_cid = lf.put(abi)
                    info["abi"] = abi_cid
                    dirty = True
                except Exception:
                    pass

        # pin the Solidity source alongside (once)
        if lf and not src_cid:
            artifact = _load_artifact(ctype)
            src_path = os.path.join(base, (artifact or {}).get("sourceName", "")) if base else ""
            if src_path and os.path.isfile(src_path):
                try:
                    with open(src_path) as f:
                        src_cid = lf.put(f.read())
                    info["src"] = src_cid
                    dirty = True
                except Exception:
                    pass

        if not abi:
            continue
        out.append({"name": name, "contract": ctype, "address": info["address"],
                    "abi": abi, "abi_cid": abi_cid, "src_cid": src_cid})

    # persist newly pinned CIDs back into config.json (the canonical index)
    if dirty and cfg_path:
        try:
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
        except Exception:
            pass

    out.sort(key=lambda c: c["name"].lower())
    return {"network": network, "chainId": deployment.get("chainId"),
            "rpc_url": deployment.get("url"), "contracts": out, "count": len(out)}


@app.get("/cid/{cid}")
async def cid_get(cid: str):
    """Fetch pinned content (ABI JSON / Solidity source) from localfs by CID."""
    lf = _localfs()
    if not lf:
        raise HTTPException(status_code=503, detail="localfs unavailable")
    try:
        data = lf.get(cid)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"CID not found: {cid}")
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except Exception:
            raise HTTPException(status_code=415, detail="Binary content")
    return {"cid": cid, "content": data}


# ── Module Proxy ──────────────────────────────────────────────────────────

class ModCallReq(BaseModel):
    method: str
    args: Optional[list] = None
    network: str = "testnet"

@app.post("/call")
async def mod_call(req: ModCallReq):
    """Call a method on the chain orchestrator (stake, credit, whitelist_token, …)."""
    chain = get_chain(req.network)
    try:
        method = getattr(chain, req.method, None)
        if not method or req.method.startswith("_"):
            raise HTTPException(status_code=404, detail=f"Method {req.method} not found")
        result = method(*(req.args or []))
        if hasattr(result, 'transactionHash'):
            return {"result": {
                "tx_hash": result.transactionHash.hex(),
                "status": "success" if result.status == 1 else "failed",
            }}
        return {"result": str(result)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/block")
async def block(number: Optional[int] = None):
    """Get latest block number, or block info by number."""
    chain = get_chain()
    return {"result": chain.block(number)}


@app.get("/timestamp")
async def timestamp(number: Optional[int] = None):
    """Get the timestamp of a block (latest if no number given)."""
    chain = get_chain()
    return {"result": chain.timestamp(number)}


# ── Protocol: Wallet & Balances ─────────────────────────────────────────────

@app.get("/wallet")
async def wallet(network: str = "testnet", key: Optional[str] = None):
    """Get the active account address + balances across core tokens."""
    chain = get_chain(network, key)
    try:
        address = chain.account.address
        balances = chain.balances(address)
        return {
            "address": address,
            "network": network,
            "balances": _serialize(balances),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/balances")
async def balances(address: Optional[str] = None, network: str = "testnet",
                   tokens: Optional[str] = None):
    """Get token balances for an address (comma-separated tokens optional)."""
    chain = get_chain(network)
    try:
        addr = address or chain.account.address
        tok_list = [t.strip() for t in tokens.split(",")] if tokens else None
        result = chain.balances(addr, tokens=tok_list)
        return {"address": addr, "balances": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Protocol: Staking (BlocTime) ────────────────────────────────────────────

class StakeReq(BaseModel):
    amount: float
    lock_blocks: int
    network: str = "testnet"
    key: Optional[str] = None

@app.post("/stake")
async def stake(req: StakeReq):
    """Stake NativeToken to earn BlocTime."""
    chain = get_chain(req.network, req.key)
    try:
        decimals = chain.decimals('nativetoken')
        amount_wei = int(req.amount * (10 ** decimals))
        result = chain.stake(amount_wei, req.lock_blocks)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class UnstakeReq(BaseModel):
    stake_id: int
    network: str = "testnet"
    key: Optional[str] = None

@app.post("/unstake")
async def unstake(req: UnstakeReq):
    """Unstake a specific BlocTime position."""
    chain = get_chain(req.network, req.key)
    try:
        result = chain.unstake(req.stake_id)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/stakes")
async def stakes(address: Optional[str] = None, network: str = "testnet"):
    """List all stake positions for an address."""
    chain = get_chain(network)
    try:
        addr = address or chain.account.address
        ids = chain.get_user_stake_ids(addr)
        positions = []
        for sid in ids:
            pos = chain.get_stake_position(addr, sid)
            pos['stake_id'] = sid
            positions.append(_serialize(pos))
        return {"address": addr, "stakes": positions}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Protocol: Market (credit / transfer) ────────────────────────────────────

class CreditReq(BaseModel):
    stable_amount: float
    payment_token: str = "usdt"
    network: str = "testnet"
    key: Optional[str] = None

@app.post("/credit")
async def credit(req: CreditReq):
    """Buy MARKET (stable) tokens with a whitelisted payment token."""
    chain = get_chain(req.network, req.key)
    try:
        result = chain.raw_credit(req.stable_amount, payment_token=req.payment_token)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class TransferReq(BaseModel):
    to: str
    amount: float
    token: str = "market"
    network: str = "testnet"
    key: Optional[str] = None

@app.post("/transfer")
async def transfer(req: TransferReq):
    """Transfer tokens (or native ETH) to another address."""
    chain = get_chain(req.network, req.key)
    try:
        result = chain.transfer(req.to, req.amount, token=req.token)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/tokens")
async def tokens(network: str = "testnet"):
    """List whitelisted payment tokens (TokenGate) + all known tokens."""
    chain = get_chain(network)
    try:
        cfg = chain.contracts_config()
        known = [k for k, v in cfg.items() if v.get("contract") == "Token"]
        whitelisted = []
        try:
            whitelisted = chain.tokens()
        except Exception:
            pass
        return {"tokens": known, "whitelisted": _serialize(whitelisted)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Protocol: BlocTime ownership ────────────────────────────────────────────

@app.get("/bloctime/owner")
async def bloctime_owner(address: Optional[str] = None, network: str = "testnet"):
    """Check whether an address holds BlocTime (staked), and how much."""
    chain = get_chain(network)
    try:
        addr = address or chain.account.address
        balance = chain.bloctime_balance(addr)
        return {"address": addr, "bloctime": balance, "is_owner": balance > 0}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Per-mod BlocTime staking ─────────────────────────────────────────────────
#
# Holders allocate their on-chain BlocTime weight to individual modules — a
# curation signal ("skin in the game" per mod) surfaced in the web catalog.
# The allocation ledger lives off-chain (~/.mod/chain/mod_stakes.json), but
# every stake is backed 1:1 by the address's live BlocTime balance: you can
# never have more allocated across mods than you hold on-chain.

MOD_STAKES_PATH = Path(os.path.expanduser("~/.mod/chain/mod_stakes.json"))


def _load_mod_stakes() -> dict:
    """{network: {mod_name: {address: amount_wei}}} — empty dict when absent."""
    try:
        with open(MOD_STAKES_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_mod_stakes(data: dict):
    MOD_STAKES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MOD_STAKES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(MOD_STAKES_PATH)


def _allocated(book: dict, addr: str) -> int:
    """Total wei `addr` has staked across every mod on one network."""
    return sum(int(stakers.get(addr, 0)) for stakers in book.values())


@app.get("/mods/stakes")
async def mod_stakes_all(network: str = "testnet"):
    """Total BlocTime staked per module — the catalog's ranking signal."""
    book = _load_mod_stakes().get(network, {})
    mods = {}
    for name, stakers in book.items():
        amounts = [int(v) for v in stakers.values() if int(v) > 0]
        if amounts:
            mods[name] = {"total": sum(amounts), "stakers": len(amounts)}
    return {
        "network": network,
        "mods": mods,
        "total": sum(m["total"] for m in mods.values()),
    }


@app.get("/mods/stakes/{name}")
async def mod_stakes_one(name: str, address: Optional[str] = None,
                         key: Optional[str] = None, network: str = "testnet"):
    """One module's stake book; with ?address= (or ?key=) also the caller's
    position and how much BlocTime they still have free to stake."""
    name = name.strip().lower()
    book = _load_mod_stakes().get(network, {})
    stakers = {a: int(v) for a, v in book.get(name, {}).items() if int(v) > 0}
    out = {
        "name": name,
        "network": network,
        "total": sum(stakers.values()),
        "stakers": [
            {"address": a, "amount": v}
            for a, v in sorted(stakers.items(), key=lambda kv: -kv[1])
        ],
    }
    if address or key:
        chain = get_chain(network, key)
        addr = address or chain.account.address
        try:
            balance = chain.bloctime_balance(addr)
        except Exception:
            balance = 0
        allocated = _allocated(book, addr)
        out.update({
            "address": addr,
            "my_stake": stakers.get(addr, 0),
            "bloctime": balance,
            "available": max(0, balance - allocated),
        })
    return out


class ModStakeReq(BaseModel):
    name: str
    amount: float                 # BLOC (18-decimals token, ether units)
    key: Optional[str] = None
    network: str = "testnet"


@app.post("/mods/stake")
async def mod_stake(req: ModStakeReq):
    """Stake BlocTime to a module. Fails if the signing key's free BlocTime
    (on-chain balance minus what it already staked to mods) can't cover it."""
    name = req.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="module name required")
    amount_wei = int(req.amount * 10**18)
    if amount_wei <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    chain = get_chain(req.network, req.key)
    try:
        addr = chain.account.address
        balance = chain.bloctime_balance(addr)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = _load_mod_stakes()
    book = data.setdefault(req.network, {})
    allocated = _allocated(book, addr)
    if allocated + amount_wei > balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"insufficient BlocTime: balance {balance / 1e18:.4f} BLOC, "
                f"already staked {allocated / 1e18:.4f} — stake MOD (POST /stake) "
                f"to earn more BlocTime first"
            ),
        )
    entry = book.setdefault(name, {})
    entry[addr] = int(entry.get(addr, 0)) + amount_wei
    _save_mod_stakes(data)
    return {
        "name": name,
        "address": addr,
        "my_stake": entry[addr],
        "total": sum(int(v) for v in entry.values()),
        "bloctime": balance,
        "available": max(0, balance - allocated - amount_wei),
    }


class ModUnstakeReq(BaseModel):
    name: str
    amount: Optional[float] = None  # BLOC; omit to withdraw the whole stake
    key: Optional[str] = None
    network: str = "testnet"


@app.post("/mods/unstake")
async def mod_unstake(req: ModUnstakeReq):
    """Withdraw some or all of the signing key's stake from a module."""
    name = req.name.strip().lower()
    chain = get_chain(req.network, req.key)
    addr = chain.account.address
    data = _load_mod_stakes()
    book = data.setdefault(req.network, {})
    entry = book.get(name, {})
    current = int(entry.get(addr, 0))
    if current <= 0:
        raise HTTPException(status_code=400, detail=f"no stake on '{name}' from {addr}")
    amount_wei = current if req.amount is None else int(req.amount * 10**18)
    if amount_wei <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    amount_wei = min(amount_wei, current)
    remaining = current - amount_wei
    if remaining > 0:
        entry[addr] = remaining
    else:
        entry.pop(addr, None)
        if not entry:
            book.pop(name, None)
    _save_mod_stakes(data)
    try:
        balance = chain.bloctime_balance(addr)
    except Exception:
        balance = 0
    return {
        "name": name,
        "address": addr,
        "my_stake": remaining,
        "total": sum(int(v) for v in entry.values()) if entry else 0,
        "bloctime": balance,
        "available": max(0, balance - _allocated(book, addr)),
    }


# ── Protocol: Registry ──────────────────────────────────────────────────────

@app.get("/registry/mods")
async def registry_mods(address: Optional[str] = None, network: str = "testnet"):
    """List mods registered by an address in the on-chain Registry."""
    chain = get_chain(network)
    try:
        addr = address or chain.account.address
        mods = chain.mods(address=addr)
        return {"address": addr, "mods": _serialize(mods)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/registry/all")
async def registry_all(network: str = "testnet"):
    """List every mod registered in the on-chain Registry, across all owners.

    This is the global view consumed by the web catalog to mark which modules
    are registered on-chain. Returns name, owner, id, and data (CID) per mod.
    """
    chain = get_chain(network)
    try:
        mods = chain.allmods()
        return {"network": network, "count": len(mods), "mods": _serialize(mods)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class RegReq(BaseModel):
    name: str
    data: Optional[str] = None
    network: str = "testnet"
    key: Optional[str] = None

@app.post("/registry/register")
async def registry_register(req: RegReq):
    """Register (or update) a mod in the on-chain Registry."""
    chain = get_chain(req.network, req.key)
    try:
        result = chain.reg(req.name, req.data)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Gated registration + MOD mint (BlocTime or pay $1 → pool) ────────────────

class GatedRegReq(BaseModel):
    name: str
    data: str                     # registry payload (e.g. the module's schema CID)
    key: Optional[str] = None
    pay: bool = False             # if no BlocTime, confirm the $1 charge
    payment_token: str = "usdc"
    network: str = "testnet"


@app.post("/register")
async def register(req: GatedRegReq):
    """Register a module on-chain, gated on BlocTime.

    Holds BlocTime → registers free. No BlocTime + pay=false → returns
    {status:'payment_required'} so the UI can confirm. No BlocTime + pay=true →
    mints $1 of MOD (the $1 funds the weekly pool) then registers.
    """
    chain = get_chain(req.network, req.key)
    try:
        result = chain.register(req.name, data=req.data, pay=req.pay,
                                payment_token=req.payment_token)
        return _serialize(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class MintReq(BaseModel):
    usd: float = 1.0
    payment_token: str = "usdc"
    network: str = "testnet"
    key: Optional[str] = None


@app.post("/mint")
async def mint(req: MintReq):
    """Mint MOD for `usd` dollars; the payment is deposited into the reward pool
    distributed to BlocTime holders."""
    chain = get_chain(req.network, req.key)
    try:
        return {"result": _serialize(chain.mint(req.payment_token, req.usd))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Reward pool (weekly distribution to BlocTime holders) ────────────────────

@app.get("/pool")
async def pool(network: str = "testnet"):
    """Current reward-pool state: size, governance token (BlocTime), holders."""
    chain = get_chain(network)
    try:
        return _serialize(chain.pool())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/pool/claimable")
async def pool_claimable(address: Optional[str] = None, network: str = "testnet"):
    """What an address can claim from the pool right now (by BlocTime share)."""
    chain = get_chain(network)
    try:
        return _serialize(chain.pool_claimable(address))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class ClaimReq(BaseModel):
    token: Optional[str] = None   # token symbol/address; None = claim all
    network: str = "testnet"
    key: Optional[str] = None


@app.post("/pool/claim")
async def pool_claim(req: ClaimReq):
    """Claim the caller's share of the pool (one token, or all)."""
    chain = get_chain(req.network, req.key)
    try:
        return _serialize(chain.pool_claim(req.token))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/pool/epochs")
async def pool_epochs(limit: int = 12, network: str = "testnet"):
    """Recent weekly pool snapshots recorded by the keeper."""
    chain = get_chain(network)
    try:
        return {"epochs": _serialize(chain.pool_epochs(limit))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pool/snapshot")
async def pool_snapshot(req: NetworkReq):
    """Record a weekly pool snapshot (called by the scheduled keeper)."""
    chain = get_chain(req.network)
    try:
        return _serialize(chain.pool_snapshot())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── DeFi yield aggregator ────────────────────────────────────────────────────
#
# Modular multi-strategy vault: deposit USDC/USDT/etc into one of many lowfi
# yield strategies; harvested profit is routed through Market and minted to
# depositors as native tokens (pro-rata).

class YieldActionReq(BaseModel):
    strategy_id: int
    amount: float = 0.0
    network: str = "testnet"
    key: Optional[str] = None


class YieldHarvestReq(BaseModel):
    strategy_id: int
    network: str = "testnet"
    key: Optional[str] = None


@app.get("/yield/strategies")
async def yield_strategies(network: str = "testnet"):
    """List the registered yield strategies (the lowfi yield options)."""
    chain = get_chain(network)
    try:
        return {"strategies": _serialize(chain.yield_strategies())}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/yield/position")
async def yield_position(strategy_id: int, address: Optional[str] = None, network: str = "testnet"):
    """A user's position in a strategy: principal shares + claimable native reward."""
    chain = get_chain(network)
    try:
        return _serialize(chain.yield_position(strategy_id, address))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/yield/deposit")
async def yield_deposit(req: YieldActionReq):
    """Deposit an asset into a yield strategy."""
    chain = get_chain(req.network, req.key)
    try:
        return {"result": _serialize(chain.yield_deposit(req.strategy_id, req.amount))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/yield/withdraw")
async def yield_withdraw(req: YieldActionReq):
    """Withdraw principal shares from a yield strategy."""
    chain = get_chain(req.network, req.key)
    try:
        return {"result": _serialize(chain.yield_withdraw(req.strategy_id, req.amount))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/yield/harvest")
async def yield_harvest(req: YieldHarvestReq):
    """Harvest a strategy's yield → mint native tokens to depositors."""
    chain = get_chain(req.network, req.key)
    try:
        return {"result": _serialize(chain.yield_harvest(req.strategy_id))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/yield/claim")
async def yield_claim(req: YieldHarvestReq):
    """Claim accrued native reward tokens for a strategy."""
    chain = get_chain(req.network, req.key)
    try:
        return {"result": _serialize(chain.yield_claim(req.strategy_id))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Owner Console (admin) ───────────────────────────────────────────────────
#
# Owner-only operations across the protocol contracts. Every action can be
# either executed directly (signed by the active deployer key) or encoded into
# calldata for a Safe multisig to execute once it owns the contracts.

# Module key (lowercased config name) -> display name. Only owner-managed
# protocol contracts are surfaced here.
ADMIN_MODULES = [
    ("tokengate", "TokenGate"),
    ("manualpriceoracle", "ManualPriceOracle"),
    ("market", "Market"),
    ("treasury", "Treasury"),
    ("bloctime", "BlocTime"),
    ("perms", "Perms"),
]

ZERO_ADDR = "0x0000000000000000000000000000000000000000"


@app.get("/admin/owners")
async def admin_owners(network: str = "testnet", key: Optional[str] = None):
    """Owner status of every owner-managed protocol contract on a network.

    Returns each contract's address + current owner, whether the active account
    is that owner, and whether ownership has been renounced (ownerless).
    """
    chain = get_chain(network, key)
    try:
        account = chain.account.address
    except Exception:
        account = None
    deployment = chain.config.get("deployments", {}).get(network, {})
    out = []
    for modkey, dispname in ADMIN_MODULES:
        contract = chain.contracts.get(modkey)
        if not contract:
            continue
        owner = chain.admin_owner(modkey)
        owner = owner if owner else None
        ownerless = owner is None or owner.lower() == ZERO_ADDR
        out.append({
            "key": modkey,
            "name": dispname,
            "address": contract.address,
            "owner": owner,
            "ownerless": ownerless,
            "is_owner": bool(owner and account and owner.lower() == account.lower()),
        })
    return {
        "contracts": out,
        "account": account,
        "deployer": deployment.get("deployer"),
        "chainId": deployment.get("chainId"),
        "network": network,
    }


class AdminReq(BaseModel):
    contract: str
    method: str
    args: list = []
    value: str = "0"
    network: str = "testnet"
    key: Optional[str] = None
    confirm: bool = False


@app.post("/admin/encode")
async def admin_encode(req: AdminReq):
    """Encode an owner call into {to, data, value} for Safe multisig execution."""
    chain = get_chain(req.network)
    try:
        return chain.admin_encode(req.contract, req.method, req.args, req.value)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/admin/send")
async def admin_send(req: AdminReq):
    """Execute an owner call directly, signed by the active deployer key."""
    _require_mainnet_confirm(req.network, req.confirm)
    chain = get_chain(req.network, req.key)
    try:
        result = chain.admin_send(req.contract, req.method, req.args, req.value)
        return {"result": _serialize(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class TransferAllReq(BaseModel):
    to: str
    network: str = "testnet"
    key: Optional[str] = None
    confirm: bool = False

@app.post("/admin/transfer-all")
async def admin_transfer_all(req: TransferAllReq):
    """Transfer ownership of every owner-managed contract to a new owner.

    Intended for handing the whole protocol to a Safe multisig in one step.
    Only contracts the active account currently owns are transferred; the rest
    are reported as skipped. Direct-signed (the current owner must sign).
    """
    _require_mainnet_confirm(req.network, req.confirm)
    chain = get_chain(req.network, req.key)
    try:
        account = chain.account.address
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    results = []
    for modkey, dispname in ADMIN_MODULES:
        contract = chain.contracts.get(modkey)
        if not contract:
            continue
        owner = chain.admin_owner(modkey)
        if not owner or owner.lower() == ZERO_ADDR:
            results.append({"contract": dispname, "status": "skipped", "reason": "ownerless"})
            continue
        if owner.lower() != account.lower():
            results.append({"contract": dispname, "status": "skipped", "reason": "not owner"})
            continue
        try:
            chain.admin_send(modkey, "transferOwnership", [req.to])
            results.append({"contract": dispname, "status": "transferred", "to": req.to})
        except Exception as e:
            results.append({"contract": dispname, "status": "failed", "reason": str(e)})
    return {"results": results, "to": req.to}


# ── Control Panel ────────────────────────────────────────────────────────
#
# Unified deploy + verify + status view layered on top of Owner Console
# (admin_*) and Chain Hub's /status. Adds what neither covers: triggering
# Hardhat-script deploys (e.g. the DeFi vault, outside the Python
# DEPLOY_GROUPS pipeline) and Basescan/Etherscan source verification.

@app.get("/control/status")
async def control_status(network: str = "testnet", key: Optional[str] = None):
    """Per-network contract rows merging address + owner info in one view."""
    chain = get_chain(network, key)
    try:
        account = chain.account.address
    except Exception:
        account = None
    deployment = chain.config.get("deployments", {}).get(network, {})
    owners = {}
    for modkey, dispname in ADMIN_MODULES:
        if modkey not in chain.contracts:
            continue
        owner = chain.admin_owner(modkey)
        owners[dispname] = {
            "owner": owner,
            "ownerless": owner is None or owner.lower() == ZERO_ADDR,
            "is_owner": bool(owner and account and owner.lower() == account.lower()),
        }
    rows = []
    for name, info in deployment.get("contracts", {}).items():
        own = owners.get(name)
        rows.append({
            "name": name,
            "contract": info.get("contract"),
            "address": info.get("address", ""),
            "owner": own["owner"] if own else None,
            "ownerless": own["ownerless"] if own else None,
            "is_owner": own["is_owner"] if own else False,
        })
    return {
        "network": network,
        "chainId": deployment.get("chainId"),
        "deployer": deployment.get("deployer"),
        "account": account,
        "contracts": rows,
    }


class VerifyReq(BaseModel):
    network: str = "testnet"
    contract: str
    args: list = []

@app.post("/control/verify")
async def control_verify(req: VerifyReq):
    """Verify a deployed contract's source on Basescan/Etherscan (hardhat-verify)."""
    chain = get_chain(req.network)
    try:
        return chain.verify_contract(req.network, req.contract, req.args)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class DeployScriptReq(BaseModel):
    network: str = "testnet"
    script: str
    confirm: bool = False

@app.post("/control/deploy-script")
async def control_deploy_script(req: DeployScriptReq):
    """Run a whitelisted Hardhat deploy script (e.g. deploy-defi.js) against a network."""
    _require_mainnet_confirm(req.network, req.confirm)
    chain = get_chain(req.network)
    try:
        return chain.deploy_script(req.network, req.script)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Contract builder (write → compile → deploy from a wallet) ───────────────
#
# Compilation runs solc in a subprocess (src/build/compile.js); imports resolve
# against the chain module's node_modules, so @openzeppelin/… just works.
# Tests run in a throwaway Hardhat project (src/build/hardhat.template.js) that
# borrows the same node_modules — offline, no compiler download.
# Deployment itself is signed in the browser — the API never sees a user key.
# Projects and the user's own deployments live off-tree in ~/.mod/chain/build/.

BUILD_DIR = Path.home() / ".mod" / "chain" / "build"
BUILD_TIMEOUT = 120
TEST_TIMEOUT = 240


def _build_dir() -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    return BUILD_DIR


def _build_store(name: str) -> dict:
    path = _build_dir() / name
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _build_save(name: str, data: dict):
    path = _build_dir() / name
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _who(address: Optional[str]) -> str:
    """Storage key for a user — an address, or 'anon' for unsigned sessions."""
    return (address or "anon").lower()


def _safe_rel(path: str) -> str:
    """A project-relative path with no way out of the project directory."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise HTTPException(status_code=400, detail=f"bad file path: {path}")
    return "/".join(parts)


def _layout(files: dict) -> dict:
    """Normalise a project's files onto the Hardhat layout tests expect."""
    out = {}
    for raw, content in (files or {}).items():
        path = _safe_rel(raw)
        head = path.split("/")[0]
        if path.endswith(".sol") and head != "contracts":
            path = f"contracts/{path}"
        elif path.endswith((".js", ".ts", ".cjs", ".mjs")) and head != "test":
            path = f"test/{path}"
        out[path] = content
    return out


class CompileReq(BaseModel):
    source: Optional[str] = None            # single file …
    filename: str = "Contract.sol"
    sources: Optional[dict] = None          # … or a whole project {path: source}
    optimize: bool = True
    runs: int = 200


@app.post("/build/compile")
async def build_compile(req: CompileReq):
    """Compile Solidity source (one file or a project); returns artifacts + diagnostics."""
    import subprocess

    base = _module_dir("chain")
    script = os.path.join(base or "", "src", "build", "compile.js")
    if not os.path.isfile(script):
        raise HTTPException(status_code=503, detail="compiler not installed")

    if req.sources:
        # Keys are relative paths so `import "./Other.sol"` resolves between them.
        sources = {_safe_rel(p): s for p, s in req.sources.items() if p.endswith(".sol")}
        if not sources:
            raise HTTPException(status_code=400, detail="no .sol sources to compile")
    else:
        filename = os.path.basename(req.filename) or "Contract.sol"
        if not filename.endswith(".sol"):
            filename += ".sol"
        sources = {filename: req.source or ""}

    payload = json.dumps({
        "sources": sources,
        "optimize": req.optimize,
        "runs": req.runs,
    })
    try:
        proc = subprocess.run(["node", script], input=payload, capture_output=True,
                              text=True, timeout=BUILD_TIMEOUT, cwd=base)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="compile timed out")
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=(proc.stderr or "compiler failed")[:500])

    errors, warnings = [], []
    for d in out.get("errors", []):
        entry = {
            "severity": d.get("severity", "error"),
            "message": d.get("formattedMessage") or d.get("message", ""),
            "line": (d.get("sourceLocation") or {}).get("start"),
        }
        (errors if entry["severity"] == "error" else warnings).append(entry)

    # Only contracts from the user's own files are deployable — imports are deps.
    contracts = []
    for path in sources:
        for name, c in (out.get("contracts", {}).get(path, {}) or {}).items():
            bytecode = "0x" + (c.get("evm", {}).get("bytecode", {}).get("object") or "")
            deployed = c.get("evm", {}).get("deployedBytecode", {}).get("object") or ""
            abi = c.get("abi") or []
            ctor = next((f for f in abi if f.get("type") == "constructor"), None)
            contracts.append({
                "name": name,
                "file": path,
                "abi": abi,
                "bytecode": bytecode,
                "size": len(deployed) // 2,
                "abstract": len(bytecode) <= 2,
                "constructor": (ctor or {}).get("inputs", []),
            })
    contracts.sort(key=lambda c: (c["abstract"], c["name"].lower()))

    return {"ok": not errors, "contracts": contracts,
            "errors": errors, "warnings": warnings, "solc": out.get("version")}


@app.get("/build/templates")
async def build_templates():
    """Starter projects — each a contract plus the test that proves it works."""
    base = _module_dir("chain")
    root = os.path.join(base or "", "src", "build", "templates")
    out = []
    for fname in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not fname.endswith(".sol"):
            continue
        stem = fname[:-4]
        with open(os.path.join(root, fname)) as f:
            source = f.read()
        test = ""
        test_path = os.path.join(root, f"{stem}.test.js")
        if os.path.isfile(test_path):
            with open(test_path) as f:
                test = f.read()
        # second comment line of each template is its one-line description
        lines = source.splitlines()
        desc = next((l.lstrip("/ ").strip() for l in lines[:4]
                     if l.startswith("//") and "SPDX" not in l), "")
        files = {f"contracts/{fname}": source}
        if test:
            files[f"test/{stem}.test.js"] = test
        out.append({"key": stem.lower(), "name": stem, "description": desc,
                    "source": source, "files": files})
    return {"templates": out}


# ── Projects ────────────────────────────────────────────────────────────────
#
# A project is a named bag of files — contracts/*.sol next to test/*.test.js —
# stored per wallet address. It's what the sidebar lists and what the test
# runner materialises into a Hardhat sandbox.


def _projects_store() -> dict:
    """All users' projects, migrating any pre-project drafts on first read."""
    store = _build_store("projects.json")
    drafts = _build_store("drafts.json")
    changed = False
    for user, user_drafts in drafts.items():
        if user in store or not user_drafts:
            continue
        for dname, d in user_drafts.items():
            stem = dname[:-4] if dname.endswith(".sol") else dname
            store.setdefault(user, {})[stem] = {
                "files": {f"contracts/{stem}.sol": d.get("source", "")},
                "updated": d.get("updated", 0),
            }
        changed = True
    if changed:
        _build_save("projects.json", store)
    return store


def _project(address: Optional[str], name: str) -> dict:
    proj = _projects_store().get(_who(address), {}).get(name)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"no project named {name}")
    return proj


class ProjectReq(BaseModel):
    address: Optional[str] = None
    name: str
    files: dict = {}


@app.get("/build/projects")
async def build_projects(address: Optional[str] = None):
    """List a user's projects (newest first), without file bodies."""
    projects = _projects_store().get(_who(address), {})
    rows = [{
        "name": n,
        "updated": p.get("updated", 0),
        "files": sorted((p.get("files") or {}).keys()),
    } for n, p in projects.items()]
    rows.sort(key=lambda p: p.get("updated", 0), reverse=True)
    return {"projects": rows}


@app.get("/build/projects/{name}")
async def build_project_get(name: str, address: Optional[str] = None):
    """One project, files and all."""
    return {"name": name, **_project(address, name)}


@app.post("/build/projects")
async def build_project_save(req: ProjectReq):
    """Create or overwrite a project under the caller's address."""
    import time
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="project needs a name")
    store = _projects_store()
    files = {_safe_rel(p): s for p, s in (req.files or {}).items()}
    store.setdefault(_who(req.address), {})[req.name] = {
        "files": files, "updated": time.time(),
    }
    _build_save("projects.json", store)
    return {"ok": True, "name": req.name, "files": sorted(files)}


@app.delete("/build/projects")
async def build_project_delete(name: str, address: Optional[str] = None):
    store = _projects_store()
    user = store.get(_who(address), {})
    if user.pop(name, None) is None:
        raise HTTPException(status_code=404, detail=f"no project named {name}")
    _build_save("projects.json", store)
    return {"ok": True}


# ── Shared projects ─────────────────────────────────────────────────────────
#
# A gallery: publish a project you built, anyone can fork it into their own
# sidebar. Entries are keyed "<author>/<name>". The fleet's own contracts ship
# as read-only entries, so the gallery is never empty and BlocTime is one click
# away from a working editor.

# Paths are chain-module-relative and read fresh on every request, so editing a
# fleet contract updates its gallery entry without a republish.
BUILTIN_SHARED = [{
    "id": "fleet/bloctime",
    "name": "bloctime",
    "author": "fleet",
    "description": "Stake an ERC20 for a block duration, mint blocTime on a multiplier curve.",
    "sources": {
        "contracts/BlocTime.sol": "src/contracts/bloctime/BlocTime.sol",
        "contracts/Token.sol": "src/build/shared/bloctime/Token.sol",
        "test/BlocTime.test.js": "src/contracts/bloctime/test/BlocTime.test.js",
    },
}]


def _builtin_files(entry: dict) -> dict:
    """Read a shipped entry's files off disk, skipping any that have moved."""
    base = _module_dir("chain") or ""
    files = {}
    for path, rel in entry["sources"].items():
        try:
            with open(os.path.join(base, rel)) as f:
                files[path] = f.read()
        except OSError:
            continue
    return files


def _shared_store() -> dict:
    return _build_store("shared.json")


def _shared_id(author: str, name: str) -> str:
    return f"{author}/{_safe_rel(name)}"


class ShareReq(BaseModel):
    address: str
    name: str
    description: str = ""
    files: dict = {}


@app.get("/build/shared")
async def build_shared_list():
    """Everything in the gallery — shipped entries first, then newest published."""
    rows = [{
        "id": e["id"], "name": e["name"], "author": e["author"],
        "description": e["description"], "files": sorted(_builtin_files(e)),
        "updated": 0, "builtin": True,
    } for e in BUILTIN_SHARED]
    published = [{
        "id": eid,
        "name": e.get("name", eid.split("/")[-1]),
        "author": e.get("author", "anon"),
        "description": e.get("description", ""),
        "files": sorted((e.get("files") or {}).keys()),
        "updated": e.get("updated", 0),
        "builtin": False,
    } for eid, e in _shared_store().items()]
    published.sort(key=lambda e: e["updated"], reverse=True)
    return {"shared": rows + published}


@app.get("/build/shared/{entry_id:path}")
async def build_shared_get(entry_id: str):
    """One gallery entry, files and all — what a fork copies."""
    for e in BUILTIN_SHARED:
        if e["id"] == entry_id:
            return {**{k: v for k, v in e.items() if k != "sources"},
                    "files": _builtin_files(e), "builtin": True}
    entry = _shared_store().get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no shared project {entry_id}")
    return {"id": entry_id, "builtin": False, **entry}


@app.post("/build/shared")
async def build_shared_publish(req: ShareReq):
    """Publish a project under the caller's address. Republishing overwrites."""
    import time
    author = _who(req.address)
    if author == "anon":
        raise HTTPException(status_code=400, detail="sign in with a wallet to share a project")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="project needs a name")
    if not req.files:
        raise HTTPException(status_code=400, detail="nothing to share — the project has no files")
    entry_id = _shared_id(author, req.name.strip())
    store = _shared_store()
    store[entry_id] = {
        "name": req.name.strip(),
        "author": author,
        "description": req.description.strip()[:280],
        "files": {_safe_rel(p): s for p, s in req.files.items()},
        "updated": time.time(),
    }
    _build_save("shared.json", store)
    return {"ok": True, "id": entry_id}


@app.delete("/build/shared")
async def build_shared_unpublish(id: str, address: Optional[str] = None):
    """Pull your own entry back out of the gallery."""
    store = _shared_store()
    entry = store.get(id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no shared project {id}")
    if entry.get("author") != _who(address):
        raise HTTPException(status_code=403, detail="only the author can unshare this")
    store.pop(id)
    _build_save("shared.json", store)
    return {"ok": True}


# ── Test runner ─────────────────────────────────────────────────────────────
#
# Each user gets one sandbox under ~/.mod/chain/build/run/<who>/ holding the
# generated hardhat.config.js, their files, and a warm compile cache. The
# sandbox borrows the chain module's node_modules by symlink, so Hardhat, its
# in-process EVM and solc are all already on disk — a run never hits the network.

_test_locks: dict = {}


def _sandbox(who: str) -> Path:
    """A ready-to-run Hardhat project directory for one user."""
    import shutil

    base = _module_dir("chain")
    template = os.path.join(base or "", "src", "build", "hardhat.template.js")
    if not os.path.isfile(template):
        raise HTTPException(status_code=503, detail="test runner not installed")

    root = _build_dir() / "run" / re.sub(r"[^a-z0-9]", "_", who)
    root.mkdir(parents=True, exist_ok=True)

    modules = root / "node_modules"
    if not modules.exists():
        modules.symlink_to(os.path.join(base, "node_modules"))
    # Hardhat insists on a package.json at (or above) the project root.
    (root / "package.json").write_text('{"name": "chain-build-sandbox", "private": true}\n')
    shutil.copyfile(template, root / "hardhat.config.js")

    for sub in ("contracts", "test"):
        shutil.rmtree(root / sub, ignore_errors=True)
        (root / sub).mkdir()
    return root


class TestReq(BaseModel):
    address: Optional[str] = None
    files: dict = {}
    grep: Optional[str] = None       # run only tests whose name matches


@app.post("/build/test")
async def build_test(req: TestReq):
    """Run a project's Hardhat/Mocha tests against an in-process EVM."""
    import subprocess
    import threading

    files = _layout(req.files)
    if not any(p.startswith("test/") for p in files):
        raise HTTPException(status_code=400, detail="no test files — add test/<Name>.test.js")

    who = _who(req.address)
    lock = _test_locks.setdefault(who, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a test run is already in flight")
    try:
        root = _sandbox(who)
        for path, content in files.items():
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content or "")

        report = root / "mocha.json"
        report.unlink(missing_ok=True)
        cmd = [os.path.join(_module_dir("chain"), "node_modules", ".bin", "hardhat"), "test"]
        if req.grep:
            cmd += ["--grep", req.grep]
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                                  timeout=TEST_TIMEOUT,
                                  env={**os.environ, "HARDHAT_DISABLE_TELEMETRY_PROMPT": "true"})
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504,
                                detail=f"tests timed out after {TEST_TIMEOUT}s")
    finally:
        lock.release()

    # Hardhat greets every run with a Node-version warning; it isn't the user's news.
    noise = re.compile(r"^WARNING: You are currently using Node\.js")
    output = "\n".join(
        line for chunk in (proc.stdout, proc.stderr) for line in chunk.splitlines()
        if line.strip() and not noise.match(line)
    )
    try:
        result = json.loads(report.read_text())
    except Exception:
        # No report means we never reached mocha — a compile error, almost always.
        return {"ok": False, "passing": 0, "failing": 0, "tests": [],
                "output": output, "error": output or "test run failed"}

    stats = result.get("stats", {})
    tests = []
    for t in result.get("tests", []):
        full, title = t.get("fullTitle", ""), t.get("title", "")
        err = t.get("err") or {}
        tests.append({
            "title": title,
            "suite": full[: len(full) - len(title)].strip(),
            "duration": t.get("duration"),
            "passed": not err,
            "error": err.get("message"),
            "diff": err.get("stack"),
        })

    return {
        "ok": proc.returncode == 0 and not stats.get("failures"),
        "passing": stats.get("passes", 0),
        "failing": stats.get("failures", 0),
        "duration": stats.get("duration", 0),
        "tests": tests,
        "output": output,
    }


class BuiltReq(BaseModel):
    address: Optional[str] = None      # deployer (wallet that signed)
    network: str = "testnet"
    name: str
    contract_address: str
    tx_hash: Optional[str] = None
    abi: list = []
    source: Optional[str] = None


@app.post("/build/deployments")
async def build_record(req: BuiltReq):
    """Record a wallet-signed deployment so it shows up in CONTRACTS / INTERACT."""
    import time
    store = _build_store("deployments.json")
    rows = store.setdefault(_who(req.address), [])

    abi_cid = src_cid = None
    lf = _localfs()
    if lf:  # pin ABI + source so a build is shareable by CID, like fleet contracts
        try:
            abi_cid = lf.put(req.abi) if req.abi else None
            src_cid = lf.put(req.source) if req.source else None
        except Exception:
            pass

    row = {"name": req.name, "network": req.network, "address": req.contract_address,
           "tx_hash": req.tx_hash, "abi": req.abi, "abi_cid": abi_cid,
           "src_cid": src_cid, "deployer": req.address, "created": time.time()}
    rows.insert(0, row)
    _build_save("deployments.json", store)
    return {"ok": True, "deployment": row}


@app.get("/build/deployments")
async def build_deployments(address: Optional[str] = None, network: Optional[str] = None):
    """A user's wallet-signed deployments, newest first."""
    rows = _build_store("deployments.json").get(_who(address), [])
    if network:
        rows = [r for r in rows if r.get("network") == network]
    return {"deployments": rows, "count": len(rows)}


# ── Host readout (owner-only) ────────────────────────────────────────────────
#
# CPU / memory / disk / process / network stats describe the machine the
# protocol runs on: raw cmdlines can carry secrets and traffic shape is
# operational intel, so this is the one part of the API that is not public.
# Access is proven by a wallet signature from an owner address, exchanged for
# a short-lived HMAC token. Owners and the signing secret live off-tree in
# ~/.mod/chain/ — never in the committed config.

CHAIN_HOME = Path.home() / ".mod" / "chain"
OWNERS_PATH = CHAIN_HOME / "owners.json"
SECRET_PATH = CHAIN_HOME / "server.secret"
TOKEN_TTL = 12 * 3600   # one signature buys half a day of access
NONCE_TTL = 300         # a challenge must be signed within five minutes

_nonces = {}            # nonce -> (address, expires_at)
_secret = None


def _owner_addresses() -> set:
    """Addresses allowed to see the host.

    ~/.mod/chain/owners.json is the ACL. On a fresh install it is seeded from
    the deployer addresses in config.json — whoever deployed the protocol owns
    the box it runs on — and can then be edited by hand. $CHAIN_OWNERS (comma
    separated) overrides the file entirely.
    """
    env = os.environ.get("CHAIN_OWNERS", "").strip()
    if env:
        return {a.strip().lower() for a in env.split(",") if a.strip()}
    try:
        owners = {str(a).lower() for a in json.loads(OWNERS_PATH.read_text()).get("owners", []) if a}
        if owners:
            return owners
    except Exception:
        pass
    seeded = set()
    base = _module_dir("chain")
    try:
        with open(os.path.join(base, "config.json")) as f:
            for deployment in (json.load(f).get("deployments") or {}).values():
                # Local chains deploy from a well-known test key whose private
                # key ships with hardhat — never an owner.
                if str(deployment.get("chainId")) in ("1337", "31337"):
                    continue
                if deployment.get("deployer"):
                    seeded.add(deployment["deployer"].lower())
    except Exception:
        pass
    try:
        CHAIN_HOME.mkdir(parents=True, exist_ok=True)
        OWNERS_PATH.write_text(json.dumps({"owners": sorted(seeded)}, indent=2))
        os.chmod(OWNERS_PATH, 0o600)
    except Exception:
        pass
    return seeded


def _server_secret() -> bytes:
    """HMAC key for host-access tokens, persisted 0600 so a restart doesn't
    invalidate every signed-in owner."""
    global _secret
    if _secret is None:
        try:
            _secret = SECRET_PATH.read_bytes()
        except Exception:
            import secrets as _s
            _secret = _s.token_bytes(32)
            try:
                CHAIN_HOME.mkdir(parents=True, exist_ok=True)
                SECRET_PATH.write_bytes(_secret)
                os.chmod(SECRET_PATH, 0o600)
            except Exception:
                pass
    return _secret


def _sign_token(address: str, expires: int) -> str:
    import hmac, hashlib
    body = f"{address}.{expires}"
    mac = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _token_address(authorization: Optional[str]) -> Optional[str]:
    """The owner address a Bearer token proves, or None if it proves nothing."""
    import hmac, hashlib, time as _t
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    address, expires, mac = parts
    try:
        if int(expires) < _t.time():
            return None
    except ValueError:
        return None
    body = f"{address}.{expires}"
    want = hmac.new(_server_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, mac):
        return None
    return address if address in _owner_addresses() else None


def _require_owner(authorization: Optional[str]) -> str:
    address = _token_address(authorization)
    if not address:
        raise HTTPException(status_code=403, detail="owner only")
    return address


def _host_module():
    """The /proc reader that lives next to this file."""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import host
    return host


@app.get("/system/access")
async def system_access(address: Optional[str] = None,
                        authorization: Optional[str] = Header(None)):
    """Whether an address may see the host readout, and whether this session
    already can. The console hides the panel outright for everyone else."""
    authed = _token_address(authorization)
    addr = (address or "").strip().lower() or (authed or "")
    return {
        "address": addr or None,
        "is_owner": bool(addr) and addr in _owner_addresses(),
        "authed": authed is not None,
    }


@app.get("/system/challenge")
async def system_challenge(address: str):
    """A single-use message for an owner wallet to sign."""
    import secrets as _s, time as _t
    addr = (address or "").strip().lower()
    if not addr or addr not in _owner_addresses():
        raise HTTPException(status_code=403, detail="owner only")
    now = _t.time()
    for stale in [n for n, (_, exp) in _nonces.items() if exp < now]:
        _nonces.pop(stale, None)
    nonce = _s.token_hex(16)
    _nonces[nonce] = (addr, now + NONCE_TTL)
    return {
        "nonce": nonce,
        "message": ("chain — owner console\n\n"
                    "Sign in to view this host's stats.\n"
                    f"address: {addr}\n"
                    f"nonce: {nonce}"),
        "expires": int(now + NONCE_TTL),
    }


class HostLoginReq(BaseModel):
    address: str
    signature: str
    nonce: str


@app.post("/system/login")
async def system_login(req: HostLoginReq):
    """Trade a signed challenge for a host-access token."""
    import time as _t
    addr = (req.address or "").strip().lower()
    entry = _nonces.pop(req.nonce, None)   # single use, win or lose
    if not entry or entry[1] < _t.time() or entry[0] != addr:
        raise HTTPException(status_code=400, detail="challenge expired — request a new one")
    message = ("chain — owner console\n\n"
               "Sign in to view this host's stats.\n"
               f"address: {addr}\n"
               f"nonce: {req.nonce}")
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        signer = Account.recover_message(encode_defunct(text=message), signature=req.signature)
    except Exception:
        raise HTTPException(status_code=400, detail="bad signature")
    if signer.lower() != addr or addr not in _owner_addresses():
        raise HTTPException(status_code=403, detail="owner only")
    expires = int(_t.time()) + TOKEN_TTL
    return {"token": _sign_token(addr, expires), "address": addr, "expires": expires}


@app.get("/system/stats")
async def system_stats(top: int = 30, authorization: Optional[str] = Header(None)):
    """Live host readout: per-core CPU, memory, disk, processes and per-
    interface network traffic. Owner-only."""
    _require_owner(authorization)
    return await _host_module().collect(top=max(1, min(top, 100)))


@app.get("/info")
async def info():
    """API info."""
    return {
        "name": "chain-hub",
        "mods": MOD_NAMES,
        "ports": PORTS,
        "endpoints": [
            "health", "mods", "status", "deploy",
            "contracts", "config", "call", "block",
            "timestamp", "info",
            "wallet", "balances", "stake", "unstake", "stakes",
            "credit", "transfer", "tokens",
            "contracts/source", "contracts/mods", "contracts/abis", "cid/{cid}",
            "registry/mods", "registry/all", "registry/register", "bloctime/owner",
            "mods/stakes", "mods/stakes/{name}", "mods/stake", "mods/unstake",
            "register", "mint", "pool", "pool/claimable", "pool/claim",
            "pool/epochs", "pool/snapshot",
            "admin/owners", "admin/encode", "admin/send", "admin/transfer-all",
            "control/status", "control/verify", "control/deploy-script",
            "build/compile", "build/templates", "build/test",
            "build/projects", "build/projects/{name}", "build/deployments",
            "build/shared", "build/shared/{id}",
            "system/access", "system/challenge", "system/login", "system/stats",
        ],
    }
