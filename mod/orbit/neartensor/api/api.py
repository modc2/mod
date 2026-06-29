"""
NearTensor API — FastAPI wrapper over the neartensor mod.

Launched/killed via mod.py serve_api() / kill_api().

The Next.js app calls POST /neartensor/<fn> (see app/app/lib/api.ts). Every
call is dispatched to a method on the neartensor Mod. When the protocol has not
yet been deployed to NEAR testnet, reads return an honest empty state and writes
return a clear "not deployed" error — no fabricated data.
"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))            # module dir (mod.py lives in neartensor/)
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))  # mod root

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import mod as m

_inst = None


def nt():
    global _inst
    if _inst is None:
        _inst = m.mod("neartensor")()
    return _inst


app = FastAPI(title="NearTensor API",
              description="Bittensor-inspired subnet protocol on NEAR Protocol.",
              version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Read actions and the empty value they return before deployment ──
READ_DEFAULTS = {
    "subnets": [],
    "validators": [],
    "leaderboard": [],
    "consensus_state": None,
    "subnet_info": None,
    "staker_rewards": None,
    "validator_balance": None,
}
# Actions that mutate NEAR state — meaningless without a deployed registry.
WRITE_ACTIONS = {
    "register_validator", "stake_on", "unstake_from", "checkin", "batch_checkin",
    "produce_block", "claim_staker_rewards", "claim_validator_rewards",
    "register_subnet", "boost_subnet", "sell_boost", "deploy", "build",
}
NOT_DEPLOYED = ("NearTensor is not deployed to NEAR testnet yet — "
                "deploy the registry (m neartensor/deploy) to enable on-chain actions.")


def _maybe_json(val):
    """near-cli view methods return raw stdout strings; parse JSON when possible."""
    if isinstance(val, str):
        s = val.strip()
        if s and s[0] in "[{\"" or s in ("true", "false", "null") or s.lstrip("-").isdigit():
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return val
    return val


def _deployed(inst):
    try:
        return bool(inst.status().get("deployed"))
    except Exception:
        return False


def _dispatch(action, params):
    inst = nt()
    if action.startswith("_") or not hasattr(inst, action):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")

    deployed = _deployed(inst)
    if not deployed:
        if action in READ_DEFAULTS:
            return READ_DEFAULTS[action]
        if action in WRITE_ACTIONS:
            raise HTTPException(status_code=400, detail=NOT_DEPLOYED)

    fn = getattr(inst, action)
    if not callable(fn):
        return _maybe_json(fn)
    try:
        result = fn(**params)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"bad arguments for {action}: {e}")
    except Exception as e:
        # On-chain call failed (network/account). Reads degrade to empty, writes surface the error.
        if action in READ_DEFAULTS:
            return READ_DEFAULTS[action]
        raise HTTPException(status_code=400, detail=str(e))
    return _maybe_json(result)


# ── Standard probes ────────────────────────────────────────────────
@app.get("/")
def root():
    return nt().health()


@app.get("/health")
def health():
    return nt().health()


@app.get("/status")
def status_get():
    return nt().status()


# ── Generic dispatch: app posts to /neartensor/<fn> ────────────────
@app.post("/neartensor/{action}")
async def dispatch_prefixed(action: str, request: Request):
    try:
        params = await request.json()
    except Exception:
        params = {}
    return _dispatch(action, params if isinstance(params, dict) else {})


@app.post("/{action}")
async def dispatch(action: str, request: Request):
    try:
        params = await request.json()
    except Exception:
        params = {}
    return _dispatch(action, params if isinstance(params, dict) else {})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50180")))
