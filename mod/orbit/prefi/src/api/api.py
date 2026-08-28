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
    source: str = Query("coingecko", description="Price source: coingecko | hyperliquid"),
):
    result = get_mod().add_market(token, symbol, fee_tier, source)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/markets/seed")
def seed_markets():
    return get_mod().seed()


# ── Hyperliquid ──────────────────────────────────────────────────

@app.get("/hyperliquid/assets")
def hl_assets(
    search: str = Query("", description="Filter by coin name"),
    limit: int = Query(50, description="Max results"),
):
    return get_mod().hl_assets(search, limit)

@app.post("/hyperliquid/add")
def add_hl_market(
    coin: str = Query(..., description="Hyperliquid perp name e.g. SOL"),
):
    result = get_mod().add_hl_market(coin)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


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


# ── Predictions ──────────────────────────────────────────────────

@app.post("/predict")
def predict(
    asset: str = Query(..., description="Market symbol e.g. WETH"),
    predicted_price: float = Query(..., description="Where the price will be at resolution"),
    burn: float = Query(..., description="PREFI to burn on the call"),
    address: str = Query(..., description="Forecaster address"),
    horizon: Optional[int] = Query(None, description="Seconds until resolution (default 86400)"),
):
    result = get_mod().predict(asset, predicted_price, burn, address, horizon)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.get("/predictions")
def list_predictions(limit: int = Query(100)):
    return get_mod().get_predictions(None, limit)

@app.get("/predictions/board")
def prediction_board():
    return get_mod().prediction_board()

@app.post("/predictions/resolve")
def resolve_predictions():
    return get_mod().resolve_predictions()

@app.get("/predictions/{address}")
def get_predictions(address: str, limit: int = Query(100)):
    return get_mod().get_predictions(address, limit)


# ── Scoring ──────────────────────────────────────────────────────

@app.get("/scoring")
def get_scoring():
    return get_mod().get_scoring()

@app.get("/scoring/models")
def scoring_models():
    return get_mod().scoring_models()

@app.post("/scoring")
def set_scoring(
    model: Optional[str] = Query(None, description="l2 | linear | exponential | threshold"),
    tolerance: Optional[float] = Query(None, description="Normalized error scale, e.g. 0.02"),
    multiplier: Optional[float] = Query(None, description="payout = burn × multiplier × score"),
    horizon: Optional[int] = Query(None, description="Default seconds until resolution"),
    min_burn: Optional[float] = Query(None, description="Smallest accepted burn"),
):
    result = get_mod().set_scoring(model=model, tolerance=tolerance,
                                   multiplier=multiplier, horizon=horizon,
                                   min_burn=min_burn)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.get("/scoring/preview")
def score_preview(
    predicted: float = Query(..., description="Predicted price"),
    actual: float = Query(..., description="Actual price"),
    model: Optional[str] = Query(None),
    tolerance: Optional[float] = Query(None),
    burn: Optional[float] = Query(None),
):
    result = get_mod().score_preview(predicted, actual, model, tolerance, burn)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


# ── PREFI balance ────────────────────────────────────────────────

@app.get("/balance/{address}")
def prefi_balance(address: str):
    return get_mod().prefi_balance(address)


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
