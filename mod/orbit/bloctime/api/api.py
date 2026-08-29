"""
BlocTime API — FastAPI backend for BlocTime staking contract on Base Sepolia.
Served via mod.serve() as part of the bloctime orbit module.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from web3 import Web3

sys.path.insert(0, str(Path(__file__).parent))
import bt_registry
import bt_contracts

app = FastAPI(title="BlocTime API", description="Time-weighted staking")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ───────────────────────────────────────────────────────────────

MODULE_DIR = Path(__file__).parent.parent
DEPLOY_PATH = MODULE_DIR / "deployment.json"
CONFIG_PATH = MODULE_DIR / "config.json"
ABI_PATH = MODULE_DIR / "artifacts" / "contracts" / "BlocTime.sol" / "BlocTime.json"
TOKEN_ABI_PATH = MODULE_DIR / "artifacts" / "contracts" / "NativeToken.sol" / "NativeToken.json"


def _load_deploy_info():
    """Load contract addresses from deployment.json or config.json."""
    if DEPLOY_PATH.exists():
        with open(DEPLOY_PATH) as f:
            deploy = json.load(f)
        return deploy.get("blocTime") or deploy.get("address"), deploy.get("nativeToken"), deploy

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        network = cfg.get("network", "testnet")
        contracts = cfg.get("contracts", {}).get(network, {})
        if contracts.get("bloctime"):
            return contracts["bloctime"], contracts.get("nativeToken"), cfg
    return None, None, {}


def load_contract():
    """Load BlocTime contract from deployment info and ABI."""
    bt_addr, ntv_addr, deploy = _load_deploy_info()
    if not bt_addr:
        return None, None, None, None

    rpc = os.environ.get("BASE_TESTNET_RPC_URL", "https://sepolia.base.org")
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        network = cfg.get("network", "testnet")
        rpc_from_cfg = cfg.get("contracts", {}).get(network, {}).get("url")
        if rpc_from_cfg:
            rpc = rpc_from_cfg

    w3 = Web3(Web3.HTTPProvider(rpc))

    if ABI_PATH.exists():
        with open(ABI_PATH) as f:
            artifact = json.load(f)
        abi = artifact["abi"]
    else:
        raise RuntimeError("ABI not found. Run 'npx hardhat compile' first.")

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(bt_addr),
        abi=abi,
    )

    token = None
    if ntv_addr:
        if TOKEN_ABI_PATH.exists():
            with open(TOKEN_ABI_PATH) as f:
                token_artifact = json.load(f)
            token_abi = token_artifact["abi"]
        else:
            token_abi = json.loads('[{"inputs":[{"internalType":"address","name":"spender","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"approve","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')
        token = w3.eth.contract(
            address=Web3.to_checksum_address(ntv_addr),
            abi=token_abi,
        )

    pk = os.environ.get("PRIVATE_KEY")
    account = w3.eth.account.from_key(pk) if pk else None

    return w3, contract, account, token


def _send_tx(w3, account, tx_fn):
    """Build, sign, send a transaction."""
    tx = tx_fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 500000,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return {
        "success": receipt.status == 1,
        "tx_hash": tx_hash.hex(),
    }


ZERO_ADDR = "0x0000000000000000000000000000000000000000"


# ── Request models ───────────────────────────────────────────────────────

class StakeReq(BaseModel):
    amount: str
    lock_blocks: int
    as_ether: bool = True

class UnstakeReq(BaseModel):
    stake_id: int

class AddressReq(BaseModel):
    address: str

class PositionReq(BaseModel):
    address: str
    stake_id: int

class DelegateReq(BaseModel):
    delegate_to: str

class SetInflationReq(BaseModel):
    initial_reward: str      # ether amount
    halving_interval: int    # epochs
    min_reward: str = "0"    # ether amount
    epoch_length: int = 43200  # blocks per epoch

class FundPotReq(BaseModel):
    amount: str
    as_ether: bool = True

class AbiCallReq(BaseModel):
    contract: str            # "bloctime" | "nativeToken"
    fn: str
    args: list = []
    value: str = "0"         # wei, payable functions only


def _call(fn, default=None):
    """Call a contract view, degrading to `default` where the deployed
    (older) contract lacks the function and reverts."""
    try:
        return fn.call()
    except Exception:
        return default


SCHEDULE = "Weekly, Friday 12:00 EST (17:00 UTC)"


def _pot_info(contract):
    """Reward pot + weekly schedule. None where the deployed contract
    predates the pot (it distributed per-epoch instead)."""
    info = _call(contract.functions.getPotInfo())
    if info is None:
        return None
    pot, pending, eligible, next_time, last_time, due = info
    return {
        "pot": str(pot),
        "pendingInflation": str(pending),
        "projected": str(pot + pending),
        "eligibleSupply": str(eligible),
        "nextDistribution": next_time,
        "lastDistribution": last_time,
        "due": due,
        "secondsRemaining": 0 if due else max(0, next_time - int(time.time())),
        "schedule": SCHEDULE,
    }


# ── Core Endpoints ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "module": "bloctime"}


@app.post("/overview")
async def overview(req: Optional[AddressReq] = None):
    w3, contract, account, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    addr = req.address if req else (account.address if account else None)
    if not addr:
        raise HTTPException(status_code=400, detail="No address provided")
    addr = Web3.to_checksum_address(addr)

    stake_ids = contract.functions.getUserStakeIds(addr).call()
    positions = []
    total_staked = 0
    total_bloctime = 0

    for sid in stake_ids:
        pos = contract.functions.getStakePosition(addr, sid).call()
        amount, start_block, lock_blocks, bt_balance, remaining = pos
        positions.append({
            "stakeId": sid,
            "amount": str(amount),
            "startBlock": start_block,
            "lockBlocks": lock_blocks,
            "blocTimeBalance": str(bt_balance),
            "blocksRemaining": remaining,
        })
        total_staked += amount
        total_bloctime += bt_balance

    delegate_addr = _call(contract.functions.delegates(addr), ZERO_ADDR)
    pending_rewards = _call(contract.functions.earned(addr), 0)
    voting_power = _call(contract.functions.getVotingPower(addr), 0)

    return {"result": {
        "address": addr,
        "stakeCount": len(positions),
        "totalStaked": str(total_staked),
        "totalBlocTime": str(total_bloctime),
        "delegate": delegate_addr if delegate_addr != ZERO_ADDR else "",
        "pendingRewards": str(pending_rewards),
        "votingPower": str(voting_power),
        "blocBalance": str(_call(contract.functions.balanceOf(addr), 0)),
        "positions": positions,
    }}


@app.post("/stake")
async def stake(req: StakeReq):
    w3, contract, account, token = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Contract not deployed or no signer")

    try:
        amount_wei = Web3.to_wei(req.amount, 'ether') if req.as_ether else int(req.amount)

        if token:
            approve_tx = token.functions.approve(
                contract.address, amount_wei
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 100000,
            })
            signed = account.sign_transaction(approve_tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        result = _send_tx(w3, account, contract.functions.stake(amount_wei, req.lock_blocks))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/unstake")
async def unstake(req: UnstakeReq):
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Contract not deployed or no signer")

    try:
        result = _send_tx(w3, account, contract.functions.unstake(req.stake_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_position")
async def get_position(req: PositionReq):
    w3, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    addr = Web3.to_checksum_address(req.address)
    pos = contract.functions.getStakePosition(addr, req.stake_id).call()
    amount, start_block, lock_blocks, bt_balance, remaining = pos

    return {"result": {
        "stakeId": req.stake_id,
        "amount": str(amount),
        "startBlock": start_block,
        "lockBlocks": lock_blocks,
        "blocTimeBalance": str(bt_balance),
        "blocksRemaining": remaining,
    }}


@app.post("/get_multiplier")
async def get_multiplier(req: dict):
    w3, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    block_count = int(req.get("block_count", 0))
    multiplier = contract.functions.getMultiplier(block_count).call()
    return {"result": {
        "blockCount": block_count,
        "multiplier": multiplier,
        "multiplierX": multiplier / 10000,
    }}


@app.get("/points")
async def get_points():
    w3, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    try:
        pts = contract.functions.getPoints().call()
    except Exception:
        # Deployed contract predates getPoints — no curve to show.
        pts = []
    return {"result": [{"blocks": p[0], "multiplier": p[1], "multiplierX": p[1] / 10000} for p in pts]}


@app.get("/params")
async def get_params():
    w3, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    p = contract.functions.params().call()
    return {"result": {
        "maxLockBlocks": p[0],
        "distributionPercentage": p[1],
    }}


@app.get("/stats")
async def stats():
    w3, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")

    total_bt = contract.functions.totalBlocTime().call()
    next_id = contract.functions.nextStakeId().call()
    supply = contract.functions.totalSupply().call()

    # The deployed testnet contract may predate the inflation/epoch
    # functions — degrade to core staking stats instead of failing.
    try:
        epoch = contract.functions.currentEpoch().call()
        epoch_reward = contract.functions.getEpochReward(epoch).call() if epoch > 0 else 0
        total_distributed = contract.functions.totalDistributed().call()
        last_dist = contract.functions.lastDistributionEpoch().call()
        infl = contract.functions.getInflationParams().call()
    except Exception:
        epoch, epoch_reward, total_distributed, last_dist, infl = 0, 0, 0, 0, None

    bt_addr, ntv_addr, deploy = _load_deploy_info()
    pot = _pot_info(contract)

    # The lock cap travels with the stats poll: the stake form needs it on
    # every render to keep its presets inside what the contract will accept.
    try:
        prm = contract.functions.params().call()
        max_lock, dist_pct = prm[0], prm[1]
    except Exception:
        max_lock, dist_pct = 0, 0

    return {"result": {
        "pot": pot,
        "maxLockBlocks": max_lock,
        "distributionPercentage": dist_pct,
        "totalBlocTime": str(total_bt),
        "totalSupply": str(supply),
        "totalStakes": next_id,
        "address": bt_addr or "",
        "nativeToken": ntv_addr or "",
        "network": deploy.get("network", ""),
        "explorer": f"https://sepolia.basescan.org/address/{bt_addr or ''}",
        "currentEpoch": epoch,
        "epochReward": str(epoch_reward),
        "totalDistributed": str(total_distributed),
        "lastDistributionEpoch": last_dist,
        "inflationParams": {
            "initialRewardPerEpoch": str(infl[0]),
            "halvingInterval": infl[1],
            "minRewardPerEpoch": str(infl[2]),
            "epochLength": infl[3],
            "startBlock": infl[4],
        } if infl else None,
    }}


# ── Delegation & Rewards Endpoints ──────────────────────────────────────

@app.post("/get_voting_power")
async def get_voting_power(req: AddressReq):
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    addr = Web3.to_checksum_address(req.address)
    vp = _call(contract.functions.getVotingPower(addr), 0)
    delegate_addr = _call(contract.functions.delegates(addr), ZERO_ADDR)
    balance = contract.functions.balanceOf(addr).call()
    delegated = _call(contract.functions.delegatedVotingPower(addr), 0)
    return {"result": {
        "votingPower": str(vp),
        "delegate": delegate_addr if delegate_addr != ZERO_ADDR else "",
        "ownBalance": str(balance),
        "delegatedReceived": str(delegated),
    }}


@app.post("/get_rewards")
async def get_rewards(req: AddressReq):
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    addr = Web3.to_checksum_address(req.address)
    pending = _call(contract.functions.earned(addr), 0)
    epoch = _call(contract.functions.currentEpoch(), 0)
    epoch_reward = _call(contract.functions.getEpochReward(epoch), 0) if epoch > 0 else 0
    total_distributed = _call(contract.functions.totalDistributed(), 0)
    return {"result": {
        "pendingRewards": str(pending),
        "currentEpoch": epoch,
        "epochReward": str(epoch_reward),
        "totalDistributed": str(total_distributed),
    }}


@app.get("/get_inflation_params")
async def get_inflation_params():
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    infl = _call(contract.functions.getInflationParams(), (0, 0, 0, 0, 0))
    epoch = _call(contract.functions.currentEpoch(), 0)
    epoch_reward = _call(contract.functions.getEpochReward(epoch), 0) if epoch > 0 else 0
    total_distributed = _call(contract.functions.totalDistributed(), 0)
    last_dist = _call(contract.functions.lastDistributionEpoch(), 0)
    return {"result": {
        "initialRewardPerEpoch": str(infl[0]),
        "halvingInterval": infl[1],
        "minRewardPerEpoch": str(infl[2]),
        "epochLength": infl[3],
        "startBlock": infl[4],
        "currentEpoch": epoch,
        "epochReward": str(epoch_reward),
        "totalDistributed": str(total_distributed),
        "lastDistributionEpoch": last_dist,
    }}


@app.get("/get_inflation_curve")
async def get_inflation_curve():
    """Return sampled inflation rewards over epochs for chart display."""
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    infl = _call(contract.functions.getInflationParams(), (0, 0, 0, 0, 0))
    halving_interval = infl[1]
    if halving_interval == 0:
        return {"result": {"points": [], "halvingInterval": 0, "totalEpochs": 0}}
    total_epochs = halving_interval * 5
    steps = min(100, total_epochs)
    points = []
    for i in range(steps + 1):
        ep = int(total_epochs * i / steps)
        reward = contract.functions.getEpochReward(ep).call()
        points.append({"epoch": ep, "reward": str(reward)})
    return {"result": {
        "halvingInterval": halving_interval,
        "totalEpochs": total_epochs,
        "points": points,
    }}


# ── Delegation / Rewards Write Endpoints ─────────────────────────────────

@app.post("/delegate")
async def delegate(req: DelegateReq):
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        addr = Web3.to_checksum_address(req.delegate_to)
        result = _send_tx(w3, account, contract.functions.delegate(addr))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/undelegate")
async def undelegate():
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        result = _send_tx(w3, account, contract.functions.undelegate())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/claim_rewards")
async def claim_rewards():
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        result = _send_tx(w3, account, contract.functions.claimRewards())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/pot")
async def pot():
    """Reward pot waiting on the next weekly distribution."""
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    info = _pot_info(contract)
    if info is None:
        raise HTTPException(status_code=501, detail="Deployed contract predates the weekly pot")
    return {"result": info}


@app.post("/fund_pot")
async def fund_pot(req: FundPotReq):
    """Add BLOC to the pot — paid out at the next Friday 12:00 EST."""
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        amount = Web3.to_wei(req.amount, "ether") if req.as_ether else int(req.amount)
        result = _send_tx(w3, account, contract.functions.fundPot(amount))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/distribute_rewards")
async def distribute_rewards():
    """Sweep the pot to BLOC holders. Only opens Friday 12:00 EST, weekly."""
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    info = _pot_info(contract)
    if info and not info["due"]:
        raise HTTPException(
            status_code=409,
            detail=f"Next distribution at {info['nextDistribution']} "
                   f"({info['secondsRemaining']}s away) — {SCHEDULE}",
        )
    try:
        result = _send_tx(w3, account, contract.functions.distributeRewards())
        return {"result": {**result, "pot": info}}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Contract Playground ──────────────────────────────────────────────────

CONTRACT_DEFS = {
    "bloctime": {
        "label": "BlocTime",
        "abi": ABI_PATH,
        "sol": MODULE_DIR / "contracts" / "BlocTime.sol",
        "cfg_key": "bloctime",
    },
    "nativeToken": {
        "label": "NativeToken",
        "abi": TOKEN_ABI_PATH,
        "sol": MODULE_DIR / "contracts" / "NativeToken.sol",
        "cfg_key": "nativeToken",
    },
}


def _network_cfg():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        network = cfg.get("network", "testnet")
        return network, cfg.get("contracts", {}).get(network, {})
    return "testnet", {}


def _load_abi(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)["abi"]
    return []


def _load_any_contract(contract_id):
    """Load (w3, contract, account, abi) for any registered contract."""
    d = CONTRACT_DEFS.get(contract_id)
    if not d:
        # Contracts you deployed yourself are addressed as "deployed:<id>".
        entry = bt_contracts.get_deployment(str(contract_id).removeprefix("deployed:"))
        if not entry:
            raise HTTPException(status_code=404, detail=f"Unknown contract '{contract_id}'")
        rpc = entry["rpc"] or os.environ.get("BASE_TESTNET_RPC_URL", "https://sepolia.base.org")
        w3 = Web3(Web3.HTTPProvider(rpc))
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(entry["address"]), abi=entry["abi"],
        )
        pk = os.environ.get("PRIVATE_KEY")
        account = w3.eth.account.from_key(pk) if pk else None
        return w3, contract, account, entry["abi"]
    network, cfg = _network_cfg()
    addr = cfg.get(d["cfg_key"])
    if not addr:
        raise HTTPException(status_code=500, detail=f"{d['label']} not deployed on {network}")
    rpc = cfg.get("url") or os.environ.get("BASE_TESTNET_RPC_URL", "https://sepolia.base.org")
    w3 = Web3(Web3.HTTPProvider(rpc))
    abi = _load_abi(d["abi"])
    if not abi:
        raise HTTPException(status_code=500, detail="ABI not found. Run 'npx hardhat compile' first.")
    contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi)
    pk = os.environ.get("PRIVATE_KEY")
    account = w3.eth.account.from_key(pk) if pk else None
    return w3, contract, account, abi


def _find_fn(abi, name, n_args):
    matches = [e for e in abi if e.get("type") == "function" and e.get("name") == name]
    if not matches:
        raise HTTPException(status_code=404, detail=f"Function '{name}' not in ABI")
    exact = [e for e in matches if len(e.get("inputs", [])) == n_args]
    if not exact:
        raise HTTPException(
            status_code=400,
            detail=f"'{name}' expects {len(matches[0].get('inputs', []))} args, got {n_args}",
        )
    return exact[0]


def _coerce_arg(value, abi_type):
    """Coerce a JSON/string value into what web3.py expects for abi_type."""
    if abi_type.endswith("]"):
        base = abi_type[: abi_type.rfind("[")]
        if isinstance(value, str):
            value = json.loads(value)
        return [_coerce_arg(v, base) for v in value]
    if abi_type == "address":
        return Web3.to_checksum_address(value)
    if abi_type.startswith(("uint", "int")):
        return int(str(value).strip(), 0)  # base 0: accepts "123" and "0x7b"
    if abi_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes")
    if abi_type.startswith("bytes"):
        s = str(value)
        return Web3.to_bytes(hexstr=s) if s.startswith("0x") else Web3.to_bytes(text=s)
    if abi_type == "string":
        return str(value)
    return value


def _json_safe(v):
    """Contract return values → JSON (big ints as strings, bytes as hex)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "0x" + v.hex()
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    return v


