"""liquidai — one front door to every Liquid AI model, wherever it runs.

Three runtimes, one catalog:

    BROWSER   the visitor's tab runs it (transformers.js + WebGPU, ONNX
              weights pulled straight from HuggingFace). This API only says
              which models qualify — no token ever touches the server.
    SERVER    this box runs it (transformers + torch), streamed back over SSE.
    CLOUD     inference.liquid.ai runs it, on the caller's own key.

Chat is one endpoint with a `runtime` field, so switching where a model runs is
a one-word change and nothing else about the call moves.

Reading is open to everyone. Spending this box's compute or the operator's
cloud key needs a signed-in account, and changing what's on the disk needs the
owner — see auth.py for how the three key kinds prove themselves.

Every call that arrives here is written down (ledger.py) and attributed to the
provider that did the work (providers.py) — browser, this box, Liquid's cloud,
or HuggingFace for the catalog and the weights. The same handlers are also
reachable over MCP (mcp.py) at POST /mcp, so an agent gets the module without
learning its REST shape, through the same gate and into the same ledger.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import (
    Body, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile,
)
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

try:  # uvicorn src.api.app:app  (package) — and plain `python app.py` (script)
    from . import (arena, auth, catalog, cloud, keys, ledger, mcp as mcp_rpc,
                   providers, server_rt)
except ImportError:  # pragma: no cover
    import arena, auth, catalog, cloud, keys, ledger, providers, server_rt
    import mcp as mcp_rpc

VERSION = "0.3.0"
START = time.time()

app = FastAPI(
    title="liquidai",
    version=VERSION,
    description="Interface to every Liquid AI (LFM) model — browser, server or cloud",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ── the ledger, wrapped around everything ────────────────────────────
#
# One middleware rather than a line in each handler: "track all the calls" only
# stays true if a route added next month is tracked without anyone remembering
# to. The handlers still speak up — they know the runtime and the model, which
# a path can only guess — but what gets written is decided here.

def _via(request: Request, session: Optional[Dict[str, Any]]) -> str:
    """Which door the call came through: console, MCP, the OpenAI face, a CLI."""
    path = request.url.path
    if path.rstrip("/").endswith("/mcp"):
        return "mcp"
    if path.startswith("/v1"):
        return "openai"
    told = request.headers.get("x-liquidai-via")
    if told:
        return told[:16]
    if (session or {}).get("kind") == "cli":
        return "cli"
    return "api"


@app.middleware("http")
async def _ledger_middleware(request: Request, call_next):
    session = auth.read(request.headers.get("authorization"))
    record = ledger.begin(request.method, request.url.path,
                          via=_via(request, session), caller=session)
    request.state.call = record
    try:
        response = await call_next(request)
    except Exception as e:                       # a crash is a call too
        ledger.end(record, status=500, error=f"{type(e).__name__}: {e}")
        raise
    # A streaming route has barely started when its response object exists —
    # closing the line here would file every generation on this box as "3 ms,
    # no tokens". Those records are closed by the generator instead.
    if not record.get("defer"):
        ledger.end(record, status=response.status_code)
    return response


@app.exception_handler(StarletteHTTPException)
async def _record_http_error(request: Request, exc: StarletteHTTPException):
    """Keep the sentence, not just the number. A 400 here always says why."""
    ledger.tag(getattr(request.state, "call", None), error=str(exc.detail))
    return await http_exception_handler(request, exc)


# ── models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    # `content` is a string or a list of parts — {"type":"text"|"image", …} —
    # so one endpoint carries text and vision turns without a second shape.
    messages: List[Dict[str, Any]]
    model: str                       # HF repo (server) or cloud model id
    runtime: str = "server"          # server | cloud
    max_tokens: int = 512
    temperature: float = 0.3
    top_p: float = 0.95


class EmbedRequest(BaseModel):
    model: str
    texts: List[str]
    normalize: bool = True


class PullRequest(BaseModel):
    repo: str


class LoadRequest(BaseModel):
    repo: str


class KeyRequest(BaseModel):
    provider: str = "cloud"
    key: str = ""


class NonceRequest(BaseModel):
    address: str
    kind: str = "browser"            # browser | evm | bittensor


class VerifyRequest(BaseModel):
    nonce: str
    signature: str
    pubkey: Optional[str] = None     # browser keys carry their own


# ── the gate ─────────────────────────────────────────────────────────

def _guard(token: Optional[str], need: str = "session") -> Dict[str, Any]:
    """403 unless the caller has earned `need`. Returns the session."""
    ok, why, session = auth.gate(token, need)
    if not ok:
        raise HTTPException(403, why)
    return session or {}


# ── info / health ────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "module": "liquidai",
        "version": VERSION,
        "description": "Every Liquid AI model, runnable in the browser, on this box, or in Liquid's cloud",
        "runtimes": ["browser", "server", "cloud"],
        "endpoints": [
            "GET /health", "GET /models", "GET /models/{id}", "GET /runtimes",
            "GET /keys", "POST /keys", "GET /local/models", "POST /local/pull",
            "GET /local/pulls", "POST /local/load", "POST /local/unload",
            "POST /chat", "GET /cloud/models",
            "POST /auth/nonce", "POST /auth/verify", "GET /auth/me",
            "GET /auth/owner", "POST /embed", "POST /transcribe",
            "GET /arena/games", "POST /arena/games", "POST /arena/match",
            "GET /arena/leaderboard",
            "GET /v1/models", "POST /v1/chat/completions", "POST /v1/embeddings",
            "GET /providers", "GET /providers/{id}", "GET /calls",
            "GET /calls/stats", "POST /calls/report", "GET|POST /mcp",
        ],
        "mcp": {"endpoint": "POST /mcp", "tools": len(mcp_rpc.TOOLS)},
        "providers": providers.ids(),
        "sign_in": ["browser", "evm", "bittensor"],
        "modalities": ["text", "vision", "audio", "embed"],
    }


@app.get("/health")
def health():
    try:
        cat = catalog.load()
        cat_state = {"ok": True, "models": cat["count"], "source": cat["source"],
                     "age_sec": round(time.time() - cat["fetched_at"])}
    except Exception as e:
        cat_state = {"ok": False, "error": str(e)}
    day = ledger.stats(24)
    return {
        "ok": True,
        "version": VERSION,
        "uptime_sec": round(time.time() - START),
        "catalog": cat_state,
        "server_runtime": server_rt.available()["ok"],
        "resident": server_rt.loaded(),
        "auth": auth.owner_state(),
        "calls_24h": day["total"],
        "providers_24h": {name: seen["calls"] for name, seen in day["providers"].items()},
        "mcp": {"endpoint": "POST /mcp", "tools": len(mcp_rpc.TOOLS)},
    }


# ── sign in ──────────────────────────────────────────────────────────

@app.post("/auth/nonce")
def auth_nonce(req: NonceRequest):
    """Mint the text the wallet has to sign. Good for five minutes, used once."""
    try:
        return auth.challenge(req.address.strip(), req.kind)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/auth/verify")
def auth_verify(req: VerifyRequest):
    """Trade a signed nonce for a session token."""
    try:
        return auth.verify(req.nonce, req.signature, req.pubkey)
    except ValueError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.get("/auth/me")
def auth_me(authorization: Optional[str] = Header(None)):
    return auth.me(authorization)


@app.get("/auth/owner")
def auth_owner():
    """Who claimed this box — the one thing about auth worth reading anonymously."""
    return auth.owner_state()


# ── catalog ──────────────────────────────────────────────────────────

@app.get("/models")
def models(
    request: Request,
    runtime: Optional[str] = Query(None, description="browser | server | edge"),
    kind: Optional[str] = Query(None, description="text | vision | audio | embed"),
    family: Optional[str] = None,
    q: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 500,
    refresh: bool = False,
):
    """The whole LFM catalog, one row per model rather than per repo."""
    try:
        cat = catalog.load(refresh=refresh)
    except Exception as e:
        raise HTTPException(502, f"catalog unavailable: {e}")
    # The catalog is HuggingFace's traffic when it's fetched and ours when it
    # is served from cache — the ledger should not credit HF for a cache hit.
    ledger.tag(getattr(request.state, "call", None),
               provider="huggingface" if cat["source"] == "huggingface" else "liquidai",
               cache=cat["source"] == "cache")
    rows = cat["models"]
    if runtime:
        rows = [m for m in rows if runtime in m["runtimes"]]
    if kind:
        rows = [m for m in rows if m["kind"] == kind]
    if family:
        rows = [m for m in rows if m["family"].lower() == family.lower()]
    if role:
        rows = [m for m in rows if m.get("role") == role]
    if q:
        needle = q.lower()
        rows = [m for m in rows if needle in m["id"].lower()]
    return {
        "count": len(rows),
        "total": cat["count"],
        "source": cat["source"],
        "fetched_at": cat["fetched_at"],
        "families": sorted({m["family"] for m in cat["models"]}),
        "kinds": sorted({m["kind"] for m in cat["models"]}),
        "roles": sorted({m["role"] for m in cat["models"] if m.get("role")}),
        "models": rows[:limit],
    }


@app.get("/models/{model_id}")
def model_detail(model_id: str, refresh: bool = False):
    entry = catalog.get(model_id, refresh=refresh)
    if not entry:
        raise HTTPException(404, f"no LFM model named {model_id}")
    local = {m["repo"]: m for m in server_rt.local_models()}
    for fmt, variant in entry["variants"].items():
        for repo in variant["repos"]:
            repo["local"] = repo["repo"] in local
    return entry


# ── runtimes ─────────────────────────────────────────────────────────

@app.get("/runtimes")
def runtimes(x_liquid_key: Optional[str] = Header(None)):
    """Which of the three can actually run something right now."""
    key = x_liquid_key or keys.get("cloud")
    return {
        "browser": {
            "runtime": "browser",
            "ok": True,
            "engine": "transformers.js (WebGPU, wasm fallback)",
            "note": "runs in the visitor's tab — weights come from HuggingFace, "
                    "prompts never reach this server",
        },
        "server": server_rt.available(),
        "cloud": cloud.available(key),
    }


@app.get("/cloud/models")
def cloud_models(x_liquid_key: Optional[str] = Header(None)):
    key = x_liquid_key or keys.get("cloud")
    if not key:
        raise HTTPException(401, "no cloud key — POST /keys or send X-Liquid-Key")
    try:
        return {"models": cloud.models(key)}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── keys ─────────────────────────────────────────────────────────────

@app.get("/keys")
def get_keys():
    return keys.status()


@app.post("/keys")
def set_key(req: KeyRequest, authorization: Optional[str] = Header(None)):
    _guard(authorization, "owner")
    return keys.put(req.provider, req.key.strip())


# ── local weights ────────────────────────────────────────────────────

@app.get("/local/models")
def local_models():
    return {"models": server_rt.local_models(), "cache": server_rt.cache_root()}


@app.post("/local/pull")
def local_pull(req: PullRequest, authorization: Optional[str] = Header(None)):
    _guard(authorization, "owner")
    if not req.repo.startswith("LiquidAI/"):
        raise HTTPException(400, "this module only pulls LiquidAI/* repos")
    return server_rt.pull(req.repo)


@app.get("/local/pulls")
def local_pulls(repo: Optional[str] = None):
    return {"pulls": server_rt.pull_status(repo)}


@app.post("/local/load")
def local_load(req: LoadRequest, authorization: Optional[str] = Header(None)):
    _guard(authorization, "owner")
    try:
        return server_rt.load_model(req.repo)
    except server_rt.UnservableModel as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.post("/local/unload")
def local_unload(authorization: Optional[str] = Header(None)):
    _guard(authorization, "owner")
    return server_rt.unload()


# ── chat ─────────────────────────────────────────────────────────────

def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest, request: Request,
         x_liquid_key: Optional[str] = Header(None),
         authorization: Optional[str] = Header(None)):
    """Stream a completion from `runtime`. SSE: token* → done | error.

    `browser` is not routable here on purpose — a browser run has no server
    leg, so asking this endpoint for one is a bug in the caller, not a mode.
    That's also why the sign-in gate sits here and not on the catalog: this is
    the endpoint that spends someone else's electricity.
    """
    _guard(authorization, "session")
    if req.runtime == "browser":
        raise HTTPException(
            400, "runtime='browser' runs in the tab — load the model with "
                 "transformers.js instead of calling this endpoint",
        )
    if not req.messages:
        raise HTTPException(400, "messages is empty")

    if req.runtime == "cloud":
        key = x_liquid_key or keys.get("cloud")
        if not key:
            raise HTTPException(401, "no cloud key — POST /keys or send X-Liquid-Key")
        stream = cloud.generate(key, req.model, req.messages, req.max_tokens,
                                req.temperature, req.top_p)
    elif req.runtime == "server":
        avail = server_rt.available()
        if not avail["ok"]:
            raise HTTPException(503, f"server runtime unavailable: {avail.get('error')}")
        # Resolve before the stream opens. A model this box can't load is a bad
        # request, and a 400 saying so is worth more than a 200 whose first SSE
        # frame is a HuggingFace traceback.
        try:
            server_rt.resolve(req.model)
        except server_rt.UnservableModel as e:
            raise HTTPException(400, str(e))
        stream = server_rt.generate(req.model, req.messages, req.max_tokens,
                                    req.temperature, req.top_p)
    else:
        raise HTTPException(400, f"unknown runtime {req.runtime!r}")

    # The ledger line for a generation is worth having only if it carries what
    # the generation cost, so this one waits for the `done` frame.
    record = getattr(request.state, "call", None)
    ledger.tag(record, kind="inference", provider=req.runtime, model=req.model,
               stream=True, turns=len(req.messages))
    if record is not None:
        record["defer"] = True

    def gen():
        yield _sse({"type": "start", "runtime": req.runtime, "model": req.model})
        failure = None
        try:
            for event in stream:
                if event.get("type") == "done":
                    ledger.tag(record, **ledger.stats_from_done(event))
                elif event.get("type") == "error":
                    failure = event.get("error")
                yield _sse(event)
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
            yield _sse({"type": "error", "error": failure})
        finally:
            # finally, not after the loop: a tab that navigates away mid-answer
            # still spent the tokens it spent, and that run has to be on the
            # board like any other.
            ledger.end(record, status=500 if failure else 200, error=failure)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── the other two modalities ─────────────────────────────────────────

@app.post("/embed")
def embed(req: EmbedRequest, request: Request,
          authorization: Optional[str] = Header(None)):
    """Sentence vectors + the cosine matrix between them, on this box."""
    _guard(authorization, "session")
    ledger.tag(getattr(request.state, "call", None), kind="inference",
               provider="server", model=req.model, turns=len(req.texts))
    if not server_rt.available()["ok"]:
        raise HTTPException(503, "server runtime unavailable")
    try:
        return server_rt.embed(req.model, req.texts, req.normalize)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.post("/transcribe")
async def transcribe(
    request: Request,
    model: str = Form(...),
    language: Optional[str] = Form(None),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """Speech → text. Any container the box can decode (ffmpeg, or plain WAV)."""
    _guard(authorization, "session")
    ledger.tag(getattr(request.state, "call", None), kind="inference",
               provider="server", model=model, modality="audio")
    if not server_rt.available()["ok"]:
        raise HTTPException(503, "server runtime unavailable")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty upload")
    try:
        return server_rt.transcribe(model, raw, language)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


# ── arena ────────────────────────────────────────────────────────────

@app.get("/arena/games")
def arena_games():
    """Every game — the four that ship, plus everything anyone has written."""
    return {"games": arena.games()}


@app.post("/arena/games")
def arena_save(payload: Dict[str, Any] = Body(...),
               authorization: Optional[str] = Header(None)):
    """Write a game. Send an `id` to edit one you already own."""
    session = _guard(authorization, "session")
    try:
        return arena.save(payload, session.get("address", "anon"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/arena/games/{game_id}/fork")
def arena_fork(game_id: str, authorization: Optional[str] = Header(None)):
    """Copy a game into your own list — the way to edit a built-in."""
    session = _guard(authorization, "session")
    try:
        return arena.fork(game_id, session.get("address", "anon"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/arena/games/{game_id}")
def arena_delete(game_id: str, authorization: Optional[str] = Header(None)):
    session = _guard(authorization, "session")
    try:
        return arena.delete(game_id, session.get("address", "anon"),
                            bool(session.get("owner")))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/arena/match")
def arena_match(request: Request, payload: Dict[str, Any] = Body(...),
                authorization: Optional[str] = Header(None),
                x_liquid_key: Optional[str] = Header(None)):
    """Run one or more models through a game and score every round.

    Browser entries are scored by the tab that ran them and posted back to
    /arena/result — this endpoint is for the two runtimes that live here.
    """
    _guard(authorization, "session")
    game_id = payload.get("game") or ""
    models = payload.get("models") or []
    runtime = payload.get("runtime", "server")
    if not models:
        raise HTTPException(400, "no models entered")
    if len(models) > 4:
        raise HTTPException(400, "four entrants a match — the box is not a cluster")
    ledger.tag(getattr(request.state, "call", None), kind="inference",
               provider=runtime, model=", ".join(models)[:120], game=game_id)

    if runtime == "cloud":
        key = x_liquid_key or keys.get("cloud")
        if not key:
            raise HTTPException(401, "no cloud key")

        def runner(model, messages, max_tokens, temperature, top_p):
            return cloud.generate(key, model, messages, max_tokens, temperature, top_p)
    elif runtime == "server":
        if not server_rt.available()["ok"]:
            raise HTTPException(503, "server runtime unavailable")
        runner = server_rt.generate
    else:
        raise HTTPException(400, f"unknown runtime {runtime!r}")

    try:
        results = [arena.play(game_id, model, runner) for model in models]
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"game": game_id, "runtime": runtime,
            "results": sorted(results, key=lambda r: (-r["score"], r["sec_per_round"]))}


@app.post("/arena/result")
def arena_result(request: Request, payload: Dict[str, Any] = Body(...),
                 authorization: Optional[str] = Header(None)):
    """Score a transcript the browser ran, so tab entries reach the board too."""
    _guard(authorization, "session")
    spec = arena.game(payload.get("game") or "")
    if not spec:
        raise HTTPException(404, "no such game")
    answers = payload.get("answers") or []
    if len(answers) != len(spec["rounds"]):
        raise HTTPException(400, f"expected {len(spec['rounds'])} answers")

    rounds = [
        {"prompt": rnd["prompt"], "answer": (ans or "").strip(), "error": None,
         **arena.score_answer(ans, rnd["check"], rnd["expect"])}
        for rnd, ans in zip(spec["rounds"], answers)
    ]
    passed = sum(1 for r in rounds if r["ok"])
    elapsed = float(payload.get("elapsed_sec") or 0)
    result = {
        "id": os.urandom(4).hex(),
        "game": spec["id"], "game_name": spec["name"],
        "model": payload.get("model") or "browser",
        "label": (payload.get("label") or payload.get("model") or "browser").split("/")[-1],
        "runtime": "browser",
        "passed": passed, "total": len(rounds),
        "score": round(100 * passed / len(rounds)),
        "elapsed_sec": round(elapsed, 2),
        "sec_per_round": round(elapsed / len(rounds), 2) if elapsed else 0,
        "at": time.time(), "rounds": rounds,
    }
    arena.record(result)
    ledger.tag(getattr(request.state, "call", None), kind="inference",
               provider="browser", model=result["model"], reported=True,
               game=spec["id"], run_sec=result["elapsed_sec"])
    return result


@app.get("/arena/leaderboard")
def arena_board(game: Optional[str] = None):
    return arena.leaderboard(game)


# ── providers ────────────────────────────────────────────────────────
#
# /runtimes answers "where could this run". /providers answers the operator's
# question instead: who is this module leaning on, is each of them up, what
# does each cost, and which one is actually carrying the traffic.

@app.get("/providers")
def providers_table(window_hours: float = 24.0, refresh: bool = False,
                    x_liquid_key: Optional[str] = Header(None)):
    return providers.table(window_hours, key=x_liquid_key, fresh=refresh)


@app.get("/providers/{provider_id}")
def provider_detail(provider_id: str, window_hours: float = 24.0):
    found = providers.one(provider_id, window_hours)
    if not found:
        raise HTTPException(404, f"no provider {provider_id!r} — "
                                 f"{', '.join(providers.ids())}")
    return found


# ── the call ledger ──────────────────────────────────────────────────

@app.get("/calls")
def calls(provider: Optional[str] = None, model: Optional[str] = None,
          via: Optional[str] = None, path: Optional[str] = None,
          failed_only: bool = False, inference_only: bool = False,
          since_minutes: float = 1440, limit: int = 200):
    """Every call this module has answered, newest first.

    Shapes, not contents: a row carries the provider, the model, who asked and
    what it cost. No prompt text is written here, ever.
    """
    return ledger.query(
        provider=provider, model=model, via=via, path=path,
        ok=(False if failed_only else None), inference_only=inference_only,
        since=time.time() - since_minutes * 60, limit=limit)


@app.get("/calls/stats")
def call_stats(window_hours: float = 24.0):
    """The same ledger, rolled up per provider, model, caller and hour."""
    return ledger.stats(window_hours)


# A tab reporting its own run is the only way a browser generation can ever
# reach this ledger — no prompt, no token and no timing of it passes through
# this server, by design. Which is also why this is the one write here that
# isn't gated: browser chat asks nobody to sign in, so requiring an account to
# report it would leave exactly the runs the module is proudest of unrecorded.
# The cost is that anyone can post a line, so rows carry `reported: true` and
# an IP gets 60 a minute.
_REPORT_SEEN: Dict[str, List[float]] = {}


class ReportRequest(BaseModel):
    model: str
    task: Optional[str] = None
    ok: bool = True
    error: Optional[str] = None
    elapsed_sec: Optional[float] = None
    ttft_sec: Optional[float] = None
    tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    turns: Optional[int] = None
    engine: Optional[str] = None      # webgpu | wasm
    via: str = "console"


@app.post("/calls/report")
def report_call(req: ReportRequest, request: Request,
                authorization: Optional[str] = Header(None)):
    """Record a run that happened in the visitor's tab."""
    who = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _REPORT_SEEN.get(who, []) if now - t < 60]
    if len(recent) >= 60:
        raise HTTPException(429, "60 reports a minute is plenty")
    recent.append(now)
    _REPORT_SEEN[who] = recent

    session = auth.read(authorization) or {}
    row = ledger.note(
        route="POST /calls/report", path="/calls/report", kind="inference",
        provider="browser", reported=True, via=req.via[:16],
        model=req.model[:120], modality=req.task,
        caller=session.get("address", "anon"),
        caller_kind=session.get("kind", "anon"), owner=bool(session.get("owner")),
        ms=round((req.elapsed_sec or 0) * 1000) or None,
        ttft_sec=req.ttft_sec, chunks=req.tokens, prompt_tokens=req.prompt_tokens,
        turns=req.turns, engine=req.engine,
        ok=bool(req.ok), status=200 if req.ok else 500,
        error=(req.error or None) if not req.ok else None,
        tok_per_sec=(round(req.tokens / req.elapsed_sec, 2)
                     if req.tokens and req.elapsed_sec else None),
    )
    # The middleware writes its own line for this POST; that one is the report
    # arriving, this one is the run it describes.
    ledger.tag(getattr(request.state, "call", None), provider="liquidai",
               model=req.model[:120])
    return {"ok": True, "recorded": row["id"]}


