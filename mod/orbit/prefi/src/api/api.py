import os
import sys
import json
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

prefi_src = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, prefi_src)

app = FastAPI(title="PreFi API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_mod = None

def get_mod():
    global _mod
    if _mod is None:
        from mod import Mod
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.json')
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        _mod = Mod(config)
    return _mod


# ── Health & Status ──────────────────────────────────────────────

@app.get("/health")
def health():
    return get_mod().health()

@app.get("/status")
def status():
    return get_mod().status()


# ── Markets ──────────────────────────────────────────────────────

@app.get("/markets")
def list_markets():
    return get_mod().list_markets()

@app.post("/markets/add")
def add_market(
    token: str = Query(..., description="Token contract address"),
    symbol: str = Query(..., description="Token symbol e.g. WETH"),
    fee_tier: int = Query(3000, description="Uniswap V3 fee tier (500, 3000, 10000)"),
):
    return get_mod().add_market(token, symbol, fee_tier)


# ── Positions ────────────────────────────────────────────────────

@app.post("/position/open")
def open_position(
    asset: str = Query(..., description="Asset symbol e.g. WETH"),
    amount: float = Query(..., description="USDC amount to invest"),
    address: str = Query(..., description="Trader address"),
):
    result = get_mod().open_position(asset, amount, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/position/close")
def close_position(
    position_id: int = Query(..., description="Position ID"),
    address: str = Query(..., description="Trader address"),
):
    result = get_mod().close_position(position_id, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.get("/positions/{address}")
def get_positions(address: str):
    return get_mod().get_positions(address)


# ── Staking ──────────────────────────────────────────────────────

@app.post("/stake/lock")
def lock_prefi(
    amount: float = Query(..., description="PREFI amount to lock"),
    duration: int = Query(..., description="Lock duration in seconds (min 604800 = 1 week)"),
    address: str = Query(..., description="Staker address"),
):
    result = get_mod().lock_prefi(amount, duration, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/stake/extend")
def extend_lock(
    stake_id: int = Query(..., description="Stake ID to extend"),
    duration: int = Query(..., description="Additional lock duration in seconds"),
    address: str = Query(..., description="Staker address"),
):
    result = get_mod().extend_lock(stake_id, duration, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/stake/unlock")
def unlock_prefi(
    stake_id: int = Query(..., description="Stake ID"),
    address: str = Query(..., description="Staker address"),
):
    result = get_mod().unlock_prefi(stake_id, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.get("/stakes/{address}")
def get_stakes(address: str):
    return get_mod().get_stakes(address)


# ── Treasury ─────────────────────────────────────────────────────

@app.get("/treasury")
def treasury():
    return get_mod().treasury()

@app.get("/treasury/history")
def treasury_history():
    return get_mod().treasury_history()

@app.post("/treasury/distribute")
def distribute_rewards(
    amount: Optional[float] = Query(None, description="Amount to distribute (default: all)"),
):
    result = get_mod().deposit_rewards(amount)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/treasury/claim")
def claim_treasury(
    epoch: int = Query(..., description="Epoch number to claim"),
    address: str = Query(..., description="Staker address"),
):
    result = get_mod().claim_treasury(epoch, address)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


# ── Leaderboard & Portfolio ──────────────────────────────────────

@app.get("/leaderboard")
def leaderboard():
    return get_mod().leaderboard()

@app.get("/portfolio/{address}")
def portfolio(address: str):
    return get_mod().portfolio(address)


# ── Prices ───────────────────────────────────────────────────────

@app.get("/prices")
def get_prices():
    return get_mod().get_prices()

@app.get("/prices/{asset}")
def get_asset_price(asset: str):
    return get_mod().get_asset_price(asset)


# ── Deployment ───────────────────────────────────────────────────

@app.get("/deployment")
def deployment():
    return get_mod().get_deployment_info()

@app.get("/test")
def test():
    return get_mod().test()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8830))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
