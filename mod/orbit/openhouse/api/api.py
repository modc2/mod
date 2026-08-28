"""
OpenHouse API — FastAPI wrapper over openhouse mod.

Serves the OpenHouse Mod class methods as REST endpoints.
Launched/killed via mod.py serve_api() / kill_api().
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import mod as m

# Lazy singleton
_openhouse = None

def get_openhouse():
    global _openhouse
    if _openhouse is None:
        _openhouse = m.mod('openhouse')()
    return _openhouse


app = FastAPI(
    title="OpenHouse API",
    description="Collective asset ownership platform — fractional property ownership",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ──────────────────────────────────────────────

class PurchaseRequest(BaseModel):
    buyer: str
    share_count: int
    payment: float = 0

class DistributeRequest(BaseModel):
    total_amount: float

class RecordActionRequest(BaseModel):
    action: str
    details: str = ""

class TransferAuthorityRequest(BaseModel):
    new_authority: str

class DeployRequest(BaseModel):
    network: str = "testnet"
    key: Optional[str] = None
    property_details: str = ""
    total_shares: int = 1000
    share_price: float = 0.1
    home_price: float = 0
    monthly_rent: float = 0
    model: Optional[str] = None
    fee_pct: Optional[float] = None
    owner: Optional[str] = None

class TermsRequest(BaseModel):
    model: Optional[str] = None
    fee_pct: Optional[float] = None
    credit_pct: Optional[float] = None
    option_fee_pct: Optional[float] = None
    home_price: Optional[float] = None
    monthly_rent: Optional[float] = None
    owner: Optional[str] = None
    treasury: Optional[str] = None

class ClaimOwnerRequest(BaseModel):
    address: str

class PayRentRequest(BaseModel):
    renter: str
    amount: float
    kind: str = "rent"

class QuoteRequest(BaseModel):
    amount: float
    kind: str = "rent"


# ── Health / Status ─────────────────────────────────────────────

@app.get("/")
def root():
    return get_openhouse().health()

@app.get("/health")
def health():
    return get_openhouse().health()

@app.get("/status")
def status():
    return get_openhouse().status()

@app.post("/status")
def status_post():
    return get_openhouse().status()


# ── Property ────────────────────────────────────────────────────

@app.get("/property")
def property_details():
    return get_openhouse().property()

@app.get("/available_shares")
def available_shares():
    return get_openhouse().available_shares()

@app.get("/share_price")
def share_price():
    return get_openhouse().share_price()

@app.get("/balance")
def balance():
    return get_openhouse().balance()


# ── Rent-to-own ─────────────────────────────────────────────────

@app.get("/models")
def models():
    """Rent-to-own model presets, the 1–5% fee band, and what platforms take."""
    return get_openhouse().models()

@app.get("/terms")
def terms():
    return get_openhouse().terms()

@app.post("/terms")
def set_terms(req: TermsRequest):
    result = get_openhouse().set_terms(**req.model_dump(exclude_none=True))
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/claim_owner")
def claim_owner(req: ClaimOwnerRequest):
    result = get_openhouse().claim_owner(req.address)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/quote")
def quote(req: QuoteRequest):
    result = get_openhouse().quote(req.amount, kind=req.kind)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/pay_rent")
def pay_rent(req: PayRentRequest):
    result = get_openhouse().pay_rent(req.renter, req.amount, kind=req.kind)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.get("/rent_ledger")
def rent_ledger(renter: str = ""):
    return get_openhouse().rent_ledger(renter)

@app.get("/rent_stats")
def rent_stats():
    return get_openhouse().rent_stats()


# ── The landscape ───────────────────────────────────────────────

@app.get("/peers")
def peers(refresh: bool = False):
    """Other on-chain housing projects, sorted by who ends up owning the house."""
    return get_openhouse().peers(refresh=refresh)

@app.get("/compare")
def compare(refresh: bool = False):
    """OpenHouse against the field — including where the field is ahead."""
    return get_openhouse().compare(refresh=refresh)

@app.get("/equity/{address}")
def equity(address: str):
    return get_openhouse().equity(address)


# ── Shareholders ────────────────────────────────────────────────

@app.get("/shareholders")
def shareholders():
    return get_openhouse().shareholders()

@app.post("/shareholders")
def shareholders_post():
    return get_openhouse().shareholders()

@app.get("/shareholder/{address}")
def shareholder(address: str):
    return get_openhouse().shareholder(address)

@app.get("/portfolio/{address}")
def portfolio(address: str):
    return get_openhouse().portfolio(address)


# ── Dividends ───────────────────────────────────────────────────

@app.get("/dividends")
def dividends():
    return get_openhouse().dividends()

@app.post("/dividends")
def dividends_post():
    return get_openhouse().dividends()


# ── Transactions ────────────────────────────────────────────────

@app.post("/purchase")
def purchase(req: PurchaseRequest):
    result = get_openhouse().purchase(req.buyer, req.share_count, req.payment)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/distribute")
def distribute(req: DistributeRequest):
    result = get_openhouse().distribute(req.total_amount)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Governance ──────────────────────────────────────────────────

@app.post("/record_action")
def record_action(req: RecordActionRequest):
    return get_openhouse().record_action(req.action, req.details)

@app.post("/transfer_authority")
def transfer_authority(req: TransferAuthorityRequest):
    result = get_openhouse().transfer_authority(req.new_authority)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/toggle_active")
def toggle_active():
    return get_openhouse().toggle_active()


# ── Source ──────────────────────────────────────────────────────

@app.get("/source")
def source():
    return get_openhouse().source()

@app.get("/code")
def code():
    return get_openhouse().source()


# ── Contract ops ────────────────────────────────────────────────

@app.post("/compile")
def compile():
    result = get_openhouse().compile()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/deploy")
def deploy(req: DeployRequest):
    result = get_openhouse().deploy(
        network=req.network, key=req.key,
        property_details=req.property_details,
        total_shares=req.total_shares,
        share_price=req.share_price,
        home_price=req.home_price,
        monthly_rent=req.monthly_rent,
        model=req.model, fee_pct=req.fee_pct, owner=req.owner,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Generic forward ─────────────────────────────────────────────

@app.post("/forward")
async def forward(request: Request):
    """Generic forward — call any public openhouse method by name."""
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    oh = get_openhouse()
    if not hasattr(oh, action) or action.startswith("_"):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(oh, action)
    if not callable(fn):
        return {"result": fn}
    result = fn(**body)
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "50130"))
    uvicorn.run(app, host="0.0.0.0", port=port)
