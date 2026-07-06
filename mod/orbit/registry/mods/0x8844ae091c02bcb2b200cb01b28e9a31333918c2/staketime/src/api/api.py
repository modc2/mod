"""
StakeTime + Incentive API

FastAPI backend proxying calls to StakeTime (staking) and Incentive (emissions)
contracts on Base Sepolia.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from web3 import Web3

app = FastAPI(title="StakeTime API", description="Delegated staking + Yuma consensus emissions")

_cors_origins = os.environ.get("MOD_CORS_ORIGINS", "").split(",") if os.environ.get("MOD_CORS_ORIGINS") else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "token"],
)

# ── Config ───────────────────────────────────────────────────────────────

MODULE_DIR = Path(__file__).parent.parent.parent
DEPLOY_PATH = MODULE_DIR / "config.json"
ST_ABI_PATH = MODULE_DIR / "artifacts" / "src" / "contracts" / "staking" / "Staking.sol" / "Staking.json"
STT_ABI_PATH = MODULE_DIR / "artifacts" / "src" / "contracts" / "staking" / "StakeTime.sol" / "StakeTime.json"
INC_ABI_PATH = MODULE_DIR / "artifacts" / "src" / "contracts" / "consensus" / "yuma" / "ConsensusYuma.sol" / "ConsensusYuma.json"
NTV_ABI_PATH = MODULE_DIR / "artifacts" / "src" / "contracts" / "Mod.sol" / "Mod.json"
REG_ABI_PATH = MODULE_DIR / "artifacts" / "src" / "contracts" / "Registry.sol" / "Registry.json"


def _w3():
    rpc = os.environ.get("BASE_TESTNET_RPC_URL", "https://sepolia.base.org")
    return Web3(Web3.HTTPProvider(rpc))


def _account(w3):
    pk = os.environ.get("PRIVATE_KEY")
    return w3.eth.account.from_key(pk) if pk else None


def _deploy():
    if not DEPLOY_PATH.exists():
        return None
    with open(DEPLOY_PATH) as f:
        data = json.load(f)
    network = os.environ.get("NETWORK", "base_sepolia")
    contracts = data.get("contracts", data)
    if isinstance(contracts, dict) and network in contracts:
        return contracts[network]
    # Fallback: first available network
    for key in contracts:
        if isinstance(contracts[key], dict) and ("staking" in contracts[key] or "staking" in contracts[key]):
            return contracts[key]
    return data


def load_staketime():
    deploy = _deploy()
    abi_path = STT_ABI_PATH if STT_ABI_PATH.exists() else ST_ABI_PATH
    if not deploy or not abi_path.exists():
        return None, None, None
    addr = deploy.get("staking") or deploy.get("stakeTime")
    if not addr:
        return None, None, None
    w3 = _w3()
    with open(abi_path) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(addr),
        abi=abi,
    )
    return w3, contract, _account(w3)


def load_incentive():
    deploy = _deploy()
    if not deploy or not INC_ABI_PATH.exists():
        return None, None, None
    w3 = _w3()
    with open(INC_ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(deploy["consensus"]),
        abi=abi,
    )
    return w3, contract, _account(w3)


def load_native_token():
    deploy = _deploy()
    if not deploy or not NTV_ABI_PATH.exists():
        return None, None, None
    w3 = _w3()
    with open(NTV_ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(deploy.get("mod") or deploy.get("subnet")),
        abi=abi,
    )
    return w3, contract, _account(w3)


def load_registry():
    deploy = _deploy()
    if not deploy or not REG_ABI_PATH.exists() or "registry" not in deploy:
        return None, None, None
    w3 = _w3()
    with open(REG_ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(deploy["registry"]),
        abi=abi,
    )
    return w3, contract, _account(w3)


def load_governance_token():
    deploy = _deploy()
    if not deploy or not NTV_ABI_PATH.exists() or "governanceToken" not in deploy:
        return None, None, None
    w3 = _w3()
    with open(NTV_ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(deploy["governanceToken"]),
        abi=abi,
    )
    return w3, contract, _account(w3)


def _send_tx(w3, contract_fn, account, gas=500000):
    tx = contract_fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": gas,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return {"success": receipt.status == 1, "tx_hash": tx_hash.hex()}


# ── Request models ───────────────────────────────────────────────────────

class RegisterReq(BaseModel):
    key: str
    key_type: int = 0
    commission_bps: int = 1000

class CheckinReq(BaseModel):
    key: str

class KeyReq(BaseModel):
    key: str

class StakeReq(BaseModel):
    validator_key: str
    amount: str  # wei string or ether string
    lock_blocks: int = 0

class UnstakeReq(BaseModel):
    stake_id: int

class AddressReq(BaseModel):
    address: str

class StakeIdReq(BaseModel):
    stake_id: int

class ClaimValidatorReq(BaseModel):
    key: str
    to: Optional[str] = None

class RegisterSubnetReq(BaseModel):
    name: str
    subnet: str
    staking: str
    consensus: str

class SubnetIdReq(BaseModel):
    subnet_id: int

class BoostSubnetReq(BaseModel):
    subnet_id: int
    stt_token: str  # address of the Staking (STT) contract
    amount: str     # wei string

class SellBoostReq(BaseModel):
    subnet_id: int
    shares: str     # wei string
    stt_token: str

class BoostPriceReq(BaseModel):
    subnet_id: int
    num_shares: str  # wei string

class PoolInfoReq(BaseModel):
    subnet_id: int

class UserSharesReq(BaseModel):
    subnet_id: int
    address: str

class GenerateSubnetReq(BaseModel):
    prompt: str

class DeploySubnetReq(BaseModel):
    name: str
    symbol: str
    max_lock_blocks: int = 100000
    max_stakers_per_validator: int = 100
    default_commission_bps: int = 1000
    epoch_length: int = 43200
    emission_rate: str = "100"
    decay_bps: int = 500
    consensus_type: str = "yuma"  # yuma | linear | staked


# ── Incentive endpoints ──────────────────────────────────────────────────

@app.post("/get_consensus")
async def get_consensus():
    w3, contract, _ = load_incentive()
    if not contract:
        raise HTTPException(status_code=500, detail="Contract not deployed")
    r = contract.functions.getBlock().call()
    decay = contract.functions.decayBps().call()
    return {"result": {
        "currentBlock": r[0],
        "lastEmissionBlock": r[1],
        "totalBlocktime": r[2],
        "emissionRate": str(r[3]),
        "epochLength": r[4],
        "decayBps": decay,
    }}


@app.post("/get_validators")
async def get_validators():
    w3_st, st, _ = load_staketime()
    w3_inc, inc, _ = load_incentive()
    if not st or not inc:
        raise HTTPException(status_code=500, detail="Contracts not deployed")

    count = st.functions.validatorCount().call()
    validators = []
    for i in range(count):
        kh = st.functions.getValidatorKeyHash(i).call()
        v = st.functions.getValidatorByHash(kh).call()
        score = inc.functions.getValidatorScore(kh).call()
        bal = inc.functions.validatorBalances(kh).call()
        total_stt = st.functions.getValidatorTotalMintedByHash(kh).call()
        validators.append({
            "key": v[0],
            "keyHash": kh.hex(),
            "keyType": v[1],
            "registeredBlock": v[2],
            "commissionBps": v[3],
            "active": v[4],
            "lastSeenBlock": score[0],
            "blocktimeScore": score[1],
            "earned": str(score[2]),
            "balance": str(bal),
            "totalSTT": str(total_stt),
        })
    return {"result": validators}


@app.post("/register")
async def register(req: RegisterReq):
    w3, contract, account = load_staketime()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        result = _send_tx(w3, contract.functions.registerValidatorAdmin(
            req.key, req.key_type, req.commission_bps
        ), account)
        result["key"] = req.key
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/checkin")
async def checkin(req: CheckinReq):
    w3, contract, account = load_incentive()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        return {"result": _send_tx(w3, contract.functions.batchCheckin([req.key]), account, gas=300000)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/produce_block")
async def produce_block():
    w3, contract, account = load_incentive()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        result = _send_tx(w3, contract.functions.produceBlock(), account)
        r = contract.functions.getBlock().call()
        result["block"] = r[0]
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/distribute")
async def distribute():
    w3, contract, account = load_incentive()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        return {"result": _send_tx(w3, contract.functions.distributeEmissions(), account, gas=1000000)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── StakeTime endpoints ─────────────────────────────────────────────────

@app.post("/stake_on")
async def stake_on(req: StakeReq):
    w3, contract, account = load_staketime()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        # Parse amount — if it looks like a small number, treat as ether
        amount = int(req.amount) if len(req.amount) > 10 else Web3.to_wei(float(req.amount), "ether")

        # Approve nativeToken first
        _, ntv, _ = load_native_token()
        approve_result = _send_tx(w3, ntv.functions.approve(contract.address, amount), account)

        result = _send_tx(w3, contract.functions.stakeOn(req.validator_key, amount, req.lock_blocks), account)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/unstake_from")
async def unstake_from(req: UnstakeReq):
    w3, contract, account = load_staketime()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        return {"result": _send_tx(w3, contract.functions.unstakeFrom(req.stake_id), account)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_user_stakes")
async def get_user_stakes(req: AddressReq):
    w3, contract, _ = load_staketime()
    if not contract:
        raise HTTPException(status_code=500, detail="Not deployed")
    addr = Web3.to_checksum_address(req.address)
    ids = contract.functions.getUserStakeIds(addr).call()
    stakes = []
    for sid in ids:
        pos = contract.functions.getStakePosition(sid).call()
        stakes.append({
            "stakeId": sid,
            "staker": pos[0],
            "validatorKeyHash": pos[1].hex(),
            "amount": str(pos[2]),
            "startBlock": pos[3],
            "lockBlocks": pos[4],
            "mintedBalance": str(pos[5]),
            "blocksRemaining": pos[6],
        })
    return {"result": stakes}


@app.post("/get_stake_position")
async def get_stake_position(req: StakeIdReq):
    w3, contract, _ = load_staketime()
    if not contract:
        raise HTTPException(status_code=500, detail="Not deployed")
    pos = contract.functions.getStakePosition(req.stake_id).call()
    return {"result": {
        "stakeId": req.stake_id,
        "staker": pos[0],
        "validatorKeyHash": pos[1].hex(),
        "amount": str(pos[2]),
        "startBlock": pos[3],
        "lockBlocks": pos[4],
        "mintedBalance": str(pos[5]),
        "blocksRemaining": pos[6],
    }}


@app.post("/get_validator")
async def get_validator(req: KeyReq):
    w3_st, st, _ = load_staketime()
    w3_inc, inc, _ = load_incentive()
    if not st or not inc:
        raise HTTPException(status_code=500, detail="Not deployed")
    v = st.functions.getValidator(req.key).call()
    kh = Web3.solidity_keccak(["string"], [req.key])
    score = inc.functions.getValidatorScore(kh).call()
    bal = inc.functions.validatorBalances(kh).call()
    total_stt = st.functions.getValidatorTotalMintedByHash(kh).call()
    return {"result": {
        "key": v[0],
        "keyType": v[1],
        "registeredBlock": v[2],
        "commissionBps": v[3],
        "active": v[4],
        "lastSeenBlock": score[0],
        "blocktimeScore": score[1],
        "earned": str(score[2]),
        "balance": str(bal),
        "totalSTT": str(total_stt),
    }}


# ── Reward claims ────────────────────────────────────────────────────────

@app.post("/claim_staker_rewards")
async def claim_staker_rewards():
    w3, contract, account = load_incentive()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        return {"result": _send_tx(w3, contract.functions.claimStakerRewards(), account)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/claim_validator_rewards")
async def claim_validator_rewards(req: ClaimValidatorReq):
    w3, contract, account = load_incentive()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Not deployed or no signer")
    try:
        to = Web3.to_checksum_address(req.to) if req.to else account.address
        return {"result": _send_tx(w3, contract.functions.claimValidatorRewards(req.key, to), account)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_staker_rewards")
async def get_staker_rewards(req: AddressReq):
    w3, contract, _ = load_incentive()
    if not contract:
        raise HTTPException(status_code=500, detail="Not deployed")
    addr = Web3.to_checksum_address(req.address)
    return {"result": str(contract.functions.getStakerRewards(addr).call())}


@app.post("/get_deployment")
async def get_deployment():
    deploy = _deploy()
    if not deploy:
        raise HTTPException(status_code=500, detail="Not deployed")
    return {"result": deploy}


# ── Registry endpoints ──────────────────────────────────────────────────

@app.post("/get_subnets")
async def get_subnets():
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    try:
        raw = contract.functions.getAllSubnets().call()
        subnets = []
        for s in raw:
            score = contract.functions.getStakeScore(s[0]).call()
            immune = contract.functions.isImmune(s[0]).call()
            pool = contract.functions.getPoolInfo(s[0]).call()
            subnets.append({
                "id": s[0],
                "owner": s[1],
                "name": s[2],
                "subnet": s[3],
                "staking": s[4],
                "consensus": s[5],
                "registeredBlock": s[6],
                "active": s[7],
                "stakeScore": str(score),
                "immune": immune,
                "totalShares": str(pool[0]),
                "totalBloctime": str(pool[1]),
                "sharePrice": str(pool[2]),
                "lockedGov": str(pool[3]),
            })
        return {"result": subnets}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_registration_cost")
async def get_registration_cost():
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    cost = contract.functions.getRegistrationCost().call()
    return {"result": str(cost)}


@app.post("/register_subnet")
async def register_subnet(req: RegisterSubnetReq):
    w3, contract, account = load_registry()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Registry not deployed or no signer")
    try:
        # Approve governance token for registration cost
        cost = contract.functions.getRegistrationCost().call()
        if cost > 0:
            _, gov, _ = load_governance_token()
            _send_tx(w3, gov.functions.approve(contract.address, cost), account)

        result = _send_tx(w3, contract.functions.registerSubnet(
            req.name,
            Web3.to_checksum_address(req.subnet),
            Web3.to_checksum_address(req.staking),
            Web3.to_checksum_address(req.consensus),
        ), account)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/deregister_subnet")
async def deregister_subnet(req: SubnetIdReq):
    w3, contract, account = load_registry()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Registry not deployed or no signer")
    try:
        result = _send_tx(w3, contract.functions.deregisterSubnet(req.subnet_id), account)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_subnet")
async def get_subnet(req: SubnetIdReq):
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    r = contract.functions.getSubnet(req.subnet_id).call()
    score = contract.functions.getStakeScore(req.subnet_id).call()
    immune = contract.functions.isImmune(req.subnet_id).call()
    return {"result": {
        "id": r[0],
        "owner": r[1],
        "name": r[2],
        "subnet": r[3],
        "staking": r[4],
        "consensus": r[5],
        "registeredBlock": r[6],
        "active": r[7],
        "stakeScore": str(score),
        "immune": immune,
    }}


@app.post("/get_weakest_subnet")
async def get_weakest_subnet():
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    weak_id, weak_score, found = contract.functions.getWeakestSubnet().call()
    return {"result": {
        "id": weak_id,
        "score": str(weak_score),
        "found": found,
    }}


# ── Bonding Curve Pool endpoints ───────────────────────────────────────

@app.post("/boost_subnet")
async def boost_subnet(req: BoostSubnetReq):
    """Deposit STT (bloctime) to boost a subnet via bonding curve."""
    w3, contract, account = load_registry()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Registry not deployed or no signer")
    try:
        stt_addr = Web3.to_checksum_address(req.stt_token)
        amount = int(req.amount)

        # Approve STT token for registry
        with open(ST_ABI_PATH) as f:
            stt_abi = json.load(f)["abi"]
        stt_contract = w3.eth.contract(address=stt_addr, abi=stt_abi)
        _send_tx(w3, stt_contract.functions.approve(contract.address, amount), account)

        result = _send_tx(w3, contract.functions.boostSubnet(
            req.subnet_id, stt_addr, amount
        ), account, gas=500000)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/sell_boost")
async def sell_boost(req: SellBoostReq):
    """Sell shares back, receive STT from the pool."""
    w3, contract, account = load_registry()
    if not contract or not account:
        raise HTTPException(status_code=500, detail="Registry not deployed or no signer")
    try:
        shares = int(req.shares)
        stt_addr = Web3.to_checksum_address(req.stt_token)
        result = _send_tx(w3, contract.functions.sellBoost(
            req.subnet_id, shares, stt_addr
        ), account, gas=300000)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_boost_price")
async def get_boost_price(req: BoostPriceReq):
    """Get STT cost for buying numShares at current supply."""
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    num_shares = int(req.num_shares)
    cost = contract.functions.getBoostPrice(req.subnet_id, num_shares).call()
    sell_return = contract.functions.getSellReturn(req.subnet_id, num_shares).call()
    return {"result": {
        "buyCost": str(cost),
        "sellReturn": str(sell_return),
    }}


@app.post("/get_pool_info")
async def get_pool_info(req: PoolInfoReq):
    """Get bonding curve pool info for a subnet."""
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    pool = contract.functions.getPoolInfo(req.subnet_id).call()
    score = contract.functions.getStakeScore(req.subnet_id).call()
    try:
        bloctime_price = contract.functions.getBloctimePrice(req.subnet_id).call()
    except Exception:
        bloctime_price = 0
    return {"result": {
        "totalShares": str(pool[0]),
        "totalBloctime": str(pool[1]),
        "sharePrice": str(pool[2]),
        "lockedGov": str(pool[3]),
        "stakeScore": str(score),
        "bloctimePrice": str(bloctime_price),
    }}


@app.post("/get_user_shares")
async def get_user_shares(req: UserSharesReq):
    """Get user's shares in a subnet's bonding curve pool."""
    w3, contract, _ = load_registry()
    if not contract:
        raise HTTPException(status_code=500, detail="Registry not deployed")
    addr = Web3.to_checksum_address(req.address)
    shares = contract.functions.getUserShares(req.subnet_id, addr).call()
    return {"result": str(shares)}


SUBNET_SYSTEM_PROMPT = """You generate mod deployment parameters for a staking protocol. Given a user's description, output ONLY a JSON object with these fields (no markdown, no explanation):

