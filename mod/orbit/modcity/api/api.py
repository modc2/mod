"""
ModCity API — FastAPI wrapper over the modcity mod.

Buildings are stored/loaded through the localfs mod (content-addressed CIDs);
bricks can be forged and shared; designs are private by default.
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
from typing import Optional, List, Dict, Any
import mod as m

_mc = None

def mc():
    global _mc
    if _mc is None:
        _mc = m.mod('modcity')()
    return _mc


app = FastAPI(title="ModCity API",
              description="Modular housing & cities — forge bricks, snap buildings like LEGO, in any architecture style.",
              version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Models ──────────────────────────────────────────────────────
class EstimateRequest(BaseModel):
    modules: Optional[List[str]] = None
    cells: Optional[List[Dict[str, Any]]] = None
    style: str = "brownstone"
    constraints: Optional[Dict[str, Any]] = None

class SaveDesignRequest(BaseModel):
    name: str = ""
    owner: str = ""
    cells: List[Dict[str, Any]]
    style: str = "brownstone"
    design_id: Optional[str] = None
    description: str = ""
    public: bool = False
    constraints: Optional[Dict[str, Any]] = None

class SaveCityRequest(BaseModel):
    name: str = ""
    owner: str = ""
    designs: Optional[List[str]] = None
    layout: Optional[List[Dict[str, Any]]] = None
    city_id: Optional[str] = None
    description: str = ""
    public: bool = False

class BrickRequest(BaseModel):
    name: str = ""
    category: str = "living"
    color: str = "#f4a261"
    price: float = 18000
    carbon_kg: float = 2000
    lead_days: int = 21
    glass: bool = False
    blurb: str = ""
    owner: str = ""
    public: bool = False
    brick_id: Optional[str] = None

class PublishRequest(BaseModel):
    owner: Optional[str] = None
    public: bool = True

class CopyRequest(BaseModel):
    owner: str = ""
    name: Optional[str] = None

class ImportRequest(BaseModel):
    doc: Dict[str, Any]
    owner: str = ""
    public: bool = False
    name: Optional[str] = None


def _guard(result):
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Info / health ───────────────────────────────────────────────
@app.get("/")
def root(): return mc().forward()
@app.get("/info")
def info(): return mc().forward()
@app.get("/health")
def health(): return mc().health()
@app.get("/status")
def status(): return mc().status()
@app.post("/status")
def status_post(): return mc().status()


# ── Catalog & styles ────────────────────────────────────────────
@app.get("/catalog")
def catalog(owner: Optional[str] = None): return mc().catalog(owner=owner)
@app.get("/module/{module_id}")
def module_detail(module_id: str): return _guard(mc().module_detail(module_id))
@app.get("/styles")
def styles(): return mc().styles()
@app.get("/style/{style_id}")
def style(style_id: str): return _guard(mc().style(style_id))


# ── Bricks (the forge + community library) ──────────────────────
@app.get("/bricks")
def bricks(owner: Optional[str] = None, include_public: bool = True, limit: int = 200):
    return mc().bricks(owner=owner, include_public=include_public, limit=limit)

@app.get("/brick/{brick_id}")
def brick(brick_id: str): return _guard(mc().brick(brick_id))

@app.post("/brick")
def create_brick(req: BrickRequest):
    return _guard(mc().create_brick(
        name=req.name, category=req.category, color=req.color, price=req.price,
        carbon_kg=req.carbon_kg, lead_days=req.lead_days, glass=req.glass,
        blurb=req.blurb, owner=req.owner, public=req.public, brick_id=req.brick_id))

@app.post("/brick/{brick_id}/publish")
def publish_brick(brick_id: str, req: PublishRequest):
    return _guard(mc().publish_brick(brick_id, owner=req.owner, public=req.public))

@app.delete("/brick/{brick_id}")
def delete_brick(brick_id: str, owner: Optional[str] = None):
    return _guard(mc().delete_brick(brick_id, owner=owner))


# ── Estimator (with constraints/compliance) ─────────────────────
@app.post("/estimate")
def estimate(req: EstimateRequest):
    return mc().estimate(modules=req.modules, style=req.style,
                         cells=req.cells, constraints=req.constraints)


# ── Designs (private by default) ────────────────────────────────
@app.get("/designs")
def designs(owner: Optional[str] = None, limit: int = 100, scope: str = "public"):
    return mc().designs(owner=owner, limit=limit, scope=scope)

@app.get("/my/designs")
def my_designs(owner: str, limit: int = 100):
    return mc().my_designs(owner, limit=limit)

@app.get("/design/{design_id}")
def design(design_id: str): return _guard(mc().design(design_id))

@app.post("/design")
def save_design(req: SaveDesignRequest):
    return _guard(mc().save_design(name=req.name, owner=req.owner, cells=req.cells,
                                   style=req.style, design_id=req.design_id,
                                   description=req.description, public=req.public,
                                   constraints=req.constraints))

@app.post("/design/{design_id}/publish")
def publish_design(design_id: str, req: PublishRequest):
    return _guard(mc().publish_design(design_id, owner=req.owner, public=req.public))

@app.post("/design/{design_id}/copy")
def copy_design(design_id: str, req: CopyRequest):
    return _guard(mc().copy_design(design_id, owner=req.owner, name=req.name))

@app.delete("/design/{design_id}")
def delete_design(design_id: str, owner: Optional[str] = None):
    return _guard(mc().delete_design(design_id, owner=owner))


# ── Export / share / import (via localfs CIDs) ──────────────────
@app.get("/design/{design_id}/export")
def export_design(design_id: str): return _guard(mc().export_design(design_id))

@app.get("/load/{cid}")
def load_cid(cid: str): return _guard(mc().load_cid(cid))

@app.post("/import")
def import_design(req: ImportRequest):
    return _guard(mc().import_design(doc=req.doc, owner=req.owner,
                                     public=req.public, name=req.name))


# ── Cities ──────────────────────────────────────────────────────
@app.get("/cities")
def cities(owner: Optional[str] = None, limit: int = 100):
    return mc().cities(owner=owner, limit=limit)
@app.get("/city/{city_id}")
def city(city_id: str): return _guard(mc().city(city_id))
@app.post("/city")
def save_city(req: SaveCityRequest):
    return _guard(mc().save_city(name=req.name, owner=req.owner, designs=req.designs,
                                 layout=req.layout, city_id=req.city_id,
                                 description=req.description, public=req.public))
@app.delete("/city/{city_id}")
def delete_city(city_id: str, owner: Optional[str] = None):
    return _guard(mc().delete_city(city_id, owner=owner))


# ── Source / generic forward ────────────────────────────────────
@app.get("/source")
def source(): return mc().source()

@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    action = body.pop("action", body.pop("fn", None))
    if not action:
        raise HTTPException(status_code=400, detail="action or fn required")
    inst = mc()
    if not hasattr(inst, action) or action.startswith("_"):
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    fn = getattr(inst, action)
    if not callable(fn):
        return {"result": fn}
    return {"result": fn(**body)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "50140"))
    uvicorn.run(app, host="0.0.0.0", port=port)
