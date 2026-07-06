"""
OpenEvent API — FastAPI wrapper over the openevent mod.

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

_inst = None


def oe():
    global _inst
    if _inst is None:
        _inst = m.mod("openevent")()
    return _inst


app = FastAPI(title="OpenEvent API",
              description="One feed for every event, one composer for every platform.",
              version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Request models ──────────────────────────────────────────────
class Price(BaseModel):
    amount: float = 0
    currency: str = "USD"


class CreateEvent(BaseModel):
    title: str
    starts_at: int
    ends_at: Optional[int] = None
    description: str = ""
    venue: str = ""
    address: str = ""
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    online: bool = False
    cover_image: str = ""
    price: Optional[Price] = None
    organizer: str = ""
    timezone: str = "UTC"
    targets: List[str] = []


class ConnectReq(BaseModel):
    platform: str
    fields: Dict[str, Any] = {}


class DisconnectReq(BaseModel):
    platform: str


class BroadcastReq(BaseModel):
    edit_key: str
    targets: List[str] = []


class DeleteReq(BaseModel):
    edit_key: str


class SudoReq(BaseModel):
    admin_secret: str


class SudoDeleteEvent(BaseModel):
    admin_secret: str
    event_id: str


def _check(result):
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Reads ───────────────────────────────────────────────────────
@app.get("/")
def root():
    return oe().health()


@app.get("/health")
def health():
    return oe().health()


@app.get("/status")
def status():
    return oe().status()


@app.get("/platforms")
def platforms():
    return oe().platforms()


@app.get("/cities")
def cities():
    return oe().cities()


@app.get("/discover")
def discover(city: Optional[str] = None, query: str = "", sources: Optional[str] = None,
             limit: int = 50, refresh: bool = False):
    src = [s for s in sources.split(",") if s] if sources else None
    return oe().discover(city=city, query=query, sources=src, limit=limit, refresh=refresh)


@app.get("/events")
def my_events():
    return oe().my_events()


@app.get("/event/{event_id}")
def event(event_id: str):
    return _check(oe().event(event_id))


@app.get("/draft/{event_id}/{platform}")
def draft_for(event_id: str, platform: str):
    return _check(oe().draft_for(event_id, platform))


@app.get("/connections")
def connections():
    return oe().connections()


# ── Create / broadcast ──────────────────────────────────────────
@app.post("/events")
def create_event(req: CreateEvent):
    payload = req.model_dump(exclude_none=True)
    if isinstance(payload.get("price"), dict):
        payload["price"] = {"amount": payload["price"].get("amount", 0),
                            "currency": payload["price"].get("currency", "USD")}
    return _check(oe().create_event(**payload))


@app.post("/event/{event_id}/broadcast")
def broadcast(event_id: str, req: BroadcastReq):
    return _check(oe().broadcast(event_id, req.edit_key, req.targets))


@app.post("/event/{event_id}/delete")
def delete_event(event_id: str, req: DeleteReq):
    return _check(oe().delete_event(event_id, req.edit_key))


# ── Connections (BYOK, off-chain) ───────────────────────────────
@app.post("/connect")
def connect(req: ConnectReq):
    return _check(oe().connect(req.platform, **(req.fields or {})))


@app.post("/disconnect")
def disconnect(req: DisconnectReq):
    return _check(oe().disconnect(req.platform))


# ── Module admin (sudo) ─────────────────────────────────────────
@app.post("/admin/verify")
def admin_verify(req: SudoReq):
    return oe().verify_sudo(req.admin_secret)


@app.post("/admin/delete_event")
def admin_delete_event(req: SudoDeleteEvent):
    return _check(oe().admin_delete_event(req.admin_secret, req.event_id))


# ── Generic forward ─────────────────────────────────────────────
@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    inst = oe()
    if not hasattr(inst, action) or action.startswith("_"):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(inst, action)
    return {"result": fn(**body) if callable(fn) else fn}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "50170")))
