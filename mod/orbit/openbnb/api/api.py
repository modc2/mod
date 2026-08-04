"""
OpenBnB API — FastAPI wrapper over the openbnb mod.

Public routes are the market (browse, quote, book). Everything under /owner is
gated by the module owner's secret, sent either as ``X-Owner-Key`` (so the whole
console is scriptable with curl) or as ``owner_key`` in the body.

Launched/killed via mod.py serve_api() / kill_api().
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import mod as m

_openbnb = None


def ob():
    global _openbnb
    if _openbnb is None:
        _openbnb = m.mod("openbnb")()
    return _openbnb


app = FastAPI(title="OpenBnB API",
              description="Open short-stay marketplace — programmable by its owner.",
              version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


def _check(result):
    if isinstance(result, dict) and result.get("error"):
        code = 403 if result["error"] == "owner key required" else 400
        raise HTTPException(status_code=code, detail=result["error"])
    return result


def _owner(body_key: Optional[str], header_key: Optional[str]) -> str:
    """Owner secret from the body or the X-Owner-Key header."""
    return (body_key or header_key or "").strip()


# ── Request models ──────────────────────────────────────────────
class CreateListing(BaseModel):
    host: str
    title: str
    city: str
    price: float
    kind: str = "entire_place"
    guests: int = 2
    bedrooms: int = 1
    beds: int = 1
    baths: float = 1
    amenities: Optional[List[str]] = None
    notes: str = ""
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    photos: Optional[List[str]] = None
    cleaning_fee: float = 0
    min_nights: int = 0
    max_nights: int = 0
    instant_book: Optional[bool] = None
    host_wallet: str = ""
    owner_key: str = ""


class EditListing(BaseModel):
    host_key: str
    fields: Dict[str, Any] = {}


class SetStatus(BaseModel):
    host_key: str
    status: str


class BlockDates(BaseModel):
    host_key: str
    dates: List[str]
    unblock: bool = False


class QuoteReq(BaseModel):
    listing_id: str
    checkin: str
    checkout: str
    guests: int = 1
    guest: str = ""
    explain: bool = False


class BookReq(BaseModel):
    listing_id: str
    guest: str
    checkin: str
    checkout: str
    guests: int = 1
    guest_wallet: str = ""
    note: str = ""
    payment: str = ""


class HostAction(BaseModel):
    host_key: str
    reason: str = ""


class CancelReq(BaseModel):
    guest: str = ""
    host_key: str = ""


class OwnerReq(BaseModel):
    owner_key: str = ""


class PolicyPatch(OwnerReq):
    patch: Dict[str, Any] = {}


class PolicyReset(OwnerReq):
    key: Optional[str] = None


class RuleReq(OwnerReq):
    name: str = "rule"
    when: str = ""
    then: Dict[str, Any] = {}
    enabled: bool = True


class RuleUpdate(OwnerReq):
    name: Optional[str] = None
    when: Optional[str] = None
    then: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class RuleMove(OwnerReq):
    direction: str = "up"


class RuleTest(OwnerReq):
    when: str
    facts: Dict[str, Any] = {}


class HookReq(OwnerReq):
    url: str
    events: Optional[List[str]] = None
    secret: str = ""


class BookingStatus(OwnerReq):
    status: str


class ImportState(OwnerReq):
    state: Dict[str, Any] = {}
    merge: bool = False


# ── Public: the market ──────────────────────────────────────────
@app.get("/")
def root():
    return ob().health()


@app.get("/health")
def health():
    return ob().health()


@app.get("/status")
def status():
    return ob().status()


@app.get("/policy")
def policy():
    """The live policy document — public, so guests can see the house rules."""
    return ob().policy()


@app.get("/rules")
def rules():
    """The owner's rules, public and readable: no hidden pricing."""
    return ob().rules()


@app.get("/kinds")
def kinds():
    return ob().kinds()


@app.get("/amenities")
def amenities():
    return ob().amenities()


@app.get("/cities")
def cities():
    return ob().cities()


@app.get("/listings")
def listings(city: Optional[str] = None, kind: Optional[str] = None,
             guests: int = 0, host: Optional[str] = None, include_all: bool = False):
    return ob().listings(city=city, kind=kind, guests=guests, host=host,
                         include_all=include_all)


@app.get("/listing/{listing_id}")
def listing(listing_id: str):
    return _check(ob().listing(listing_id))


@app.get("/listing/{listing_id}/calendar")
def calendar(listing_id: str):
    return _check(ob().calendar(listing_id))


@app.post("/listings")
def create_listing(req: CreateListing):
    return _check(ob().create_listing(**req.model_dump()))


@app.post("/listing/{listing_id}/edit")
def edit_listing(listing_id: str, req: EditListing):
    return _check(ob().edit_listing(listing_id, req.host_key, **(req.fields or {})))


@app.post("/listing/{listing_id}/status")
def set_status(listing_id: str, req: SetStatus):
    return _check(ob().set_status(listing_id, req.host_key, req.status))


@app.post("/listing/{listing_id}/block")
def block_dates(listing_id: str, req: BlockDates):
    return _check(ob().block_dates(listing_id, req.host_key, req.dates, unblock=req.unblock))


@app.post("/quote")
def quote(req: QuoteReq):
    return _check(ob().quote(req.listing_id, req.checkin, req.checkout,
                             guests=req.guests, guest=req.guest, explain=req.explain))


@app.post("/book")
def book(req: BookReq):
    return _check(ob().book(**req.model_dump()))


