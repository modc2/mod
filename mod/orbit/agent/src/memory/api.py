"""
memory api - standalone FastAPI service over the agent's Memory subsystem

The memory layer runs as its own process, independent of the agent API:

    uvicorn api:app --host 0.0.0.0 --port 50119

Endpoints:
    GET  /health            - liveness
    GET  /status            - layer counts + session
    GET  /facts             - semantic facts
    GET  /recall?q=...&k=5  - facts scored against a query
    POST /remember          - store a fact {name, content, tags}
    DELETE /facts/{id}      - forget a fact
    GET  /episodes?n=50     - recent episode trail
    POST /observe           - append an episode {tool, params, result?, error?}
    GET  /compile?q=...     - prompt-ready context block
    POST /forward           - mod protocol entry point
"""
import os
import sys
from typing import Optional, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mod import Memory, DEFAULT_PORT  # noqa: E402

app = FastAPI(title="Agent Memory API", version="1.0.0",
              description="Layered agent memory service")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_memory = None

def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory(dir=os.environ.get('AGENT_MEMORY_DIR'))
    return _memory


class RememberRequest(BaseModel):
    name: str
    content: str
    tags: Optional[List[str]] = None

class ObserveRequest(BaseModel):
    tool: str
    params: dict = {}
    result: Optional[str] = None
    error: Optional[str] = None

class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: dict = {}


@app.get("/health")
def health():
    return {"status": "ok", "module": "agent.memory", "port": int(os.environ.get('PORT', DEFAULT_PORT))}

@app.get("/status")
def status():
    return get_memory().status()

@app.get("/facts")
def facts():
    return {"facts": get_memory().facts()}

@app.get("/recall")
def recall(q: str, k: int = 5):
    return {"query": q, "facts": get_memory().recall(q, k=k)}

@app.post("/remember")
def remember(req: RememberRequest):
    return {"fact": get_memory().remember(req.name, req.content, req.tags)}

@app.delete("/facts/{fid}")
def forget(fid: str):
    return get_memory().forget(fid)

@app.get("/episodes")
def episodes(n: int = 50, session: Optional[str] = None):
    return {"episodes": get_memory().episodes(n, session=session)}

@app.post("/observe")
def observe(req: ObserveRequest):
    event = {"tool": req.tool, "params": req.params}
    if req.result is not None:
        event["result"] = req.result
    if req.error is not None:
        event["error"] = req.error
    return {"episode": get_memory().observe(event)}

@app.get("/compile")
def compile_context(q: Optional[str] = None, k: int = 5, episodes: int = 0):
    return {"context": get_memory().compile(q, k=k, episodes=episodes)}

@app.post("/forward")
def forward(req: ForwardRequest):
    try:
        return {"action": req.action, "result": get_memory().forward(req.action, **req.params)}
    except Exception as e:
        return {"action": req.action, "error": str(e)}
