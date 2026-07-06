"""Chain Hub API — FastAPI backend for chain deployment and module orchestration."""

import json
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
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
    mod: str
    method: str
    args: Optional[list] = None
    network: str = "testnet"

@app.post("/call")
async def mod_call(req: ModCallReq):
    """Call a method on a contract module."""
    chain = get_chain(req.network)
    try:
        mod_instance = chain.mod(req.mod)
        method = getattr(mod_instance, req.method, None)
        if not method:
            raise HTTPException(status_code=404, detail=f"Method {req.method} not found on {req.mod}")
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
            "register", "mint", "pool", "pool/claimable", "pool/claim",
            "pool/epochs", "pool/snapshot",
            "admin/owners", "admin/encode", "admin/send", "admin/transfer-all",
            "control/status", "control/verify", "control/deploy-script",
        ],
    }
