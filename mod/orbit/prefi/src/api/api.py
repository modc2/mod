import os
import sys
import json
from fastapi import FastAPI, Query, HTTPException, Request
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
    search: str = Query("", description="Filter by pair name or HL key"),
    limit: int = Query(50, description="Max results — 0 for every pair"),
    kind: str = Query("all", description="all | perp | spot"),
):
    return get_mod().hl_assets(search, limit, kind)

@app.get("/hyperliquid/stats")
def hl_stats():
    """How many pairs Hyperliquid quotes, how many are listed here, and how old
    the snapshot is — a picker showing 24 of 900 rows has to be able to say so."""
    return get_mod().hl_stats()

@app.post("/hyperliquid/seed")
def seed_hl_markets(
    limit: int = Query(20, description="How many pairs to list"),
    kind: str = Query("all", description="all | perp | spot"),
    min_volume: float = Query(0, description="Skip pairs under this 24h volume"),
):
    """List the busiest pairs in one call — standing a pool up without clicking
    through a 900-row picker."""
    return _ok(get_mod().seed_hl(limit, kind, min_volume))


@app.post("/hyperliquid/add")
def add_hl_market(
    coin: str = Query(..., description="Hyperliquid pair: a perp (SOL), a spot pair (HYPE/USDC) or an @index key"),
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
    address: str = Query(..., description="Forecaster address"),
    burn: float = Query(0, description="PREFI to burn — 0 (the default) spends a free call"),
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

# Ahead of /predictions/{address} — FastAPI matches routes in order, and a
# literal segment must be declared before the path parameter that would eat it.
@app.get("/predictions/free/{address}")
def free_quota(address: str):
    """Free calls left in this address's rolling 24h window"""
    return get_mod().free_quota(address)

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
    free_per_day: Optional[int] = Query(None, description="Free calls per address per 24h (0 = off)"),
    free_payout: Optional[float] = Query(None, description="PREFI a perfect free call mints"),
):
    result = get_mod().set_scoring(model=model, tolerance=tolerance,
                                   multiplier=multiplier, horizon=horizon,
                                   min_burn=min_burn, free_per_day=free_per_day,
                                   free_payout=free_payout)
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

@app.get("/prices/{asset:path}")
def get_asset_price(asset: str):
    """`:path` because a spot pair symbol carries a slash — HYPE/USDC is one
    asset, not a two-segment route."""
    return get_mod().get_asset_price(asset)


# ── Stake pool (real USDC/USDT0 on HyperEVM) ─────────────────────
#
# Reads are open. Everything that spends a balance carries a wallet signature
# the engine checks itself — these routes never decide who may move money, they
# just hand the arguments through, so there is one place to audit.

def _ok(result):
    if isinstance(result, dict) and 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.get("/pool")
def pool_status():
    return get_mod().pool_status()

@app.get("/pool/config")
def pool_config():
    return get_mod().pool_config()

@app.post("/pool/config")
def set_pool_config(
    interval: Optional[int] = Query(None, description="Round length in seconds (604800 = weekly)"),
    entry_cutoff: Optional[int] = Query(None, description="Entries stop this long before the close"),
    model: Optional[str] = Query(None, description="linear | l2 | exponential | threshold"),
    tolerance: Optional[float] = Query(None, description="Error scale; 1.0 + linear = 1 − relL1"),
    min_stake: Optional[float] = Query(None),
    max_stake: Optional[float] = Query(None, description="0 = uncapped"),
    min_withdraw: Optional[float] = Query(None),
    fee_bps: Optional[int] = Query(None, description="Protocol cut of a pot, max 500"),
    auto_pay: Optional[bool] = Query(None, description="Send withdrawals from the hot key"),
    spot_grace: Optional[int] = Query(None),
    free_per_round: Optional[int] = Query(None, description="Free calls per address per round (0 = off)"),
    free_notional: Optional[float] = Query(None, description="Paper stake a free call's would-have-won is priced at"),
    secret: Optional[str] = Query(None, description="Owner secret"),
    owner: Optional[str] = Query(None, description="Owner address (with signature)"),
    signature: Optional[str] = Query(None),
):
    params = {k: v for k, v in dict(
        interval=interval, entry_cutoff=entry_cutoff, model=model,
        tolerance=tolerance, min_stake=min_stake, max_stake=max_stake,
        min_withdraw=min_withdraw, fee_bps=fee_bps, auto_pay=auto_pay,
        spot_grace=spot_grace, free_per_round=free_per_round,
        free_notional=free_notional).items() if v is not None}
    return _ok(get_mod().set_pool_config(secret=secret, owner=owner,
                                         signature=signature, **params))

@app.get("/pool/owner")
def pool_owner():
    return get_mod().pool_owner()

@app.post("/pool/owner/claim")
def pool_claim_owner(address: str = Query(...), secret: Optional[str] = Query(None)):
    return _ok(get_mod().pool_claim_owner(address, secret))


@app.get("/pool/vault")
def pool_vault():
    return get_mod().pool_vault()

@app.post("/pool/vault/create")
def pool_create_vault(secret: Optional[str] = Query(None),
                      owner: Optional[str] = Query(None),
                      signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_create_vault(secret=secret, owner=owner,
                                           signature=signature))

@app.post("/pool/vault/set")
def pool_set_vault(address: str = Query(...), secret: Optional[str] = Query(None),
                   owner: Optional[str] = Query(None),
                   signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_set_vault(address, secret=secret, owner=owner,
                                        signature=signature))