@app.get("/contracts")
async def contracts():
    """All deployed contracts with address, ABI, and Solidity source."""
    network, cfg = _network_cfg()
    out = []
    for cid, d in CONTRACT_DEFS.items():
        addr = cfg.get(d["cfg_key"], "")
        out.append({
            "id": cid,
            "name": d["label"],
            "address": addr,
            "chainId": str(cfg.get("chainId", "")),
            "rpc": cfg.get("url", ""),
            "explorer": f"https://sepolia.basescan.org/address/{addr}" if addr else "",
            "abi": _load_abi(d["abi"]),
            "source": d["sol"].read_text() if d["sol"].exists() else "",
        })
    for e in bt_contracts.list_deployments():
        out.append({
            "id": f"deployed:{e['id']}",
            "name": e["name"],
            "address": e["address"],
            "chainId": e["chainId"],
            "rpc": e["rpc"],
            "explorer": e["explorer"],
            "abi": e["abi"],
            "source": e["source"],
            "deployed": True,
            "deployer": e["deployer"],
            "createdAt": e["createdAt"],
        })
    return {"result": {
        "network": network,
        "chainId": cfg.get("chainId", ""),
        "rpc": cfg.get("url", ""),
        "signer": (lambda pk: Web3().eth.account.from_key(pk).address if pk else "")(os.environ.get("PRIVATE_KEY")),
        "contracts": out,
    }}


