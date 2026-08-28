"""cshare API — the fractional compute marketplace over HTTP.

Serves the protocol card (also at /.well-known/compute.json), the node
marketplace, the share order book, and the hourly rental market. Gateway
routes /cshare/api → :50290 per the mod protocol URL convention.
"""
import os
import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="cshare — fractional compute marketplace", version="2.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

_mod = None


def get_mod():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location(
            "_cshare_mod", MODULE_DIR / "mod.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _mod = module.Mod()
    return _mod


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class DepositRequest(BaseModel):
    address: str
    amount: float = 1000.0


class ListRequest(BaseModel):
    address: str
    name: str
    gpu: str = ""
    gpu_count: int = 1
    vcpus: int = 8
    ram_gb: int = 32
    disk_gb: int = 256
    region: str = ""
    rate_hour: float = 1.0
    total_shares: int = 1000
    share_price: Optional[float] = None
    offer_shares: int = 0
    description: str = ""


class SellRequest(BaseModel):
    address: str
    node_id: str
    shares: int
    price: float


class CancelRequest(BaseModel):
    address: str


class BuyRequest(BaseModel):
    address: str
    order_id: str
    shares: Optional[int] = None


class RentRequest(BaseModel):
    address: str
    node_id: str
    hours: int = 1


class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: Dict[str, Any] = {}


@app.get("/")
def root():
    """Null call returns the protocol card (mod protocol convention)."""
    return get_mod().card()


@app.get("/card")
def card():
    return get_mod().card()


@app.get("/.well-known/compute.json")
def well_known():
    return get_mod().card()


@app.get("/stats")
def stats():
    return get_mod().stats()


# ── marketplace ──────────────────────────────────────────────────────

@app.get("/nodes")
def nodes(q: Optional[str] = None, region: Optional[str] = None,
          gpu: Optional[str] = None, status: Optional[str] = None):
    return _call(get_mod().nodes, q=q, region=region, gpu=gpu, status=status)


@app.post("/nodes")
def list_node(req: ListRequest):
    return _call(get_mod().list_node, **req.model_dump())


@app.get("/nodes/{node_id}")
def node(node_id: str):
    return _call(get_mod().node, node_id)


@app.get("/market")
def market(node_id: Optional[str] = None):
    return _call(get_mod().market, node_id=node_id)


# ── share trading ────────────────────────────────────────────────────

@app.post("/orders")
def sell(req: SellRequest):
    return _call(get_mod().sell, req.address, req.node_id, req.shares,
                 req.price)


@app.post("/orders/{order_id}/cancel")
def cancel(order_id: str, req: CancelRequest):
    return _call(get_mod().cancel, req.address, order_id)


@app.post("/buy")
def buy(req: BuyRequest):
    return _call(get_mod().buy, req.address, req.order_id, req.shares)


# ── rental market ────────────────────────────────────────────────────

@app.post("/rent")
def rent(req: RentRequest):
    return _call(get_mod().rent, req.address, req.node_id, req.hours)


@app.get("/rentals")
def rentals(address: Optional[str] = None, node_id: Optional[str] = None):
    return _call(get_mod().rentals, address=address, node_id=node_id)


# ── ledger / portfolio ───────────────────────────────────────────────

@app.post("/deposit")
def deposit(req: DepositRequest):
    return _call(get_mod().deposit, req.address, req.amount)


@app.get("/balance/{address}")
def balance(address: str):
    return _call(get_mod().balance, address)


@app.get("/portfolio/{address}")
def portfolio(address: str):
    return _call(get_mod().portfolio, address)


@app.post("/demo")
def demo():
    return _call(get_mod().demo)


@app.post("/forward")
def forward(req: ForwardRequest):
    return _call(get_mod().forward, action=req.action, **req.params)


@app.get("/health")
def health():
    return {"status": "ok", "protocol": "compute/1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 50290)))