@app.get("/pool/tokens")
def pool_tokens(verify: bool = Query(False, description="Re-read symbol/decimals on chain")):
    return get_mod().pool_tokens(verify=verify)

@app.post("/pool/tokens/add")
def pool_add_token(symbol: str = Query(...), address: str = Query(...),
                   secret: Optional[str] = Query(None),
                   owner: Optional[str] = Query(None),
                   signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_add_token(symbol, address, secret=secret,
                                        owner=owner, signature=signature))


@app.post("/pool/deposit")
def pool_deposit(tx: str = Query(..., description="HyperEVM transaction hash")):
    return _ok(get_mod().pool_deposit(tx))

@app.post("/pool/sync")
def pool_sync(max_chunks: int = Query(20, description="1000-block chunks to scan")):
    return _ok(get_mod().pool_sync(max_chunks))

@app.get("/pool/balance/{address}")
def pool_balance(address: str):
    return get_mod().pool_balance(address)

@app.get("/pool/ledger")
def pool_ledger(address: Optional[str] = Query(None), limit: int = Query(100)):
    return get_mod().pool_ledger(address, limit)


@app.get("/pool/sign")
def pool_sign(request: Request, action: str = Query(...), address: str = Query(...)):
    """The exact message a wallet must sign for `action`, bound to a nonce.

    Extra query params become signed fields, so the client cannot silently omit
    one — the server rebuilds this same message when it checks the signature.
    """
    fields = {k: v for k, v in request.query_params.items()
              if k not in ('action', 'address')}
    return get_mod().pool_sign(action, address, **fields)

@app.post("/pool/stake")
def pool_stake(
    address: str = Query(...),
    asset: str = Query(..., description="A Hyperliquid-priced market, e.g. BTC"),
    predicted_price: float = Query(..., description="Where it closes this round"),
    amount: float = Query(..., description="Dollars to stake"),
    signature: Optional[str] = Query(None),
    nonce: Optional[int] = Query(None),
):
    return _ok(get_mod().pool_stake(address, asset, predicted_price, amount,
                                    signature=signature, nonce=nonce))

@app.post("/pool/free")
def pool_free_stake(
    address: str = Query(...),
    asset: str = Query(..., description="A Hyperliquid-priced market, e.g. BTC"),
    predicted_price: float = Query(..., description="Where it closes this round"),
    signature: Optional[str] = Query(None),
    nonce: Optional[int] = Query(None),
):
    """Call a price with no money down — scored like a stake, paid nothing."""
    return _ok(get_mod().pool_free_stake(address, asset, predicted_price,
                                         signature=signature, nonce=nonce))

# Ahead of /pool/free/{address} — FastAPI matches in order, and "leaderboard"
# would otherwise be read as an address.
@app.get("/pool/free/leaderboard")
def pool_free_leaderboard(limit: int = Query(50)):
    return get_mod().pool_free_leaderboard(limit)

@app.get("/pool/free/{address}")
def pool_free_quota(address: str, index: Optional[int] = Query(None)):
    """Free calls this address has left in the round."""
    return get_mod().pool_free_quota(address, index)

@app.get("/pool/round")
def pool_round(index: Optional[int] = Query(None), address: Optional[str] = Query(None)):
    return get_mod().pool_round(index, address)

@app.get("/pool/rounds")
def pool_rounds(limit: int = Query(20)):
    return get_mod().pool_rounds(limit)

@app.get("/pool/entries")
def pool_entries(address: Optional[str] = Query(None), limit: int = Query(100)):
    return get_mod().pool_entries(address, limit)

@app.post("/pool/settle")
def pool_settle(force: bool = Query(False)):
    return get_mod().pool_settle(force=force)

@app.post("/pool/settle/manual")
def pool_settle_manual(index: int = Query(...), asset: str = Query(...),
                       price: float = Query(...),
                       secret: Optional[str] = Query(None),
                       owner: Optional[str] = Query(None),
                       signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_settle_manual(index, asset, price, secret=secret,
                                            owner=owner, signature=signature))

@app.get("/pool/leaderboard")
def pool_leaderboard(limit: int = Query(50)):
    return get_mod().pool_leaderboard(limit)


@app.post("/pool/withdraw")
def pool_withdraw(address: str = Query(...), amount: float = Query(...),
                  token: Optional[str] = Query(None),
                  signature: Optional[str] = Query(None),
                  nonce: Optional[int] = Query(None)):
    return _ok(get_mod().pool_withdraw(address, amount, token,
                                       signature=signature, nonce=nonce))

@app.get("/pool/withdrawals")
def pool_withdrawals(address: Optional[str] = Query(None), limit: int = Query(50)):
    return get_mod().pool_withdrawals(address, limit)

@app.post("/pool/withdrawals/{withdrawal_id}/pay")
def pool_pay_withdrawal(withdrawal_id: int, secret: Optional[str] = Query(None),
                        owner: Optional[str] = Query(None),
                        signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_pay_withdrawal(withdrawal_id, secret=secret,
                                             owner=owner, signature=signature))

@app.post("/pool/withdrawals/{withdrawal_id}/mark-paid")
def pool_mark_paid(withdrawal_id: int, tx: str = Query(...),
                   secret: Optional[str] = Query(None),
                   owner: Optional[str] = Query(None),
                   signature: Optional[str] = Query(None)):
    return _ok(get_mod().pool_mark_paid(withdrawal_id, tx, secret=secret,
                                        owner=owner, signature=signature))

@app.get("/hyperevm")
def hyperevm_status():
    return get_mod().hyperevm_status()


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