@app.post("/contract/read")
async def contract_read(req: AbiCallReq):
    """Call any view/pure function on a registered contract."""
    w3, contract, _, abi = _load_any_contract(req.contract)
    entry = _find_fn(abi, req.fn, len(req.args))
    if entry.get("stateMutability") not in ("view", "pure"):
        raise HTTPException(status_code=400, detail=f"'{req.fn}' is a write function — use /contract/write")
    try:
        args = [_coerce_arg(a, i["type"]) for a, i in zip(req.args, entry.get("inputs", []))]
        raw = getattr(contract.functions, req.fn)(*args).call()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"result": {
        "fn": req.fn,
        "output": _json_safe(raw),
        "outputs": [{"name": o.get("name", ""), "type": o["type"]} for o in entry.get("outputs", [])],
    }}


@app.post("/contract/write")
async def contract_write(req: AbiCallReq):
    """Send a state-changing transaction via the server signer (PRIVATE_KEY)."""
    w3, contract, account, abi = _load_any_contract(req.contract)
    if not account:
        raise HTTPException(status_code=500, detail="No server signer (PRIVATE_KEY not set) — connect a wallet instead")
    entry = _find_fn(abi, req.fn, len(req.args))
    if entry.get("stateMutability") in ("view", "pure"):
        raise HTTPException(status_code=400, detail=f"'{req.fn}' is read-only — use /contract/read")
    try:
        args = [_coerce_arg(a, i["type"]) for a, i in zip(req.args, entry.get("inputs", []))]
        tx_params = {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 500000,
        }
        if entry.get("stateMutability") == "payable" and int(req.value or "0") > 0:
            tx_params["value"] = int(req.value)
        tx = getattr(contract.functions, req.fn)(*args).build_transaction(tx_params)
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return {"result": {
            "success": receipt.status == 1,
            "tx_hash": tx_hash.hex(),
            "gas_used": receipt.gasUsed,
            "block": receipt.blockNumber,
            "explorer": f"https://sepolia.basescan.org/tx/0x{tx_hash.hex().removeprefix('0x')}",
        }}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Factory: deploy-your-own kit ─────────────────────────────────────────

