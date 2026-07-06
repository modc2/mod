"""
StakeTime + Incentive API

FastAPI backend backed by Rust engine (staketime_rs via PyO3).
All blockchain calls go through the Rust alloy layer for performance.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="StakeTime API", description="Delegated staking + Yuma consensus emissions (Rust backend)")

_cors_origins = os.environ.get("MOD_CORS_ORIGINS", "").split(",") if os.environ.get("MOD_CORS_ORIGINS") else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "token"],
)

# ── Rust engine setup ─────────────────────────────────────────────────────

MODULE_DIR = Path(__file__).parent.parent.parent
SRC_DIR = Path(__file__).parent.parent

# Add src dir to path for staketime_rs import
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_engine = None


def get_engine():
    """Lazy-init the Rust StakeTimeEngine."""
    global _engine
    if _engine is None:
        try:
            from staketime_rs import StakeTimeEngine
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="staketime_rs not built. Run: cd src/rs && ./build.sh",
            )
        rpc = os.environ.get("BASE_TESTNET_RPC_URL", "https://sepolia.base.org")
        pk = os.environ.get("PRIVATE_KEY")
        network = os.environ.get("NETWORK", "base_sepolia")
        _engine = StakeTimeEngine(str(MODULE_DIR), rpc, pk, network)
        _engine.init()
    return _engine


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
    try:
        engine = get_engine()
        result = json.loads(engine.get_consensus())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_validators")
async def get_validators():
    try:
        engine = get_engine()
        result = json.loads(engine.get_validators())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/register")
async def register(req: RegisterReq):
    try:
        engine = get_engine()
        result = json.loads(engine.register(req.key, req.key_type, req.commission_bps))
        result["key"] = req.key
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/checkin")
async def checkin(req: CheckinReq):
    try:
        engine = get_engine()
        result = json.loads(engine.checkin(req.key))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/produce_block")
async def produce_block():
    try:
        engine = get_engine()
        result = json.loads(engine.produce_block())
        # Fetch updated block state
        state = json.loads(engine.get_consensus())
        result["block"] = state["currentBlock"]
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/distribute")
async def distribute():
    try:
        engine = get_engine()
        result = json.loads(engine.distribute())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── StakeTime endpoints ─────────────────────────────────────────────────

@app.post("/stake_on")
async def stake_on(req: StakeReq):
    try:
        engine = get_engine()
        # Parse amount — if it looks like a small number, treat as ether
        if len(req.amount) <= 10:
            amount_wei = str(int(float(req.amount) * 10**18))
        else:
            amount_wei = req.amount
        result = json.loads(engine.stake_on(req.validator_key, amount_wei, req.lock_blocks))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/unstake_from")
async def unstake_from(req: UnstakeReq):
    try:
        engine = get_engine()
        result = json.loads(engine.unstake_from(req.stake_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_user_stakes")
async def get_user_stakes(req: AddressReq):
    try:
        engine = get_engine()
        result = json.loads(engine.get_user_stakes(req.address))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_stake_position")
async def get_stake_position(req: StakeIdReq):
    try:
        engine = get_engine()
        result = json.loads(engine.get_stake_position(req.stake_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_validator")
async def get_validator(req: KeyReq):
    try:
        engine = get_engine()
        result = json.loads(engine.get_validator(req.key))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Reward claims ────────────────────────────────────────────────────────

@app.post("/claim_staker_rewards")
async def claim_staker_rewards():
    try:
        engine = get_engine()
        result = json.loads(engine.claim_staker_rewards())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/claim_validator_rewards")
async def claim_validator_rewards(req: ClaimValidatorReq):
    try:
        engine = get_engine()
        result = json.loads(engine.claim_validator_rewards(req.key, req.to))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_staker_rewards")
async def get_staker_rewards(req: AddressReq):
    try:
        engine = get_engine()
        result = engine.get_staker_rewards(req.address)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_deployment")
async def get_deployment():
    try:
        engine = get_engine()
        result = json.loads(engine.get_deployment())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Registry endpoints ──────────────────────────────────────────────────

@app.post("/get_subnets")
async def get_subnets():
    try:
        engine = get_engine()
        result = json.loads(engine.get_subnets())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_registration_cost")
async def get_registration_cost():
    try:
        engine = get_engine()
        result = engine.get_registration_cost()
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/register_subnet")
async def register_subnet(req: RegisterSubnetReq):
    try:
        engine = get_engine()
        result = json.loads(engine.register_subnet(req.name, req.subnet, req.staking, req.consensus))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/deregister_subnet")
async def deregister_subnet(req: SubnetIdReq):
    try:
        engine = get_engine()
        result = json.loads(engine.deregister_subnet(req.subnet_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_subnet")
async def get_subnet(req: SubnetIdReq):
    try:
        engine = get_engine()
        result = json.loads(engine.get_subnet(req.subnet_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_weakest_subnet")
async def get_weakest_subnet():
    try:
        engine = get_engine()
        result = json.loads(engine.get_weakest_subnet())
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Bonding Curve Pool endpoints ───────────────────────────────────────

@app.post("/boost_subnet")
async def boost_subnet(req: BoostSubnetReq):
    """Deposit STT (bloctime) to boost a subnet via bonding curve."""
    try:
        engine = get_engine()
        result = json.loads(engine.boost_subnet(req.subnet_id, req.stt_token, req.amount))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/sell_boost")
async def sell_boost(req: SellBoostReq):
    """Sell shares back, receive STT from the pool."""
    try:
        engine = get_engine()
        result = json.loads(engine.sell_boost(req.subnet_id, req.shares, req.stt_token))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/get_boost_price")
async def get_boost_price(req: BoostPriceReq):
    """Get STT cost for buying numShares at current supply."""
    try:
        engine = get_engine()
        pool = json.loads(engine.get_pool_info(req.subnet_id))
        return {"result": {
            "buyCost": pool.get("sharePrice", "0"),
            "sellReturn": "0",
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_pool_info")
async def get_pool_info(req: PoolInfoReq):
    """Get bonding curve pool info for a subnet."""
    try:
        engine = get_engine()
        result = json.loads(engine.get_pool_info(req.subnet_id))
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/get_user_shares")
async def get_user_shares(req: UserSharesReq):
    """Get user's shares in a subnet's bonding curve pool."""
    # This reads directly from registry — not yet in rust engine, use fallback
    try:
        engine = get_engine()
        # For now return via pool info query
        return {"result": "0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── LLM Integration ───────────────────────────────────────��──────────────

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
    config_path = MODULE_DIR / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="Not deployed")
    with open(config_path) as f:
        data = json.load(f)
    network = os.environ.get("NETWORK", "base_sepolia")
    deploy = data.get("contracts", {}).get(network, {})
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
    return {"status": "ok", "module": "staketime", "backend": "rust+pyo3"}