@app.get("/bookings")
def bookings(guest: str = "", host: str = "", listing_id: str = "", status: str = ""):
    return ob().bookings(guest=guest, host=host, listing_id=listing_id, status=status)


@app.get("/booking/{booking_id}")
def booking(booking_id: str):
    return _check(ob().booking(booking_id))


@app.post("/booking/{booking_id}/approve")
def approve(booking_id: str, req: HostAction):
    return _check(ob().approve_booking(booking_id, req.host_key))


@app.post("/booking/{booking_id}/decline")
def decline(booking_id: str, req: HostAction):
    return _check(ob().decline_booking(booking_id, req.host_key, reason=req.reason))


@app.post("/booking/{booking_id}/cancel")
def cancel(booking_id: str, req: CancelReq):
    return _check(ob().cancel_booking(booking_id, guest=req.guest, host_key=req.host_key))


@app.get("/booking/{booking_id}/payment")
def payment_requirements(booking_id: str):
    return _check(ob().payment_requirements(booking_id))


# ── Owner: the programmable surface ─────────────────────────────
@app.post("/owner/verify")
def owner_verify(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return ob().verify_owner(_owner(req.owner_key, x_owner_key))


@app.post("/owner/state")
def owner_state(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().owner_state(_owner(req.owner_key, x_owner_key)))


@app.get("/owner/policy_schema")
def policy_schema():
    """Public: the shape of every knob, so a client can render the console."""
    return ob().policy_schema()


@app.get("/owner/sandbox")
def sandbox():
    """Public: the rule language — facts, effects, events, sample values."""
    return ob().sandbox()


@app.post("/owner/policy")
def set_policy(req: PolicyPatch, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().set_policy(_owner(req.owner_key, x_owner_key), patch=req.patch))


@app.post("/owner/policy/reset")
def reset_policy(req: PolicyReset, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().reset_policy(_owner(req.owner_key, x_owner_key), key=req.key))


@app.post("/owner/rules")
def add_rule(req: RuleReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().add_rule(_owner(req.owner_key, x_owner_key), req.name,
                                req.when, req.then, enabled=req.enabled))


@app.post("/owner/rules/{rule_id}")
def update_rule(rule_id: str, req: RuleUpdate, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().update_rule(_owner(req.owner_key, x_owner_key), rule_id,
                                   name=req.name, when=req.when, then=req.then,
                                   enabled=req.enabled))


@app.post("/owner/rules/{rule_id}/delete")
def delete_rule(rule_id: str, req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().delete_rule(_owner(req.owner_key, x_owner_key), rule_id))


@app.post("/owner/rules/{rule_id}/move")
def move_rule(rule_id: str, req: RuleMove, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().move_rule(_owner(req.owner_key, x_owner_key), rule_id,
                                 direction=req.direction))


@app.post("/owner/rules/test")
def test_rule(req: RuleTest, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().test_rule(_owner(req.owner_key, x_owner_key), req.when,
                                 facts=req.facts))


@app.post("/owner/hooks/list")
def list_hooks(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().hooks(_owner(req.owner_key, x_owner_key)))


@app.post("/owner/hooks")
def add_hook(req: HookReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().add_hook(_owner(req.owner_key, x_owner_key), req.url,
                                events=req.events, secret=req.secret))


@app.post("/owner/hooks/{hook_id}/delete")
def delete_hook(hook_id: str, req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().delete_hook(_owner(req.owner_key, x_owner_key), hook_id))


@app.post("/owner/deliveries")
def deliveries(req: OwnerReq, x_owner_key: Optional[str] = Header(None), limit: int = 25):
    return _check(ob().hook_deliveries(_owner(req.owner_key, x_owner_key), limit=limit))


@app.post("/owner/listing/{listing_id}/delete")
def owner_delete_listing(listing_id: str, req: OwnerReq,
                         x_owner_key: Optional[str] = Header(None)):
    return _check(ob().owner_delete_listing(_owner(req.owner_key, x_owner_key), listing_id))


@app.post("/owner/booking/{booking_id}/delete")
def owner_delete_booking(booking_id: str, req: OwnerReq,
                         x_owner_key: Optional[str] = Header(None)):
    return _check(ob().owner_delete_booking(_owner(req.owner_key, x_owner_key), booking_id))


@app.post("/owner/booking/{booking_id}/status")
def owner_booking_status(booking_id: str, req: BookingStatus,
                         x_owner_key: Optional[str] = Header(None)):
    return _check(ob().owner_set_booking_status(_owner(req.owner_key, x_owner_key),
                                                booking_id, req.status))


@app.post("/owner/export")
def export_state(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().export_state(_owner(req.owner_key, x_owner_key)))


@app.post("/owner/import")
def import_state(req: ImportState, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().import_state(_owner(req.owner_key, x_owner_key), req.state,
                                    merge=req.merge))


@app.post("/owner/seed_demo")
def seed_demo(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().seed_demo(_owner(req.owner_key, x_owner_key)))


@app.post("/owner/wipe_demo")
def wipe_demo(req: OwnerReq, x_owner_key: Optional[str] = Header(None)):
    return _check(ob().wipe_demo(_owner(req.owner_key, x_owner_key)))


# ── Generic forward — any mod fn over HTTP ──────────────────────
@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    inst = ob()
    if action.startswith("_") or not hasattr(inst, action):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(inst, action)
    return {"result": fn(**body) if callable(fn) else fn}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50370")))
