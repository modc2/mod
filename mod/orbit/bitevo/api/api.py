import sys
import time
import traceback
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent.parent  # orbit/bitevo/
MOD_ROOT = MODULE_DIR.parent.parent.parent           # ~/mod/

for p in [str(MODULE_DIR), str(MOD_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from bitevo.mod import Mod as Bitevo

app = FastAPI(title="Bitevo API", description="YC startup idea generation & judging subnet")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bitevo: Optional[Bitevo] = None


def get_bitevo() -> Bitevo:
    global _bitevo
    if _bitevo is None:
        _bitevo = Bitevo(local=True)
    return _bitevo


class SimulateRequest(BaseModel):
    n_miners: int = 3
    backends: Optional[List[str]] = None
    epochs: int = 1


class EpochRequest(BaseModel):
    challenge_type: Optional[str] = None


class AddMinerRequest(BaseModel):
    backend: str = "openrouter"
    model: Optional[str] = None


class ScoreIdeaRequest(BaseModel):
    idea: str
    challenge: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok", "module": "bitevo", "time": time.time()}


@app.get("/status")
async def status():
    return {"result": get_bitevo().status()}


@app.post("/simulate")
async def simulate(req: SimulateRequest):
    try:
        result = get_bitevo().simulate(
            n_miners=req.n_miners,
            backends=req.backends,
            epochs=req.epochs,
        )
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.post("/epoch")
async def epoch(req: EpochRequest):
    try:
        result = get_bitevo().epoch(challenge_type=req.challenge_type)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/leaderboard")
async def leaderboard():
    return {"result": get_bitevo().leaderboard()}


@app.get("/results")
async def results(epoch: Optional[int] = Query(None)):
    return {"result": get_bitevo().results(epoch=epoch)}


@app.post("/miner")
async def add_miner(req: AddMinerRequest):
    try:
        result = get_bitevo().add_miner(backend=req.backend, model=req.model)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/challenge")
async def challenge(type: Optional[str] = Query(None)):
    return {"result": get_bitevo().challenge(challenge_type=type)}


@app.post("/score")
async def score_idea(req: ScoreIdeaRequest):
    try:
        result = get_bitevo().score_idea(idea=req.idea, challenge=req.challenge)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")