# Mirrors scripts/deploy.js so wallet deploys match the canonical recipe.
#
# Blocks are 2s on Base, so a day is 43,200 blocks — the same unit the
# inflation epoch uses. A year is 365 days (1,460 epochs is the halving
# interval, i.e. 4 years), and the lock is capped at 8 of them.
BLOCKS_PER_DAY = 43200
BLOCKS_PER_YEAR = BLOCKS_PER_DAY * 365          # 15,768,000
MAX_LOCK_BLOCKS = BLOCKS_PER_YEAR * 8           # 126,144,000 — the 8-year cap

# The curve is a straight line: 1x at no lock, 8x at the 8-year cap, +8750 bps
# per year in between. The points below are year marks ON that line, not bends
# in it — they exist so the UI has readable presets, and getMultiplier()
# interpolates the same value it would with a single 0 → 8y segment.
LINEAR_POINTS = [
    {"blocks": 0, "multiplier": 10000},                     # 1.000x
    {"blocks": BLOCKS_PER_YEAR, "multiplier": 18750},       # 1y — 1.875x
    {"blocks": BLOCKS_PER_YEAR * 2, "multiplier": 27500},   # 2y — 2.750x
    {"blocks": BLOCKS_PER_YEAR * 4, "multiplier": 45000},   # 4y — 4.500x
    {"blocks": MAX_LOCK_BLOCKS, "multiplier": 80000},       # 8y — 8.000x
]