@app.delete("/calls")
def clear_calls(authorization: Optional[str] = Header(None)):
    """Drop the ledger — owner only, because it is everyone's traffic history."""
    _guard(authorization, "owner")
    return ledger.purge()


# ── MCP ──────────────────────────────────────────────────────────────
#
# The same handlers, JSON-RPC instead of REST. Mounted on three paths because
# this module is reachable on three: direct on :50460, through the gateway at
# /api/liquidai, and through the console's own rewrite — a client that pasted
# whichever URL it was given should not have to know which one it took.

def _mcp_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/mcp")
@app.get("/_api/mcp")
@app.get("/liquidai/mcp")
def mcp_descriptor(request: Request):
    """What this MCP server is and how to point a client at it."""
    return mcp_rpc.descriptor(_mcp_base(request))


@app.post("/mcp")
@app.post("/_api/mcp")
@app.post("/liquidai/mcp")
async def mcp_endpoint(request: Request):
    """One JSON-RPC message in, one response out."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "MCP expects a JSON-RPC object")
    token = request.headers.get("authorization")
    record = getattr(request.state, "call", None)

    def seen(name: str, args: Dict[str, Any]):
        ledger.tag(record, tool=name, model=args.get("model"))

    if isinstance(payload, list):     # a batch is legal JSON-RPC
        out = [r for r in (mcp_rpc.handle(msg, token, seen) for msg in payload) if r]
        return out or Response(status_code=202)
    ledger.tag(record, provider="liquidai")
    answer = mcp_rpc.handle(payload, token, seen)
    if answer is None:                # a notification gets no body
        return Response(status_code=202)
    return answer


# ── OpenAI-compatible face ───────────────────────────────────────────
#
# Everything above is this module's own shape. This block is the same three
# runtimes wearing the interface every other tool already speaks, so liquidai
# can be dropped into anything that takes a base_url and an API key — the agent
# and dev modules, the OpenAI SDK, curl — without that thing learning anything
# about LFMs. The key is a liquidai session token: sign in, use it as the
# bearer, and the gate is the same one the console goes through.

@app.get("/v1/models")
def v1_models(runtime: str = "server"):
    """Catalog rows that this runtime can actually serve, OpenAI-shaped."""
    try:
        rows = catalog.load()["models"]
    except Exception as e:
        raise HTTPException(502, f"catalog unavailable: {e}")
    field = {"browser": "onnx_repo", "server": "torch_repo"}.get(runtime, "torch_repo")
    if runtime == "cloud":
        try:
            names = cloud.models(keys.get("cloud") or "")
        except Exception as e:
            raise HTTPException(502, str(e))
        return {"object": "list",
                "data": [{"id": n, "object": "model", "owned_by": "liquid"} for n in names]}
    return {
        "object": "list",
        "data": [
            {"id": m[field], "object": "model", "owned_by": "liquid",
             "kind": m["kind"], "params_b": m["params_b"], "runtime": runtime}
            for m in rows if m.get(field)
        ],
    }


@app.post("/v1/chat/completions")
def v1_chat(req: ChatRequest, request: Request,
            authorization: Optional[str] = Header(None),
            x_liquid_key: Optional[str] = Header(None)):
    """OpenAI chat completions. `stream` is not a field here — see /chat.

    Streaming has one implementation, on /chat, in this module's own SSE shape.
    Rather than maintain a second streaming encoder that can drift from it,
    this endpoint drains that one and answers in a single message.
    """
    _guard(authorization, "session")
    if req.runtime == "browser":
        raise HTTPException(400, "runtime='browser' has no server leg")
    if req.runtime == "cloud":
        key = x_liquid_key or keys.get("cloud")
        if not key:
            raise HTTPException(401, "no cloud key")
        stream = cloud.generate(key, req.model, req.messages, req.max_tokens,
                                req.temperature, req.top_p)
    else:
        avail = server_rt.available()
        if not avail["ok"]:
            raise HTTPException(503, f"server runtime unavailable: {avail.get('error')}")
        try:
            server_rt.resolve(req.model)
        except server_rt.UnservableModel as e:
            raise HTTPException(400, str(e))
        stream = server_rt.generate(req.model, req.messages, req.max_tokens,
                                    req.temperature, req.top_p)

    record = getattr(request.state, "call", None)
    ledger.tag(record, kind="inference", provider=req.runtime, model=req.model,
               turns=len(req.messages))
    text, tail = "", {}
    for event in stream:
        if event.get("type") == "token":
            text += event["text"]
        elif event.get("type") == "done":
            tail = event
            ledger.tag(record, **ledger.stats_from_done(event))
        elif event.get("type") == "error":
            raise HTTPException(500, event.get("error", "generation failed"))

    return {
        "id": f"liquidai-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": tail.get("usage") or {"prompt_tokens": tail.get("prompt_tokens")},
        "liquidai": {"runtime": req.runtime, **{k: v for k, v in tail.items()
                                                if k not in ("type", "usage")}},
    }


@app.post("/v1/embeddings")
def v1_embeddings(req: EmbedRequest, request: Request,
                  authorization: Optional[str] = Header(None)):
    _guard(authorization, "session")
    ledger.tag(getattr(request.state, "call", None), kind="inference",
               provider="server", model=req.model, turns=len(req.texts))
    try:
        out = server_rt.embed(req.model, req.texts, req.normalize)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {
        "object": "list",
        "model": req.model,
        "data": [{"object": "embedding", "index": i, "embedding": v}
                 for i, v in enumerate(out["vectors"])],
        "usage": {"prompt_tokens": None},
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 50460)))