{
  "name": "Human-readable mod name",
  "symbol": "3-5 char token ticker",
  "maxLockBlocks": integer max lock duration in blocks (1 block ≈ 2s on Base),
  "maxStakersPerValidator": integer max stakers per validator per epoch,
  "defaultCommissionBps": integer validator commission in basis points (1000 = 10%),
  "epochLength": integer blocks per epoch (43200 ≈ 1 day on Base),
  "emissionRate": "tokens minted per epoch in whole tokens (e.g. '100')",
  "decayBps": integer score decay per epoch in basis points (500 = 5%),
  "description": "one-line summary of what this mod does"
}

Note: Mod tokens start with zero supply. All tokens are minted by the consensus mechanism.

Guidelines:
- For AI/compute mods: higher emissions (500-5000/epoch), shorter epochs (21600-43200)
- For data/storage mods: moderate emissions (100-500/epoch), longer epochs (43200-86400)
- For governance/social mods: lower emissions (10-100/epoch), longer epochs (86400+)
- maxLockBlocks: shorter for liquid markets (50000), longer for long-term alignment (500000)
- Higher decay (1000-2000 bps) rewards consistent uptime, lower (200-500) is more forgiving
- Commission 500-2000 bps is typical (5-20%)
- Keep symbol uppercase, 3-5 chars, relevant to the mod purpose"""


def _llm_generate(prompt: str) -> dict:
    """Call an LLM to generate subnet params. Supports OpenRouter, Anthropic, or any OpenAI-compatible API."""
    import httpx

    # Resolve provider: OpenRouter > Anthropic > OpenAI-compatible
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if openrouter_key:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = openrouter_key
        model = os.environ.get("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://staketime.mod",
            "X-Title": "StakeTime Mod Creator",
        }
    elif anthropic_key:
        # Use Anthropic Messages API directly
        headers = {
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5-20250929")
        body = {
            "model": model,
            "max_tokens": 1024,
            "system": SUBNET_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        return _parse_llm_json(text)
    elif openai_key:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")
        api_key = openai_key
        model = os.environ.get("LLM_MODEL", "gpt-4o")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    else:
        raise RuntimeError("Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY")

    # OpenAI-compatible request (OpenRouter / OpenAI)
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SUBNET_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    import time as _time
    for attempt in range(4):
        resp = httpx.post(base_url, headers=headers, json=body, timeout=60)
        if resp.status_code == 429 and attempt < 3:
            _time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        break
    text = resp.json()["choices"][0]["message"]["content"].strip()
    return _parse_llm_json(text)


def _parse_llm_json(text: str) -> dict:
    """Strip markdown fences and parse JSON from LLM output."""
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


@app.post("/generate_subnet_params")
async def generate_subnet_params(req: GenerateSubnetReq):
    """Use an LLM (via OpenRouter, Anthropic, or OpenAI) to generate subnet deployment params."""
    try:
        params = _llm_generate(req.prompt)
        return {"result": params}
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"LLM returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/deploy_subnet")
async def deploy_subnet(req: DeploySubnetReq):
    """Deploy a new mod (Mod ERC20 + StakeTime) and register it in the Registry."""
    deploy = _deploy()
    if not deploy or "registry" not in deploy:
        raise HTTPException(status_code=500, detail="Registry not deployed")

    valid_types = ("yuma", "linear", "staked")
    if req.consensus_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"consensus_type must be one of: {', '.join(valid_types)}")

    params = {
        "name": req.name,
        "symbol": req.symbol,
        "maxLockBlocks": req.max_lock_blocks,
        "maxStakersPerValidator": req.max_stakers_per_validator,
        "defaultCommissionBps": req.default_commission_bps,
        "epochLength": req.epoch_length,
        "emissionRate": req.emission_rate,
        "decayBps": req.decay_bps,
        "consensusType": req.consensus_type,
        "registryAddress": deploy["registry"],
    }

    network = os.environ.get("NETWORK", "base_sepolia")
    # Validate network name to prevent injection
    if not re.match(r'^[a-zA-Z0-9_-]+$', network):
        raise HTTPException(status_code=400, detail=f"Invalid network name: {network}")
    env = os.environ.copy()
    env["SUBNET_PARAMS"] = json.dumps(params)

    try:
        result = subprocess.run(
            ["npx", "hardhat", "run", "scripts/deploy_subnet.js", "--network", network],
            shell=False,
            cwd=str(MODULE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        output = result.stdout + result.stderr

        # Parse result from output
        match = re.search(r"__RESULT__(.+?)__END__", output)
        if match:
            deployed = json.loads(match.group(1))
            return {"result": deployed}

        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=f"Deploy failed: {output[-500:]}")

        return {"result": {"output": output, "success": result.returncode == 0}}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Deploy timed out")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "module": "staketime"}
