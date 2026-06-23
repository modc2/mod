"""
OpenPlay API — FastAPI wrapper over the openplay mod.

Launched/killed via mod.py serve_api() / kill_api().
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import mod as m

_openplay = None


def op():
    global _openplay
    if _openplay is None:
        _openplay = m.mod("openplay")()
    return _openplay


app = FastAPI(title="OpenPlay API",
              description="Pickup sports across the city — free by default.",
              version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Request models ──────────────────────────────────────────────
class Recurrence(BaseModel):
    freq: str = "once"          # once | daily | weekly
    interval: int = 1
    days: Optional[List[int]] = None   # weekly: 0=Mon..6=Sun
    until: Optional[int] = None


class Cost(BaseModel):
    amount: float = 0
    currency: str = "USDC"
    receiver: str = ""
    chain: Optional[str] = None


class CreateGame(BaseModel):
    sport: str
    title: str
    starts_at: int
    admin: str
    city: Optional[str] = None
    venue: str = ""
    neighborhood: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    duration_min: int = 60
    capacity: int = 10
    recurrence: Optional[Recurrence] = None
    cost: Optional[Cost] = None
    notes: str = ""
    admin_wallet: str = ""


class JoinReq(BaseModel):
    handle: str
    wallet: str = ""
    occ: Optional[str] = None
    payment: Optional[str] = None


class LeaveReq(BaseModel):
    handle: str
    occ: Optional[str] = None


class InviteReq(BaseModel):
    admin_key: str
    handle: str
    occ: Optional[str] = None
    series: bool = False


class KickReq(BaseModel):
    admin_key: str
    handle: str
    occ: Optional[str] = None
    series: bool = False


class AcceptReq(BaseModel):
    handle: str
    wallet: str = ""
    occ: Optional[str] = None
    payment: Optional[str] = None


class DeclineReq(BaseModel):
    handle: str
    occ: Optional[str] = None


class SetCostReq(BaseModel):
    admin_key: str
    amount: float = 0
    currency: str = "USDC"
    receiver: str = ""
    chain: Optional[str] = None


class CancelReq(BaseModel):
    admin_key: str
    occ: Optional[str] = None


class AddVenue(BaseModel):
    name: str
    lat: float
    lng: float
    city: Optional[str] = None
    neighborhood: str = ""
    sports: Optional[List[str]] = None


class SudoReq(BaseModel):
    admin_secret: str


class SudoDeleteGame(BaseModel):
    admin_secret: str
    game_id: str


class SudoRemovePlayer(BaseModel):
    admin_secret: str
    handle: str
    game_id: Optional[str] = None
    occ: Optional[str] = None


class SudoDeleteVenue(BaseModel):
    admin_secret: str
    venue_id: Optional[str] = None
    name: Optional[str] = None


class EditReq(BaseModel):
    admin_key: str
    fields: Dict[str, Any] = {}


def _check(result):
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Reads ───────────────────────────────────────────────────────
@app.get("/")
def root():
    return op().health()


@app.get("/health")
def health():
    return op().health()


@app.get("/status")
def status():
    return op().status()


@app.get("/sports")
def sports():
    return op().sports()


@app.get("/cities")
def cities():
    return op().cities()


@app.get("/venues")
def venues(city: Optional[str] = None):
    return op().venues(city=city)


@app.post("/venues")
def add_venue(req: AddVenue):
    return _check(op().add_venue(req.name, req.lat, req.lng, city=req.city,
                                 neighborhood=req.neighborhood, sports=req.sports))


@app.get("/games")
def games(sport: Optional[str] = None, city: Optional[str] = None):
    return op().games(sport=sport, city=city)


@app.get("/game/{game_id}")
def game(game_id: str, occ: Optional[str] = None):
    return _check(op().game(game_id, occ))


@app.get("/payment_requirements/{game_id}")
def payment_requirements(game_id: str):
    return _check(op().payment_requirements(game_id))


# ── Create / edit ───────────────────────────────────────────────
@app.post("/games")
def create_game(req: CreateGame):
    payload = req.model_dump(exclude_none=True)
    return _check(op().create_game(**payload))


@app.post("/game/{game_id}/edit")
def edit_game(game_id: str, req: EditReq):
    return _check(op().edit_game(game_id, req.admin_key, **(req.fields or {})))


@app.post("/game/{game_id}/cost")
def set_cost(game_id: str, req: SetCostReq):
    return _check(op().set_cost(game_id, req.admin_key, amount=req.amount,
                                currency=req.currency, receiver=req.receiver, chain=req.chain))


@app.post("/game/{game_id}/cancel")
def cancel_game(game_id: str, req: CancelReq):
    return _check(op().cancel_game(game_id, req.admin_key, occ=req.occ))


# ── RSVP / invites ──────────────────────────────────────────────
@app.post("/game/{game_id}/join")
def join(game_id: str, req: JoinReq):
    return _check(op().join(game_id, req.handle, wallet=req.wallet, occ=req.occ, payment=req.payment))


@app.post("/game/{game_id}/leave")
def leave(game_id: str, req: LeaveReq):
    return _check(op().leave(game_id, req.handle, occ=req.occ))


@app.post("/game/{game_id}/invite")
def invite(game_id: str, req: InviteReq):
    return _check(op().invite(game_id, req.admin_key, req.handle, occ=req.occ, series=req.series))


@app.post("/game/{game_id}/accept")
def accept(game_id: str, req: AcceptReq):
    return _check(op().accept_invite(game_id, req.handle, wallet=req.wallet, occ=req.occ, payment=req.payment))


@app.post("/game/{game_id}/decline")
def decline(game_id: str, req: DeclineReq):
    return _check(op().decline_invite(game_id, req.handle, occ=req.occ))


@app.post("/game/{game_id}/kick")
def kick(game_id: str, req: KickReq):
    return _check(op().kick(game_id, req.admin_key, req.handle, occ=req.occ, series=req.series))


# ── Module admin (sudo) ─────────────────────────────────────────
@app.post("/admin/verify")
def admin_verify(req: SudoReq):
    return op().verify_sudo(req.admin_secret)


@app.post("/admin/delete_game")
def admin_delete_game(req: SudoDeleteGame):
    return _check(op().admin_delete_game(req.admin_secret, req.game_id))


@app.post("/admin/remove_player")
def admin_remove_player(req: SudoRemovePlayer):
    return _check(op().admin_remove_player(req.admin_secret, req.handle,
                                           game_id=req.game_id, occ=req.occ))


@app.post("/admin/delete_venue")
def admin_delete_venue(req: SudoDeleteVenue):
    return _check(op().admin_delete_venue(req.admin_secret, venue_id=req.venue_id, name=req.name))


# ── Generic forward ─────────────────────────────────────────────
@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    inst = op()
    if not hasattr(inst, action) or action.startswith("_"):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(inst, action)
    return {"result": fn(**body) if callable(fn) else fn}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50150")))
