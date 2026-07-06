"""
BlocTime API — FastAPI backend for BlocTime staking contract on Base Sepolia.
Served via mod.serve() as part of the bloctime orbit module.
"""

import json
import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from web3 import Web3

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

    delegate_addr = contract.functions.delegates(addr).call()
    pending_rewards = contract.functions.earned(addr).call()
    voting_power = contract.functions.getVotingPower(addr).call()

    return {"result": {
        "address": addr,
        "stakeCount": len(positions),
        "totalStaked": str(total_staked),
        "totalBlocTime": str(total_bloctime),
        "delegate": delegate_addr if delegate_addr != ZERO_ADDR else "",
        "pendingRewards": str(pending_rewards),
        "votingPower": str(voting_power),
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

    pts = contract.functions.getPoints().call()
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
    epoch = contract.functions.currentEpoch().call()
    epoch_reward = contract.functions.getEpochReward(epoch).call() if epoch > 0 else 0
    total_distributed = contract.functions.totalDistributed().call()
    last_dist = contract.functions.lastDistributionEpoch().call()

    bt_addr, ntv_addr, deploy = _load_deploy_info()

    infl = contract.functions.getInflationParams().call()

    return {"result": {
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
        },
    }}


# ── Delegation & Rewards Endpoints ──────────────────────────────────────

@app.post("/get_voting_power")
async def get_voting_power(req: AddressReq):
    _, contract, _, _ = load_contract()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    addr = Web3.to_checksum_address(req.address)
    vp = contract.functions.getVotingPower(addr).call()
    delegate_addr = contract.functions.delegates(addr).call()
    balance = contract.functions.balanceOf(addr).call()
    delegated = contract.functions.delegatedVotingPower(addr).call()
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
    pending = contract.functions.earned(addr).call()
    epoch = contract.functions.currentEpoch().call()
    epoch_reward = contract.functions.getEpochReward(epoch).call() if epoch > 0 else 0
    total_distributed = contract.functions.totalDistributed().call()
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
    infl = contract.functions.getInflationParams().call()
    epoch = contract.functions.currentEpoch().call()
    epoch_reward = contract.functions.getEpochReward(epoch).call() if epoch > 0 else 0
    total_distributed = contract.functions.totalDistributed().call()
    last_dist = contract.functions.lastDistributionEpoch().call()
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
    infl = contract.functions.getInflationParams().call()
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


@app.post("/distribute_rewards")
async def distribute_rewards():
    w3, contract, account, _ = load_contract()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not loaded or no signer")
    try:
        result = _send_tx(w3, account, contract.functions.distributeRewards())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
