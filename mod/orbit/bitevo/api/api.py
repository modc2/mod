import asyncio
import concurrent.futures
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
_progress: dict = {"step": "idle", "epoch": 0, "lines": []}


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
    challenge: Optional[dict] = None


class AddMinerRequest(BaseModel):
    backend: str = "claude"
    model: Optional[str] = None
    payout: Optional[str] = None


class ScoreIdeaRequest(BaseModel):
    idea: str
    challenge: Optional[str] = None
    previous_feedback: Optional[str] = None


class EvolveRequest(BaseModel):
    generations: int = 25
    population: int = 40
    dataset: str = "coupled"
    horizon: int = 10
    max_lag: int = 6
    seed: Optional[int] = None
    fresh: bool = False
    csv: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok", "module": "bitevo", "time": time.time()}


@app.get("/status")
async def status():
    return {"result": get_bitevo().status()}


@app.post("/simulate")
async def simulate(req: SimulateRequest):
    global _progress
    _progress = {"step": "running", "epoch": 0, "lines": []}

    def _cb(msg: str):
        _progress["lines"].append(msg)
        _progress["step"] = msg

    def _run():
        return get_bitevo().simulate(
            n_miners=req.n_miners,
            backends=req.backends,
            epochs=req.epochs,
            progress_callback=_cb,
        )

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = await loop.run_in_executor(pool, _run)
        _progress["step"] = "idle"
        return {"result": result}
    except ValueError as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/zuma")
async def zuma():
    return {"result": get_bitevo().zuma()}


@app.get("/audit")
async def audit(epoch: Optional[int] = Query(None)):
    try:
        return {"result": get_bitevo().audit(epoch=epoch)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/progress")
async def progress():
    return _progress


@app.post("/epoch")
async def epoch(req: EpochRequest):
    global _progress
    _progress = {"step": "running", "epoch": 0, "lines": []}

    def _cb(msg: str):
        _progress["lines"].append(msg)
        _progress["step"] = msg

    def _run():
        return get_bitevo().epoch(
            challenge_type=req.challenge_type,
            challenge=req.challenge,
            progress_callback=_cb,
        )

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = await loop.run_in_executor(pool, _run)
        _progress["step"] = "idle"
        return {"result": result}
    except ValueError as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _progress["step"] = "idle"
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
        result = get_bitevo().add_miner(backend=req.backend, model=req.model,
                                        payout=req.payout)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/evolve")
async def evolve(req: EvolveRequest):
    global _progress
    _progress = {"step": "running", "epoch": 0, "lines": []}

    def _cb(msg: str):
        _progress["lines"].append(msg)
        _progress["step"] = msg

    def _run():
        return get_bitevo().evolve(
            generations=req.generations, population=req.population,
            dataset=req.dataset, horizon=req.horizon, max_lag=req.max_lag,
            seed=req.seed, fresh=req.fresh, csv=req.csv,
            progress_callback=_cb,
        )

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = await loop.run_in_executor(pool, _run)
        _progress["step"] = "idle"
        return {"result": result}
    except ValueError as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/forecast")
async def forecast(horizon: Optional[int] = Query(None), tail: int = Query(60)):
    try:
        return {"result": get_bitevo().forecast(horizon=horizon, tail=tail)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/forecast/ensemble")
async def forecast_ensemble(top_n: int = Query(10), tail: int = Query(60)):
    try:
        return {"result": get_bitevo().ensemble_forecast(top_n=top_n, tail=tail)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@app.get("/population")
async def population(top: int = Query(20)):
    return {"result": get_bitevo().population(top=top)}


@app.get("/datasets")
async def datasets():
    return {"result": get_bitevo().datasets()}


@app.get("/top")
async def top_pitches(limit: int = Query(10)):
    try:
        return {"result": get_bitevo().top_pitches(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/genome")
async def genome_influences(top_k: int = Query(10)):
    try:
        engine = get_bitevo()._get_engine()
        if engine is None:
            return {"error": "no evolved population yet — run evolve first"}
        return {"result": engine.top_influences(top_k=top_k)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/epochs/list")
async def list_epochs():
    try:
        return {"result": get_bitevo().list_epochs()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/backends")
async def backends():
    return {"result": get_bitevo().backends()}


@app.get("/whitepaper")
async def whitepaper():
    from fastapi.responses import PlainTextResponse
    wp = MODULE_DIR / "WHITEPAPER.md"
    if not wp.exists():
        raise HTTPException(status_code=404, detail="whitepaper missing")
    return PlainTextResponse(wp.read_text(), media_type="text/markdown; charset=utf-8")


@app.get("/challenge")
async def challenge(type: Optional[str] = Query(None)):
    return {"result": get_bitevo().challenge(challenge_type=type)}


@app.post("/score")
async def score_idea(req: ScoreIdeaRequest):
    global _progress
    _progress = {"step": "running", "epoch": 0, "lines": []}

    def _cb(msg: str):
        _progress["lines"].append(msg)
        _progress["step"] = msg

    def _run():
        return get_bitevo().score_idea(idea=req.idea, challenge=req.challenge,
                                       previous_feedback=req.previous_feedback,
                                       progress_callback=_cb)

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = await loop.run_in_executor(pool, _run)
        _progress["step"] = "idle"
        return {"result": result}
    except Exception as e:
        _progress["step"] = "idle"
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")