DEPLOY_DEFAULTS = {
    "initialSupply": "1000000",
    "maxLockBlocks": MAX_LOCK_BLOCKS,
    "distributionPercentage": 5000,
    "points": LINEAR_POINTS,
    "inflation": {
        "initialRewardPerEpoch": "50",
        "halvingInterval": 1460,
        "minRewardPerEpoch": "0",
        "epochLength": 43200,
    },
}


@app.get("/factory")
async def factory():
    """ABI + bytecode + default params so a browser wallet can deploy its own BlocTime."""
    out = {}
    for key, path in (("bloctime", ABI_PATH), ("nativeToken", TOKEN_ABI_PATH)):
        if not path.exists():
            raise HTTPException(status_code=500, detail="Artifacts missing. Run 'npx hardhat compile' first.")
        with open(path) as f:
            artifact = json.load(f)
        out[key] = {"abi": artifact["abi"], "bytecode": artifact["bytecode"]}
    return {"result": {
        "contracts": out,
        "defaults": DEPLOY_DEFAULTS,
        "fork": "m bloctime/fork name=<yourname>   # copies the whole module into orbit/<yourname>",
    }}


# ── Deploy anything: compile Solidity and put it on chain ────────────────

STARTER_SOL = '''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Greeter {
    string public greeting;

    constructor(string memory _greeting) {
        greeting = _greeting;
    }

    function setGreeting(string calldata _greeting) external {
        greeting = _greeting;
    }
}
'''


