"""
Cost Market API — FastAPI over the costmarket mod.

Reads are open: the book, the odds, the oracle reading, and every settlement
are things a market's participants have to be able to check. Writes that move
someone else's money (settle, confirm, buckets) take the owner key, which
lives off-tree in ~/.mod/costmarket/owner.json.

Launched via mod.py serve_api().
"""
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import mod as m

_cm = None


def cm():
    global _cm
    if _cm is None:
        _cm = m.mod("costmarket")()
    return _cm


app = FastAPI(
    title="Cost Market",
    description="Monthly prediction market on the average AI cost per user.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


def _check(result):
    """A mod-layer {"error": ...} is a client error, not a 500 — these are
    refusals (unsubscribed, betting closed, not the owner), not failures."""
    if isinstance(result, dict) and result.get("error"):
        code = 403 if "owner" in result["error"] else 400
        raise HTTPException(status_code=code, detail=result["error"])
    return result


# ── Read ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return cm().health()


@app.get("/status")
def status():
    return cm().status()


@app.get("/oracle")
def oracle(month: str = ""):
    return cm().oracle(month)


@app.get("/epochs")
def epochs(limit: int = 12):
    return {"epochs": cm().epochs(limit)}


@app.get("/epoch/{month}")
def epoch(month: str):
    return cm().epoch(month)


@app.get("/book/{month}")
def book(month: str):
    return cm().book(month)


@app.get("/account/{address}")
def account(address: str):
    return cm().account(address)


@app.get("/member/{address}")
def member(address: str, month: str = ""):
    return cm().is_member(address, month)


@app.get("/leaderboard")
def leaderboard(limit: int = 25):
    return {"leaderboard": cm().leaderboard(limit)}


@app.get("/treasury")
def treasury():
    return cm().treasury_report()


@app.get("/config")
def config():
    return cm().config


# ── Write ───────────────────────────────────────────────────────

class Subscribe(BaseModel):
    address: str
    amount_usd: Optional[Any] = None
    tx_hash: str = ""
    month: str = ""


class Bet(BaseModel):
    address: str
    bucket: int
    amount_usd: Optional[Any] = None
    month: str = ""


class Withdraw(BaseModel):
    address: str
    amount_usd: Optional[Any] = None


class OwnerAction(BaseModel):
    key: str = ""
    month: str = ""
    address: str = ""


class Buckets(BaseModel):
    key: str = ""
    month: str
    edges: List[float]


@app.post("/subscribe")
def subscribe(req: Subscribe):
    return _check(cm().subscribe(req.address, req.amount_usd, req.tx_hash, req.month))


@app.post("/bet")
def bet(req: Bet):
    return _check(cm().bet(req.address, req.bucket, req.amount_usd, req.month))


@app.post("/withdraw")
def withdraw(req: Withdraw):
    return _check(cm().withdraw(req.address, req.amount_usd))


@app.post("/settle")
def settle(req: OwnerAction):
    return _check(cm().settle(req.month, req.key))


@app.post("/confirm")
def confirm(req: OwnerAction):
    return _check(cm().confirm_subscription(req.address, req.month, req.key))


@app.post("/buckets")
def buckets(req: Buckets):
    return _check(cm().set_buckets(req.month, req.edges, req.key))


@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    inst = cm()
    if not hasattr(inst, action) or action.startswith("_"):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(inst, action)
    return {"result": fn(**body) if callable(fn) else fn}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50490")))
