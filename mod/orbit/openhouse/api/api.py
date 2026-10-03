"""
OpenHouse API — FastAPI wrapper over openhouse mod.

Serves the OpenHouse Mod class methods as REST endpoints.
Launched/killed via mod.py serve_api() / kill_api().
"""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
# This directory, so `mcp_server` resolves however this module is loaded —
# uvicorn puts it on the path via --app-dir, a test importing by path does not.
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import mod as m

from mcp_server import build_router as build_mcp_router

# Lazy singleton
_openhouse = None

def get_openhouse():
    global _openhouse
    if _openhouse is None:
        _openhouse = m.mod('openhouse')()
    return _openhouse


def _version():
    try:
        with open(Path(__file__).parent.parent / "config.json") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


app = FastAPI(
    title="OpenHouse API",
    description="Collective asset ownership platform — fractional property ownership",
    version=_version(),
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

class CivicCharterRequest(BaseModel):
    key: str
    name: str = ""
    region: str = ""
    uri: str = ""
    owner: Optional[str] = None

class CivicOverrideRequest(BaseModel):
    action: str
    key: str
    reason: str = ""

class CivicResignRequest(BaseModel):
    key: str


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
    """Rent-to-own model presets, the 0–5% fee band, and what platforms take."""
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


# ── The civic seat ──────────────────────────────────────────────
# A government's standing on this property. The write endpoints mirror the
# contract's seats: the owner charters, only the chartered key overrides or
# resigns. The government's own half lives in civic/server.py, on its box.

@app.get("/civic")
def civic():
    """Who holds the civic seat, what stands, every override on record."""
    return get_openhouse().civic()

@app.post("/civic/charter")
def civic_charter(req: CivicCharterRequest):
    result = get_openhouse().civic_charter(
        req.key, name=req.name, region=req.region, uri=req.uri, owner=req.owner)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/civic/override")
def civic_override(req: CivicOverrideRequest):
    result = get_openhouse().civic_override(req.action, req.key, reason=req.reason)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.post("/civic/resign")
def civic_resign(req: CivicResignRequest):
    result = get_openhouse().civic_resign(req.key)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Testnet examples ────────────────────────────────────────────
# Guided walkthroughs, each played in a throwaway store — running one never
# touches the live testnet data, so GET is honest about it.

@app.get("/examples")
def examples():
    return get_openhouse().examples()

@app.get("/examples/{name}")
def example(name: str):
    result = get_openhouse().example(name)
    if "error" in result and "steps" not in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── The bank rail ───────────────────────────────────────────────
# Any bank: sandbox (testnet, open) · statement files · Open Banking · a
# bank's own MCP server. Real banks need the operator bank key, passed as
# the X-Bank-Key header or `key` in the body/query. Errors are 4xx, never
# 5xx (Cloudflare strips 5xx bodies).

class BankConnectRequest(BaseModel):
    kind: str = "sandbox"
    name: str = ""
    config: dict = {}
    key: str = ""

class BankImportRequest(BaseModel):
    content: str
    connection: str = ""
    format: str = "auto"
    key: str = ""

class BankReceiveRequest(BaseModel):
    amount: float
    reference: str = ""
    from_name: str = ""
    from_iban: str = ""
    account: str = ""
    connection: str = ""
    date: int = 0

class BankLinkRequest(BaseModel):
    address: str
    payer_iban: str = ""
    payer_name: str = ""
    kind: str = "rent"
    key: str = ""

class BankReconcileRequest(BaseModel):
    connection: str = ""
    account: str = ""
    since: int = 0
    dry_run: bool = False
    rate: Optional[float] = None
    key: str = ""

class BankPayRequest(BaseModel):
    amount: float
    to_iban: str
    to_name: str = ""
    account: str = ""
    connection: str = ""
    currency: str = ""
    reference: str = ""
    key: str = ""

class BankDisconnectRequest(BaseModel):
    connection: str
    key: str = ""


def _bk(request: Request, key: str = "") -> str:
    return key or request.headers.get("x-bank-key", "")

def _bank_out(result):
    if isinstance(result, dict) and "error" in result:
        code = 403 if "key=" in result["error"] else 400
        raise HTTPException(status_code=code, detail=result["error"])
    return result

@app.get("/bank")
def bank_status():
    return get_openhouse().bank_status()

@app.get("/bank/kinds")
def bank_kinds():
    return get_openhouse().bank_kinds()

@app.get("/bank/connections")
def bank_connections(request: Request, key: str = ""):
    return get_openhouse().bank_connections(key=_bk(request, key))

@app.post("/bank/connect")
def bank_connect(req: BankConnectRequest, request: Request):
    return _bank_out(get_openhouse().bank_connect(
        req.kind, name=req.name, config=req.config, key=_bk(request, req.key)))

@app.post("/bank/disconnect")
def bank_disconnect(req: BankDisconnectRequest, request: Request):
    return _bank_out(get_openhouse().bank_disconnect(req.connection, key=_bk(request, req.key)))

@app.get("/bank/accounts")
def bank_accounts(request: Request, connection: str = "", key: str = ""):
    return _bank_out(get_openhouse().bank_accounts(connection, key=_bk(request, key)))

@app.get("/bank/transactions")
def bank_transactions(request: Request, connection: str = "", account: str = "",
                      since: int = 0, limit: int = 100, key: str = ""):
    return _bank_out(get_openhouse().bank_transactions(
        connection, account=account, since=since, limit=limit, key=_bk(request, key)))

@app.post("/bank/import")
def bank_import(req: BankImportRequest, request: Request):
    return _bank_out(get_openhouse().bank_import(
        req.content, connection=req.connection, format=req.format, key=_bk(request, req.key)))

@app.post("/bank/receive")
def bank_receive(req: BankReceiveRequest):
    return _bank_out(get_openhouse().bank_receive(**req.model_dump()))

@app.get("/bank/reference/{address}")
def bank_reference(address: str):
    return _bank_out(get_openhouse().bank_reference(address))

@app.post("/bank/link")
def bank_link(req: BankLinkRequest, request: Request):
    return _bank_out(get_openhouse().bank_link(
        req.address, payer_iban=req.payer_iban, payer_name=req.payer_name,
        kind=req.kind, key=_bk(request, req.key)))

@app.get("/bank/links")
def bank_links(request: Request, key: str = ""):
    return get_openhouse().bank_links(key=_bk(request, key))

@app.post("/bank/reconcile")
def bank_reconcile(req: BankReconcileRequest, request: Request):
    return _bank_out(get_openhouse().bank_reconcile(
        req.connection, account=req.account, since=req.since, dry_run=req.dry_run,
        rate=req.rate, key=_bk(request, req.key)))

@app.post("/bank/pay")
def bank_pay(req: BankPayRequest, request: Request):
    return _bank_out(get_openhouse().bank_pay(
        req.amount, req.to_iban, to_name=req.to_name, account=req.account,
        connection=req.connection, currency=req.currency, reference=req.reference,
        key=_bk(request, req.key)))


# ── The landscape ───────────────────────────────────────────────

@app.get("/peers")
def peers(refresh: bool = False):
    """Other on-chain housing projects, sorted by who ends up owning the house."""
    return get_openhouse().peers(refresh=refresh)

@app.get("/compare")
def compare(refresh: bool = False):
    """OpenHouse against the field — including where the field is ahead."""
    return get_openhouse().compare(refresh=refresh)

@app.get("/fx")
def fx(refresh: bool = False):
    """ETH quoted in fiat currencies, for display. Cached; never errors."""
    return get_openhouse().fx(refresh=refresh)

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


# ── MCP ─────────────────────────────────────────────────────────

# POST /mcp — the same protocol as a set of JSON-RPC tools, for LLM agents.
# The router calls get_openhouse() per tool call, so it reads exactly what
# the REST endpoints above read.
app.include_router(build_mcp_router(get_openhouse, app.version))


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
    port = int(os.getenv("PORT", "50132"))
    uvicorn.run(app, host="0.0.0.0", port=port)