class CompileReq(BaseModel):
    source: str
    filename: str = "Contract.sol"
    optimize: bool = True
    runs: int = 200

class RecordDeployReq(BaseModel):
    name: str
    address: str
    abi: list
    rpc: str = ""
    chainId: str = ""
    deployer: str = ""
    txHash: str = ""
    source: str = ""
    filename: str = ""
    solc: str = ""

class ForgetDeployReq(BaseModel):
    id: str
    address: str = ""
    signature: str = ""

class DeployContractReq(BaseModel):
    source: str = ""
    contract: str = ""       # which contract in the source (default: the only/last one)
    abi: list = []           # or skip compiling and deploy a prebuilt artifact
    bytecode: str = ""
    args: list = []
    rpc: str = ""
    filename: str = "Contract.sol"
    name: str = ""           # label in the store (default: contract name)
    record: bool = True


@app.get("/compile/starter")
async def compile_starter():
    """A tiny contract to start from, plus what the compiler here can do."""
    return {"result": {
        "source": STARTER_SOL,
        "filename": "Greeter.sol",
        "imports": "@openzeppelin/contracts/** resolves from this module's node_modules",
    }}


@app.post("/compile")
async def compile_contract(req: CompileReq):
    """Compile Solidity → ABI + bytecode for every contract in the file."""
    try:
        return {"result": bt_contracts.compile_source(
            req.source, filename=req.filename, optimize=req.optimize, runs=req.runs,
        )}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/deployments")
async def deployments():
    """Contracts deployed through this console (yours and anyone else's on this box)."""
    entries = bt_contracts.list_deployments()
    return {"result": {"count": len(entries), "deployments": entries}}


@app.post("/deployments/record")
async def deployments_record(req: RecordDeployReq):
    """Remember a wallet-deployed contract so it joins the CONTRACTS playground."""
    try:
        entry = bt_contracts.add_deployment(
            name=req.name, address=req.address, abi=req.abi, rpc=req.rpc,
            chain_id=req.chainId, deployer=req.deployer, tx_hash=req.txHash,
            source=req.source, filename=req.filename, solc=req.solc,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"result": entry}


@app.post("/deployments/forget")
async def deployments_forget(req: ForgetDeployReq):
    """Drop a deployment record. Deployer must sign 'bloctime:forget:<id>'."""
    entry = bt_contracts.get_deployment(req.id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown deployment '{req.id}'")
    deployer = entry.get("deployer", "")
    if deployer:
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
            recovered = Account.recover_message(
                encode_defunct(text=f"bloctime:forget:{req.id}"), signature=req.signature,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Bad signature: {e}")
        if recovered.lower() != deployer.lower():
            raise HTTPException(status_code=403, detail="Signature does not match the deployer")
    try:
        return {"result": bt_contracts.remove_deployment(req.id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/deploy")
async def deploy_contract(req: DeployContractReq):
    """Deploy a contract with the server signer (PRIVATE_KEY) — the CLI path.

    The app deploys from your own wallet instead; this is for headless use.
    """
    abi, bytecode, source, solc_version, name = req.abi, req.bytecode, "", "", req.contract
    if req.source:
        try:
            compiled = bt_contracts.compile_source(req.source, filename=req.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        picks = [c for c in compiled["contracts"] if c["deployable"]]
        if req.contract:
            picks = [c for c in picks if c["name"] == req.contract]
        if not picks:
            raise HTTPException(status_code=400, detail=f"No deployable contract '{req.contract}' in {req.filename}")
        chosen = picks[-1]
        abi, bytecode, name = chosen["abi"], chosen["bytecode"], chosen["name"]
        source, solc_version = req.source, compiled["solc"]
    if not abi or not bytecode:
        raise HTTPException(status_code=400, detail="Provide `source`, or `abi` + `bytecode`")

    ctor = next((e for e in abi if e.get("type") == "constructor"), None)
    try:
        args = bt_contracts.coerce_args(ctor.get("inputs", []) if ctor else [], req.args)
        facts = bt_contracts.deploy(abi, bytecode, args=args, rpc=req.rpc or _network_cfg()[1].get("url"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = {**facts, "name": req.name or name or "Contract", "abi": abi}
    if req.record:
        try:
            result["entry"] = bt_contracts.add_deployment(
                name=result["name"], address=facts["address"], abi=abi, rpc=facts["rpc"],
                chain_id=facts["chainId"], deployer=facts["deployer"], tx_hash=facts["txHash"],
                source=source, filename=req.filename if source else "", solc=solc_version,
                verify=False,
            )
        except ValueError as e:
            result["recordError"] = str(e)
    return {"result": result}


# ── Marketplace: registry of deployed BlocTime instances ─────────────────

class RegisterReq(BaseModel):
    name: str
    rpc: str
    bloctime: str
    nativeToken: str = ""
    description: str = ""

class UnregisterReq(BaseModel):
    id: str
    address: str
    signature: str


_stats_cache: dict = {}  # instance id -> (fetched_at, stats)
STATS_TTL = 60


@app.get("/registry")
async def registry_list():
    """All known BlocTime instances (official first) with best-effort live stats."""
    instances = bt_registry.list_instances()
    now = time.time()
    for e in instances:
        cached = _stats_cache.get(e["id"])
        if cached and now - cached[0] < STATS_TTL:
            e["stats"] = cached[1]
        else:
            s = bt_registry.instance_stats(e)
            _stats_cache[e["id"]] = (now, s)
            e["stats"] = s
    return {"result": {"count": len(instances), "instances": instances}}


@app.post("/registry/register")
async def registry_register(req: RegisterReq):
    """Register a deployed BlocTime. Verified on-chain; owner() is recorded."""
    try:
        entry = bt_registry.add_instance(
            name=req.name, rpc=req.rpc, bloctime=req.bloctime,
            native_token=req.nativeToken or None, description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _stats_cache.pop(entry["id"], None)
    return {"result": entry}


@app.post("/registry/unregister")
async def registry_unregister(req: UnregisterReq):
    """Remove an entry. Caller must sign 'bloctime:unregister:<id>' with the instance owner key."""
    entry = bt_registry.get_instance(req.id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown instance '{req.id}'")
    if entry.get("official"):
        raise HTTPException(status_code=403, detail="The official instance cannot be removed")
    owner = entry.get("owner", "")
    if not owner:
        raise HTTPException(status_code=403, detail="Instance has no recorded owner — remove via 'm bloctime/unregister_instance'")
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        message = encode_defunct(text=f"bloctime:unregister:{req.id}")
        recovered = Account.recover_message(message, signature=req.signature)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad signature: {e}")
    if recovered.lower() != owner.lower():
        raise HTTPException(status_code=403, detail="Signature does not match the instance owner")
    try:
        result = bt_registry.remove_instance(req.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _stats_cache.pop(req.id, None)
    return {"result": result}


# ── Bridge: proxy into the bridge module ─────────────────────────────────

BRIDGE_API_URL = os.environ.get("BRIDGE_API_URL", "http://localhost:8840")
BRIDGE_FNS = {
    "health", "status", "owner", "contract_info", "in_snapshot",
    "has_claimed", "unclaimed", "commitment", "claims", "commitments",
    "get_total_balances",
}
# Rust bridge routes these as GET /{fn}/{address}
BRIDGE_PATH_FNS = {"in_snapshot", "has_claimed", "unclaimed", "commitment"}


# Fleet boxes run bridge behind the scale-to-zero activator (:9000) — its
# /api/bridge route wakes the module on access, so fall back to it when the
# direct port is asleep.
BRIDGE_ACTIVATOR_URL = os.environ.get("BRIDGE_ACTIVATOR_URL", "http://localhost:9000/api/bridge")


def _bridge_call(fn: str, params: Optional[dict] = None):
    import requests as req_lib

    def hit(base, timeout):
        if fn in BRIDGE_PATH_FNS:
            addr = (params or {}).get("address", "")
            if not addr:
                raise HTTPException(status_code=400, detail=f"'{fn}' needs an address")
            r = req_lib.get(f"{base}/{fn}/{addr}", timeout=timeout)
        else:
            r = req_lib.post(f"{base}/{fn}", json=params or {}, timeout=timeout)
            if r.status_code in (404, 405):
                r = req_lib.get(f"{base}/{fn}", params=params or {}, timeout=timeout)
        return (r.json() if r.content else {}), r.status_code

    try:
        return hit(BRIDGE_API_URL, 8)
    except Exception:
        pass
    try:
        return hit(BRIDGE_ACTIVATOR_URL, 25)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Bridge module unreachable at {BRIDGE_API_URL}: {e}")


@app.get("/bridge/info")
async def bridge_info():
    """Bridge module health + totals, plus where its app lives."""
    try:
        health, code = _bridge_call("health")
    except HTTPException:
        health, code = {}, 0
    totals = {}
    if code == 200:
        try:
            totals, _ = _bridge_call("status")
        except HTTPException:
            pass
    return {"result": {
        "online": code == 200,
        "health": health,
        "totals": totals,
        "app": "/bridge",
        "api": BRIDGE_API_URL,
    }}


@app.post("/bridge/{fn}")
async def bridge_proxy(fn: str, params: Optional[dict] = None):
    """Whitelisted read-only pass-through to the bridge module's API."""
    if fn not in BRIDGE_FNS:
        raise HTTPException(status_code=403, detail=f"Bridge fn '{fn}' not allowed via this proxy")
    body, code = _bridge_call(fn, params)
    if code >= 400:
        raise HTTPException(status_code=code, detail=body.get("detail") or body.get("error") or "Bridge call failed")
    return {"result": body}


@app.post("/set_inflation_params")
async def set_inflation_params(req: SetInflationReq):
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        initial = Web3.to_wei(float(req.initial_reward), "ether")
        min_r = Web3.to_wei(float(req.min_reward), "ether")
        result = _send_tx(w3, account, contract.functions.setInflationParams(
            initial, req.halving_interval, min_r, req.epoch_length
        ))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
