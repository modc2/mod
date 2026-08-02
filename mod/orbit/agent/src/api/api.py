"""
agent api - thin FastAPI gateway over mod.forward()

All logic lives in agent/mod.py. The API just dispatches to forward().

Endpoints:
    GET  /health       - health check
    GET  /status       - module status
    GET  /skills       - list skills + schemas
    GET  /schema       - get skill schemas for LLM
    GET  /agents       - list agent personas
    GET  /agents/{name} - get agent config
    POST /agents       - create agent    PUT /agents/{name}  DELETE /agents/{name}
    POST /agents/import - install a shared agent from its localfs CID
    GET  /harnesses    - external agent CLIs (claude code, codex) + availability
    GET  /chains       - list chain presets
    GET  /library      - unified index: prompts + skills + memory + agents (q/kind/tag filters)
    POST /library/upload      - upload a file as a prompt/skill/memory note/agent
    POST /library/upload/file - the same, as a multipart file post
    POST /library/import      - install anything from a shared localfs CID
    GET  /library/formats     - what an upload may look like + docs/uploads.md
    GET  /prompts      - prompt library      POST /prompts  DELETE /prompts/{id}
    POST /prompts/import - install a shared prompt from its localfs CID
    GET  /memory       - memory notes        POST /memory   DELETE /memory/{id}
    POST /memory/import - install a shared memory note from its localfs CID
    GET  /discover     - scan GitHub/npm/MCP/Glama/awesome-lists for skills (q/sources/kind)
    GET  /discover/sources - the source catalog + GitHub token state
    GET  /discover/item?id= - full record for one result (SKILL.md paths, readme)
    GET  /discover/doc?id=  - preview the document an install would add
    POST /discover/install  - install a scanned result into the library
    POST /discover/token    - owner: store a GitHub token (lifts rate limits)
    GET  /skills/installed  - external skills installed from the aggregator
    POST /skills/import     - install an external skill from its CID
    DELETE /skills/installed/{id} - uninstall one
    GET  /conversations - the caller's saved console conversations (localfs-pinned)
    POST /conversations - upsert a conversation  DELETE /conversations/{id}
    POST /conversations/import - restore a shared conversation from its CID
    GET  /vaults       - the caller's key-value vaults (store-module-backed)
    POST /vaults       - create a vault          DELETE /vaults/{name}
    GET  /vaults/{name}?reveal= - entries (private values masked unless reveal)
    POST /vaults/{name}/entries - upsert {entry, value, private}
    DELETE /vaults/{name}/entries/{entry} - remove one entry
    GET  /vaults/public?address=&name= - anyone: a vault's public entries
    GET  /toolboxes    - skill bundles       POST /toolboxes  DELETE /toolboxes/{name}
    POST /toolboxes/{name}/snap   - snap a bundle onto the agent (unsnap to detach)
    POST /toolboxes/unsnap        - detach everything, back to the full tool set
    GET  /tools        - every callable tool: shipped skills + custom shell tools
    POST /tools        - host: create/update a custom tool  DELETE /tools/{name}
    POST /tools/{name}/run - host: execute one custom tool (the console's test run)
    POST /tools/select - host: pin the loadout to an exact list (null = toolboxes)
    GET  /memory/state - memory subsystem layers (working/episodic/semantic)
    GET  /memory/recall?q= - facts scored against a query
    POST /memory/remember  - store a durable fact   DELETE /memory/facts/{id}
    GET  /memory/episodes  - step trail    POST /memory/serve - own process :50119
    GET  /whoami       - resolve a signed token to an address + role
    GET  /credits      - deposit info + caller's credit balance/history
    POST /credits/deposit - verify a USDT/USDC tx hash, credit the on-chain sender
    POST /credits/grant   - owner: adjust an account's credits (± amount)
    GET  /tasks        - server-side task registry (running + recent runs)
    GET  /tasks/{id}   - one task with its step trace
    GET  /tasks/{id}/images - thumbnails of the images attached to that run
    POST /forward      - mod protocol entry point
    POST /run          - run the full agent loop
    POST /run/stream   - run the agent loop, streaming steps live (SSE)
    POST /skills/run   - run a single skill

Usage:
    uvicorn api:app --host 0.0.0.0 --port 50117 --reload
"""
import os
import sys
import json
import time
import uuid
import queue
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# resolve paths: api.py is at src/api/api.py
# paths MUST be normalized — the mod framework prefix-matches sys.path entries
# against absolute paths, and an unnormalized entry silently breaks its import
src_dir = os.path.join(os.path.dirname(__file__), '..')             # src/
module_root = os.path.abspath(os.path.join(src_dir, '..'))          # orbit/agent/
mod_root = os.path.abspath(os.path.join(module_root, '..', '..', '..'))  # mod framework root
sys.path.insert(0, module_root)
sys.path.insert(0, mod_root)

# the module's config.json is the one place a version lives — a literal here
# just drifts, and /health is what callers use to tell deploys apart
try:
    with open(os.path.join(module_root, 'config.json')) as f:
        VERSION = json.load(f).get('version', '0.0.0')
except Exception:
    VERSION = '0.0.0'

app = FastAPI(title="Agent API", version=VERSION, description="Autonomous coding agent API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── request models ───────────────────────────────────────────────────

class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: dict = {}
    key: Optional[str] = None

class RunRequest(BaseModel):
    query: str
    model: str = "anthropic/claude-opus-5"
    provider: Optional[str] = None
    steps: int = 10
    skills: Optional[List[str]] = None
    toolbox: Optional[str] = None           # toolbox name to snap on for this run
    toolboxes: Optional[List[str]] = None   # or several — skills = union of boxes
    temperature: float = 0.0
    safety: bool = False
    free: bool = False
    agent: Optional[str] = None
    agent_type: Optional[str] = None
    chain: Optional[List[dict]] = None
    prompt: Optional[str] = None          # system prompt override (library prompt or free text)
    memory_ids: Optional[List[str]] = None  # library memory note ids injected as context
    skill_ids: Optional[List[str]] = None   # installed external skill ids injected as context
    images: Optional[List[str]] = None      # pasted images (data: URLs) — needs a vision model
    thumbs: Optional[List[str]] = None      # tiny copies of the same images, for the task registry
    key: Optional[str] = None

class SkillRunRequest(BaseModel):
    name: str
    params: dict = {}
    key: Optional[str] = None

class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    goal: str = ""
    icon: str = ">_"
    skills: Optional[List[str]] = None
    model: Optional[str] = None
    harness: Optional[str] = None   # 'claude' | 'codex' — run on that CLI instead
    key: Optional[str] = None

class AgentUpdateRequest(BaseModel):
    description: Optional[str] = None
    goal: Optional[str] = None
    icon: Optional[str] = None
    skills: Optional[List[str]] = None
    model: Optional[str] = None
    harness: Optional[str] = None
    clear_skills: bool = False   # explicit: reset to all skills
    clear_model: bool = False    # explicit: reset to default model
    clear_harness: bool = False  # explicit: back to this module's own loop
    key: Optional[str] = None

class GrantRequest(BaseModel):
    address: str
    actions: Optional[List[str]] = None  # default: ['run', 'skill']
    key: Optional[str] = None  # owner auth token

class RevokeRequest(BaseModel):
    address: str
    key: Optional[str] = None

class AgentRegisterRequest(BaseModel):
    name: str
    backend: str = "offchain"
    key: Optional[str] = None

class PromptRequest(BaseModel):
    name: str
    text: str
    description: str = ""
    tags: Optional[List[str]] = None
    id: Optional[str] = None
    key: Optional[str] = None

class PromptImportRequest(BaseModel):
    cid: str
    key: Optional[str] = None

class ApiKeyRequest(BaseModel):
    api_key: str
    provider: str = "openrouter"
    passphrase: Optional[str] = None  # set -> key saved as an encrypted vault file
    remember: bool = True             # keep it unlocked on this server across restarts
    key: Optional[str] = None

class VaultRequest(BaseModel):
    provider: str = "openrouter"
    passphrase: Optional[str] = None
    remember: bool = True
    key: Optional[str] = None

class MemoryNoteRequest(BaseModel):
    name: str
    content: str
    tags: Optional[List[str]] = None
    id: Optional[str] = None
    key: Optional[str] = None

class MemoryImportRequest(BaseModel):
    cid: str
    key: Optional[str] = None

class SkillInstallRequest(BaseModel):
    id: str                               # discover result id, e.g. gh:owner/repo:skills/pdf
    path: Optional[str] = None            # explicit SKILL.md path within the repo
    key: Optional[str] = None

class SkillImportRequest(BaseModel):
    cid: str
    key: Optional[str] = None

class UploadRequest(BaseModel):
    text: str                             # the file's contents
    filename: Optional[str] = None        # helps detect the kind
    kind: Optional[str] = None            # prompt|skill|memory|agent, else auto
    key: Optional[str] = None

class LibraryImportRequest(BaseModel):
    cid: str
    kind: Optional[str] = None            # assert the bundle's type, else auto
    key: Optional[str] = None

class DiscoverTokenRequest(BaseModel):
    token: str = ""                       # empty clears the stored token
    key: Optional[str] = None

class ConversationRequest(BaseModel):
    id: Optional[str] = None
    query: str = ""
    agent_type: str = "default"
    status: str = "done"
    messages: List[dict] = []
    started: Optional[float] = None
    key: Optional[str] = None

class ConversationImportRequest(BaseModel):
    cid: str
    key: Optional[str] = None

class ToolboxRequest(BaseModel):
    name: str
    tools: List[str] = []
    description: str = ""
    key: Optional[str] = None

class ToolRequest(BaseModel):
    name: str
    command: str
    description: str = ""
    params: Optional[Dict[str, Dict]] = None
    cwd: Optional[str] = None
    timeout: int = 60
    key: Optional[str] = None

class ToolRunRequest(BaseModel):
    params: Dict[str, Any] = {}
    key: Optional[str] = None

class SelectRequest(BaseModel):
    tools: Optional[List[str]] = None   # null = back to the snapped toolboxes
    key: Optional[str] = None

class FactRequest(BaseModel):
    name: str
    content: str
    tags: Optional[List[str]] = None
    key: Optional[str] = None

class DepositRequest(BaseModel):
    tx_hash: str
    network: str = "base"
    key: Optional[str] = None

class CreditGrantRequest(BaseModel):
    address: str
    amount: float
    note: str = ""
    key: Optional[str] = None

class VaultCreateRequest(BaseModel):
    name: str
    key: Optional[str] = None

class VaultEntryRequest(BaseModel):
    entry: str
    value: str
    private: bool = True
    key: Optional[str] = None


# ── lazy mod singleton ───────────────────────────────────────────────

_mod = None

def get_mod():
    global _mod
    if _mod is None:
        from src.mod import Mod
        _mod = Mod()
    return _mod


# ── server-side task registry ────────────────────────────────────────
# Every run (blocking or streamed) is registered here so any client can
# see what the agent is doing in the background — runs keep going server
# side even after the page that started them disconnects.

TASKS: "OrderedDict[str, dict]" = OrderedDict()
TASKS_LOCK = threading.Lock()
MAX_TASKS = 100        # registry entries kept
MAX_TRACE = 60         # trimmed steps kept per task
MAX_TASK_THUMBS = 4    # attachment previews kept per task
MAX_THUMB_BYTES = 96_000   # one preview — a thumbnail, never the full-size image


def _caller_address(key: Optional[str]) -> Optional[str]:
    """Verified address for a caller token/key ('' -> None).

    Verified because this address is what runs get billed to and what
    conversations are filed under — an unsigned address claim is not enough.
    """
    if not key:
        return None
    try:
        addr = get_mod()._resolve_address(key, verified=True)
    except Exception:
        return None
    if isinstance(addr, str) and addr.startswith('0x') and len(addr) == 42:
        return addr.lower()
    return None


def _task_thumbs(req: "RunRequest") -> List[str]:
    """Previews of what the caller attached, small enough to keep in memory.

    The registry holds thumbnails only — full-size data URLs would be tens of
    megabytes across 100 tasks, and they're never rendered larger than a chip.
    """
    src = req.thumbs or req.images or []
    return [u for u in src
            if isinstance(u, str) and u.startswith('data:image/')
            and len(u) <= MAX_THUMB_BYTES][:MAX_TASK_THUMBS]


def _task_model(req: "RunRequest") -> str:
    """The model this run will actually use.

    A FREE MODE run ignores req.model and resolves a zero-cost one, so the
    requested model would be a lie in the ledger and in the console's trace.
    """
    if not req.free:
        return req.model
    try:
        return get_mod().free_model() or req.model
    except Exception:
        return req.model


def _task_create(req: "RunRequest", chain: bool = False, agent: str = None) -> dict:
    t = {
        "id": uuid.uuid4().hex[:12],
        "query": (req.query or "")[:400],
        # the agent the run actually used — resolved by the caller when the
        # request named none, so the registry doesn't say 'default' for a
        # run that went to Claude Code
        "agent_type": (agent or req.agent_type or req.agent or "default"),
        "provider": req.provider or "openrouter",
        "model": _task_model(req),
        "chain": chain,
        "user": _caller_address(req.key),
        "status": "running",
        "steps": 0,
        "tool": None,          # tool currently/last executing
        "started_at": time.time(),
        "finished_at": None,
        "summary": None,
        "trace": [],           # [{tool, path}] — trimmed, no results
        "thumbs": _task_thumbs(req),   # attachment previews, served on demand
    }
    with TASKS_LOCK:
        TASKS[t["id"]] = t
        while len(TASKS) > MAX_TASKS:
            TASKS.popitem(last=False)
    return t


def _task_step(t: dict, step) -> None:
    if not isinstance(step, dict):
        return
    with TASKS_LOCK:
        t["steps"] += 1
        t["tool"] = step.get("tool")
        params = step.get("params") or {}
        entry = {"tool": step.get("tool")}
        path = params.get("path") or params.get("file_path") or params.get("pattern")
        if path:
            entry["path"] = str(path)[:200]
        t["trace"].append(entry)
        if len(t["trace"]) > MAX_TRACE:
            t["trace"] = t["trace"][-MAX_TRACE:]


def _summary_of(result) -> str:
    """Pull the finish summary (or last response / error) out of a run's step list."""
    if not isinstance(result, list):
        return ""
    summary = ""
    error = ""
    for s in result:
        if isinstance(s, dict):
            if s.get("tool") == "finish":
                summary = s.get("params", {}).get("summary", "") or summary
            elif s.get("tool") == "response" and s.get("result") and not summary:
                summary = str(s.get("result"))
            elif s.get("tool") == "error" and s.get("error") and not error:
                error = str(s.get("error"))
    return (summary or error)[:400]


def _status_of(result) -> str:
    """A run that produced only error steps failed, even if forward() returned."""
    if isinstance(result, list):
        has_err = any(isinstance(s, dict) and s.get("tool") == "error" for s in result)
        has_ok = any(isinstance(s, dict) and s.get("tool") in ("finish", "response") for s in result)
        if has_err and not has_ok:
            return "error"
    return "done"


def _task_finish(t: dict, status: str, summary: str = "") -> None:
    with TASKS_LOCK:
        t["status"] = status
        t["finished_at"] = time.time()
        if summary:
            t["summary"] = summary[:400]


def _charge_run(req: "RunRequest", t: dict) -> Optional[dict]:
    """Bill a finished non-owner run against the caller's credit ledger.

    Owners run free on their own key; `free` runs (free models) cost
    nothing. Everyone else pays steps × price, clamped to their balance.
    """
    if req.free or not t.get("user"):
        return None
    mod = get_mod()
    try:
        if mod.is_owner(req.key):
            return None
        charge = mod.credits.charge_steps(t["user"], t["steps"], note=t["query"][:80])
        if charge.get("charged"):
            with TASKS_LOCK:
                t["charged"] = charge["charged"]
        return charge
    except Exception:
        return None


# ── routes (thin wrappers over mod.forward) ──────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "module": "agent", "version": app.version}

@app.get("/config")
def get_config():
    """Get module config.json"""
    import json
    config_path = os.path.join(module_root, 'config.json')
    if not os.path.exists(config_path):
        return {"error": "config.json not found"}
    with open(config_path) as f:
        return json.load(f)

@app.get("/status")
def get_status():
    return get_mod().forward('status')

@app.post("/forward")
def forward(req: ForwardRequest):
    """Mod protocol entry point: dispatch any action"""
    mod = get_mod()
    try:
        result = mod.forward(action=req.action, key=req.key, **req.params)
        return {"action": req.action, "result": result}
    except PermissionError as e:
        return {"action": req.action, "error": str(e), "code": 403}
    except Exception as e:
        return {"action": req.action, "error": str(e)}

@app.get("/skills")
def list_skills():
    mod = get_mod()
    return {"skills": mod.skills.ls(), "schemas": mod.skill_schema()}

@app.get("/schema")
def get_schema():
    return get_mod().skill_schema()

@app.get("/providers")
def list_providers():
    """List LLM providers with their selectable models and default model."""
    mod = get_mod()
    providers = []
    for key in mod.PROVIDERS:
        default_model = mod.DEFAULT_MODELS.get(key) or mod.DEFAULT_MODELS.get(mod.PROVIDERS.get(key, ''), '')
        info = mod.key_info(key)
        providers.append({
            "key": key,
            "models": mod.MODELS.get(key, []),
            "default_model": default_model,
            "configured": info.get("configured", False),
            "encrypted": info.get("encrypted", False),
            "unlocked": info.get("unlocked", False),
            "remembered": info.get("remembered", False),
        })
    return {"providers": providers, "default": "openrouter"}

@app.get("/params")
def run_params(key: Optional[str] = None):
    """Self-describing UI schema for a run's parameters. Sibling consoles
    (e.g. orbit/build) render this generically instead of hardcoding a
    panel per agent backend: each field maps 1:1 onto a RunRequest key,
    select options come from the live registries (agents, providers,
    toolboxes), and the credits block points at the billing endpoints."""
    mod = get_mod()
    personas = []
    try:
        reg = mod.forward('agents')
        for name in reg.get('agents', []):
            s = (reg.get('schemas') or {}).get(name) or {}
            personas.append({"value": name, "label": s.get('name') or name,
                             "icon": s.get('icon') or '>_',
                             "hint": s.get('description') or '',
                             "model": s.get('model')})
    except Exception:
        personas = [{"value": "default", "label": "Default", "icon": ">_", "hint": ""}]
    providers, models_by, default_by = [], {}, {}
    for key in mod.PROVIDERS:
        info = mod.key_info(key)
        state = ("ready" if info.get("configured") else
                 "locked" if info.get("encrypted") and not info.get("unlocked") else "no key")
        providers.append({"value": key, "label": key, "hint": state})
        models_by[key] = mod.MODELS.get(key, [])
        default_by[key] = mod.DEFAULT_MODELS.get(key) or mod.DEFAULT_MODELS.get(mod.PROVIDERS.get(key, ''), '')
    try:
        toolboxes = [{"value": t.get('name'), "label": t.get('name'),
                      "hint": t.get('description') or ''} for t in mod.toolboxes.items()]
    except Exception:
        toolboxes = []
    return {
        "module": "agent", "version": app.version, "title": "AGENT PARAMS",
        "run": {"endpoint": "/run/stream", "blocking": "/run", "auth": "body key (protocol-auth token)"},
        "fields": [
            {"name": "agent_type", "label": "PERSONA", "type": "select",
             "default": mod.default_agent(key), "options": personas},
            {"name": "provider", "label": "PROVIDER", "type": "select",
             "default": "openrouter", "options": providers},
            {"name": "model", "label": "MODEL", "type": "select", "depends": "provider",
             "options_by": models_by, "default_by": default_by},
            {"name": "toolbox", "label": "TOOLBOX", "type": "select", "default": None,
             "options": [{"value": None, "label": "auto", "hint": "persona default"}] + toolboxes},
            {"name": "steps", "label": "MAX STEPS", "type": "number",
             "default": 10, "min": 1, "max": 50, "step": 1,
             "hint": "agent-loop iterations — also the billing unit"},
            {"name": "temperature", "label": "TEMP", "type": "number",
             "default": 0.0, "min": 0.0, "max": 2.0, "step": 0.1},
            {"name": "safety", "label": "SAFETY REVIEW", "type": "toggle", "default": False,
             "hint": "safety agent reviews each plan"},
            {"name": "free", "label": "FREE MODE", "type": "toggle", "default": False,
             "hint": "free models only; run is never billed"},
        ],
        "credits": {"info": "/credits", "deposit": "/credits/deposit",
                    "balance": "/balance", "whoami": "/whoami"},
    }

@app.post("/skills/run")
def run_skill(req: SkillRunRequest):
    """Run a single skill. Write skills are path-restricted for non-owners."""
    mod = get_mod()
    try:
        # custom tools have their own admin-gated route — this one is open
        if req.name not in mod.skills.ls() and mod.tools.exists(req.name):
            return {"skill": req.name, "error": f"'{req.name}' is a custom tool — "
                    f"POST /tools/{req.name}/run", "code": 403}
        if req.name in ('write', 'edit', 'patch'):
            allowed = mod.allowed_paths_for(req.key)
            fp = req.params.get('file_path', '')
            if fp and allowed is not None:
                from src.mod import check_path_allowed
                if not check_path_allowed(fp, allowed):
                    return {"skill": req.name, "error": f"Permission denied: cannot write to {fp}", "code": 403}
        result = mod.run_skill(req.name, **req.params)
        return {"skill": req.name, "result": result}
    except KeyError:
        return {"skill": req.name, "error": f"unknown skill: {req.name}", "available": mod.skills.ls()}
    except Exception as e:
        return {"skill": req.name, "error": str(e)}

@app.get("/key")
def key_info(provider: str = "openrouter"):
    """Masked view of the active provider API key + encrypted-vault state."""
    return get_mod().key_info(provider)

@app.post("/key")
def set_key(req: ApiKeyRequest):
    """Set your own provider API key.

    With `passphrase` set, the key is sealed into an encrypted vault file
    (AES-256-GCM) that only the passphrase can open — the server never
    stores the plaintext. Without it, the key goes to the provider's
    plaintext store (legacy behavior).
    """
    try:
        return get_mod().forward('set_key', key=req.key, api_key=req.api_key,
                                 provider=req.provider, passphrase=req.passphrase,
                                 remember=req.remember)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/key/unlock")
def unlock_key(req: VaultRequest):
    """Decrypt the vaulted key into server memory.

    `remember` (default true) re-seals it under this server's device key so the
    unlock survives restarts — the passphrase is asked for once, not forever.
    """
    try:
        return get_mod().forward('unlock', key=req.key, provider=req.provider,
                                 passphrase=req.passphrase or '', remember=req.remember)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/key/lock")
def lock_key(req: VaultRequest):
    """Forget the decrypted key. The encrypted file stays on disk."""
    try:
        return get_mod().forward('lock', key=req.key, provider=req.provider)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.delete("/key")
def remove_vault(provider: str = "openrouter", key: Optional[str] = None):
    """Delete the encrypted vault file for a provider."""
    try:
        return get_mod().forward('vault_rm', key=key, provider=provider)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.get("/balance")
def get_balance(provider: str = "openrouter"):
    """Remaining credit on the active API key."""
    return get_mod().balance(provider)

@app.get("/owner")
def get_owner():
    mod = get_mod()
    return {"owner": mod._owner, "has_owner": bool(mod._owner)}

@app.get("/whoami")
def whoami(key: Optional[str] = None):
    """Resolve a signed protocol-auth token to a verified address + role.

    The token is the fleet-standard base64url of {data, time, key, signature}
    where signature = personal_sign of the compact JSON {"data":…,"time":…}.
    """
    mod = get_mod()
    out = {"signed_in": False, "address": None, "is_owner": False,
           "owner": mod._owner}
    if not key:
        return out
    addr = None
    if mod.auth:
        try:
            addr = mod.auth.verify(key)['key']
        except Exception:
            return {**out, "error": "invalid or expired token"}
    else:
        addr = _caller_address(key)
    if not addr:
        return {**out, "error": "could not resolve address"}
    out.update(signed_in=True, address=addr.lower(),
               is_owner=mod.is_owner(key))
    return out

# ── credits (prepaid USDT/USDC top-ups for the public key) ───────────

@app.get("/credits")
def get_credits(key: Optional[str] = None):
    """Deposit address, pricing, and the caller's credit account/history.

    Owner also gets the full per-address account list.
    """
    return get_mod().credits_info(key)

@app.post("/credits/deposit")
def credit_deposit(req: DepositRequest):
    """Verify a USDT/USDC transfer to the deposit address by tx hash.

    Credits go to the ON-CHAIN SENDER of the transfer (so a hash can't
    be claimed by someone else), and each hash is credited only once.
    """
    try:
        return get_mod().credit_deposit(req.tx_hash, req.network)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"verification failed: {e}"}

@app.post("/credits/grant")
def credit_grant(req: CreditGrantRequest):
    """Manually adjust an account's credits (± amount). Owner only."""
    try:
        return get_mod().credit_grant(req.address, req.amount, req.note, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

# ── server-side tasks (background runs) ──────────────────────────────

@app.get("/tasks")
def list_tasks(limit: int = 25):
    """Running + recent runs across all clients (running first, newest first).

    Attachments come back as a count — the previews themselves are base64 and
    this list is polled every few seconds, so they get their own route.
    """
    with TASKS_LOCK:
        items = [{**{k: v for k, v in t.items() if k not in ("trace", "thumbs")},
                  "images": len(t.get("thumbs") or [])}
                 for t in TASKS.values()]
    items.sort(key=lambda t: (t["status"] != "running", -t["started_at"]))
    running = sum(1 for t in items if t["status"] == "running")
    return {"tasks": items[:max(1, min(limit, MAX_TASKS))], "running": running}

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return {"error": f"unknown task: {task_id}"}
        return {**{k: v for k, v in t.items() if k != "thumbs"},
                "images": len(t.get("thumbs") or [])}

@app.get("/tasks/{task_id}/images")
def get_task_images(task_id: str):
    """The images attached to a run, as thumbnails — fetched when a task is opened."""
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return {"error": f"unknown task: {task_id}"}
        return {"id": task_id, "images": list(t.get("thumbs") or [])}

# ── access control (gate) ────────────────────────────────────────────

@app.post("/grant")
def grant_access(req: GrantRequest):
    """Grant admin access to an address. Owner only."""
    try:
        return get_mod().forward('grant', key=req.key, address=req.address, actions=req.actions)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/revoke")
def revoke_access(req: RevokeRequest):
    """Revoke access from an address. Owner only."""
    try:
        return get_mod().forward('revoke', key=req.key, address=req.address)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.get("/acl")
def get_acl(key: Optional[str] = None):
    """View current access control list. Owner only."""
    try:
        return get_mod().forward('acl', key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

# ── agents (from agents/ registry) ──────────────────────────────────

@app.get("/agents")
def list_agents(key: Optional[str] = None):
    """List all agent personas from agents/ directory.

    `default` is the one a run lands on when none is named — Claude Code for
    the host, the native agent for everyone else — so a console can preselect
    it instead of deciding for itself.
    """
    mod = get_mod()
    return {**mod.forward('agents'), "default": mod.default_agent(key)}

@app.get("/agents/{name}")
def get_agent(name: str):
    """Get a specific agent config"""
    try:
        config = get_mod().forward('agent', name=name)
        return {k: v for k, v in config.items() if k != 'cls'}
    except KeyError:
        return {"error": f"agent not found: {name}", "available": get_mod().agents.ls()}

@app.post("/agents")
def create_agent(req: AgentCreateRequest):
    """Create a new agent locally. Signed-in callers only — the agent is
    filed under the address that made it."""
    mod = get_mod()
    try:
        # note: mod.forward('agents', action=...) collides with forward's own
        # `action` arg — the agents action is public, so dispatch directly
        result = mod.agents.forward(action='create',
            name=req.name, description=req.description, goal=req.goal,
            icon=req.icon, skills=req.skills, model=req.model,
            harness=req.harness, key=req.key)
        if isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != 'cls'}
        return result
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (FileExistsError, ValueError) as e:
        return {"error": str(e)}

@app.put("/agents/{name}")
def update_agent(name: str, req: AgentUpdateRequest):
    """Update an agent. Its owner or the host — built-ins are host-only."""
    mod = get_mod()
    try:
        skills = None if req.clear_skills else (req.skills if req.skills is not None else ...)
        model = None if req.clear_model else (req.model if req.model is not None else ...)
        harness = None if req.clear_harness else (req.harness if req.harness is not None else ...)
        result = mod.agents.update(
            name=name, description=req.description, goal=req.goal,
            icon=req.icon, skills=skills, model=model, harness=harness, key=req.key)
        return {k: v for k, v in result.items() if k != 'cls'}
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (KeyError, ValueError) as e:
        return {"error": str(e)}

@app.delete("/agents/{name}")
def remove_agent(name: str, key: Optional[str] = None):
    """Remove an agent. Its owner or the host — built-ins are host-only."""
    mod = get_mod()
    try:
        return mod.agents.forward(action='remove', name=name, key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

@app.post("/agents/import")
def import_agent(req: LibraryImportRequest):
    """Install a shared agent from its localfs CID (QR / share path)."""
    try:
        return get_mod().library.import_cid(req.cid, "agent", key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError, KeyError) as e:
        return {"error": str(e)}

@app.post("/agents/{name}/register")
def register_agent(name: str, req: AgentRegisterRequest):
    """Register an agent on the registry (offchain or on-chain)"""
    mod = get_mod()
    try:
        return mod.agents.forward(action='register',
            name=name, backend=req.backend, key=req.key)
    except Exception as e:
        return {"error": str(e)}

@app.get("/harnesses")
def list_harnesses():
    """External agent CLIs an agent can hand its run to, and whether the
    binary is installed on this host."""
    return get_mod().forward('harnesses')

@app.get("/chains")
def list_chains():
    """List chain presets"""
    return get_mod().forward('chains')

# ── library (prompts / skills / memory / agent market) ──────────────

@app.get("/library")
def get_library(q: Optional[str] = None, kind: Optional[str] = None,
                tag: Optional[str] = None):
    """Unified filterable index across prompts, skills, memory, and agents."""
    return get_mod().library.items(q=q, kind=kind, tag=tag)

@app.get("/library/formats")
def library_formats():
    """What an upload may look like, plus docs/uploads.md for the console."""
    return get_mod().library.formats()

@app.post("/library/upload")
def library_upload(req: UploadRequest):
    """Upload one file into the library — prompt, skill, memory note or agent.

    The kind is the caller's pick, else the file's `type:`, else its name,
    else its shape (see docs/uploads.md). Creating takes a sign-in.
    """
    try:
        return get_mod().library.upload(req.text, req.filename, req.kind,
                                        key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

try:  # the file post needs python-multipart — JSON upload works either way
    from fastapi import File, Form, UploadFile

    @app.post("/library/upload/file")
    async def library_upload_file(file: UploadFile = File(...),
                                  kind: Optional[str] = Form(None),
                                  key: Optional[str] = Form(None)):
        """The same upload, posted as a file: `curl -F file=@my.agent.md`."""
        raw = await file.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "upload must be UTF-8 text (JSON or markdown)"}
        try:
            return get_mod().library.upload(text, file.filename, kind, key=key)
        except PermissionError as e:
            return {"error": str(e), "code": 403}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
except (ImportError, RuntimeError):
    pass

@app.post("/library/import")
def library_import(req: LibraryImportRequest):
    """Install anything from its localfs CID — the bundle says what it is."""
    try:
        return get_mod().library.import_cid(req.cid, req.kind, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError, KeyError) as e:
        return {"error": str(e)}

@app.get("/prompts")
def list_prompts():
    """Prompts with their effective owner (unowned = owned by the host)."""
    lib = get_mod().library
    return {"prompts": [{**p, **lib.prompt_owner(p)} for p in lib.prompts()],
            "host": lib.identity.host}

@app.post("/prompts")
def save_prompt(req: PromptRequest):
    """Create or update a prompt (upsert by id). Editing needs owner/host."""
    try:
        return get_mod().library.prompt_add(
            req.name, req.text, req.description, req.tags, req.id, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/prompts/import")
def import_prompt(req: PromptImportRequest):
    """Install a shared prompt from its localfs CID (QR / share path)."""
    try:
        return get_mod().library.prompt_import(req.cid.strip(), key=req.key)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

@app.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str, key: Optional[str] = None):
    """Remove a prompt. Its owner or the host only."""
    try:
        return get_mod().library.prompt_rm(prompt_id, key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

@app.get("/memory")
def list_memory():
    """Memory notes with their effective owner (unowned = owned by the host)."""
    lib = get_mod().library
    return {"memory": [{**n, **lib.note_owner(n)} for n in lib.notes()],
            "host": lib.identity.host}

@app.post("/memory")
def save_memory(req: MemoryNoteRequest):
    """Create or update a memory note (upsert by id). Creating needs a
    sign-in; editing needs owner/host."""
    try:
        return get_mod().library.note_add(req.name, req.content, req.tags,
                                          req.id, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/memory/import")
def import_memory(req: MemoryImportRequest):
    """Install a shared memory note from its localfs CID."""
    try:
        return get_mod().library.note_import(req.cid.strip(), key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

@app.delete("/memory/{note_id}")
def delete_memory(note_id: str, key: Optional[str] = None):
    """Remove a memory note. Its owner or the host only."""
    try:
        return get_mod().library.note_rm(note_id, key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

# ── discover (internet-wide skill aggregator) ───────────────────────
# Read-only scanning is public; installs land in the shared library as
# documents. Setting the GitHub token / clearing the cache is owner-only.

@app.get("/discover")
def discover_scan(q: str = "", sources: Optional[str] = None, limit: int = 30,
                  kind: Optional[str] = None, fresh: bool = False):
    """Scan every registry at once. `sources` is a comma-separated subset."""
    picked = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    try:
        return get_mod().discover.search(q, picked, limit, kind, fresh)
    except ValueError as e:
        return {"error": str(e), "items": [], "total": 0}

@app.get("/discover/sources")
def discover_sources():
    """The source catalog + whether a GitHub token is configured."""
    d = get_mod().discover
    return {"sources": d.sources(), "token": bool(d.token())}

@app.get("/discover/item")
def discover_detail(id: str):
    """Full record for one scanned result, including its SKILL.md paths."""
    try:
        return get_mod().discover.detail(id)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

@app.get("/discover/doc")
def discover_doc(id: str, path: Optional[str] = None):
    """Preview the exact document an install would add to the library."""
    try:
        return get_mod().discover.skill_doc(id, path)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

@app.post("/discover/install")
def discover_install(req: SkillInstallRequest):
    """Install a scanned result into the library as an external skill.

    Signed-in callers only — the installer owns what they added."""
    try:
        return get_mod().skill_install(req.id, req.path, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

@app.post("/discover/token")
def discover_token(req: DiscoverTokenRequest):
    """Owner: store a GitHub token off-tree to lift the anonymous rate limit."""
    mod = get_mod()
    if not mod.is_owner(req.key):
        return {"error": "owner only", "code": 403}
    return mod.discover.set_token(req.token)

@app.post("/discover/cache/clear")
def discover_clear_cache(req: DiscoverTokenRequest):
    """Owner: drop every cached scan so the next one hits the network."""
    mod = get_mod()
    if not mod.is_owner(req.key):
        return {"error": "owner only", "code": 403}
    return mod.discover.clear_cache()

@app.get("/skills/installed")
def list_installed_skills():
    """External skills installed from the aggregator, with their owner."""
    lib = get_mod().library
    return {"skills": [{**s, **lib.skill_owner(s)} for s in lib.installed_skills()],
            "host": lib.identity.host}

@app.post("/skills/import")
def import_installed_skill(req: SkillImportRequest):
    """Install an external skill from its localfs CID (the share path)."""
    try:
        return get_mod().library.skill_import(req.cid.strip(), key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

@app.delete("/skills/installed/{skill_id}")
def delete_installed_skill(skill_id: str, key: Optional[str] = None):
    """Uninstall an external skill. Whoever installed it, or the host."""
    try:
        return get_mod().library.skill_rm(skill_id, key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

# ── conversations (per-user console history, pinned to localfs) ──────

@app.get("/conversations")
def list_conversations(key: Optional[str] = None):
    """The caller's saved conversations (newest first). Requires sign-in."""
    addr = _caller_address(key)
    if not addr:
        return {"conversations": [], "signed_in": False}
    return {"conversations": get_mod().library.convs(addr), "signed_in": True}

@app.post("/conversations")
def save_conversation(req: ConversationRequest):
    """Upsert a conversation for the signed-in caller. Pins it to localfs."""
    addr = _caller_address(req.key)
    if not addr:
        return {"error": "sign in to save conversations", "code": 403}
    try:
        return get_mod().library.conv_save(
            addr, req.id, req.query, req.agent_type, req.status,
            req.messages, req.started)
    except (ValueError, PermissionError) as e:
        return {"error": str(e)}

@app.post("/conversations/import")
def import_conversation(req: ConversationImportRequest):
    """Restore a shared conversation from its localfs CID."""
    addr = _caller_address(req.key)
    if not addr:
        return {"error": "sign in to import conversations", "code": 403}
    try:
        return get_mod().library.conv_import(addr, req.cid.strip())
    except (ValueError, RuntimeError, PermissionError) as e:
        return {"error": str(e)}

@app.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, key: Optional[str] = None):
    addr = _caller_address(key)
    if not addr:
        return {"error": "sign in to manage conversations", "code": 403}
    try:
        return get_mod().library.conv_rm(addr, conv_id)
    except KeyError as e:
        return {"error": str(e)}

# ── vaults (per-address KV stores via the mod store module) ──────────
# Entries are public (plaintext, anyone can read via /vaults/public) or
# private (AES-GCM-sealed at rest, masked in listings, revealed only to
# the signed-in owner with ?reveal=true).

@app.get("/vaults")
def list_vaults(key: Optional[str] = None):
    """The caller's vaults (summaries, newest first). Requires sign-in."""
    addr = _caller_address(key)
    if not addr:
        return {"vaults": [], "signed_in": False}
    try:
        return {"vaults": get_mod().vaults.ls(addr), "signed_in": True}
    except Exception as e:
        return {"error": str(e)}

@app.post("/vaults")
def create_vault(req: VaultCreateRequest):
    """Create an empty vault for the signed-in caller."""
    addr = _caller_address(req.key)
    if not addr:
        return {"error": "sign in to use vaults", "code": 403}
    try:
        return get_mod().vaults.create(addr, req.name)
    except (ValueError, PermissionError) as e:
        return {"error": str(e)}

@app.get("/vaults/public")
def public_vault(address: str, name: str):
    """The PUBLIC entries of any address's vault. No auth."""
    try:
        return get_mod().vaults.public(address, name)
    except (KeyError, ValueError, PermissionError) as e:
        return {"error": str(e)}

@app.get("/vaults/{name}")
def get_vault(name: str, key: Optional[str] = None, reveal: bool = False):
    """A vault's entries. Private values stay masked unless reveal=true."""
    addr = _caller_address(key)
    if not addr:
        return {"error": "sign in to use vaults", "code": 403}
    try:
        return get_mod().vaults.get(addr, name, reveal=reveal)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}

@app.post("/vaults/{name}/entries")
def set_vault_entry(name: str, req: VaultEntryRequest):
    """Upsert an entry (private by default). Creates the vault on first write."""
    addr = _caller_address(req.key)
    if not addr:
        return {"error": "sign in to use vaults", "code": 403}
    try:
        return get_mod().vaults.set(addr, name, req.entry, req.value,
                                    private=req.private)
    except (ValueError, KeyError) as e:
        return {"error": str(e)}

@app.delete("/vaults/{name}/entries/{entry}")
def delete_vault_entry(name: str, entry: str, key: Optional[str] = None):
    addr = _caller_address(key)
    if not addr:
        return {"error": "sign in to use vaults", "code": 403}
    try:
        return get_mod().vaults.entry_rm(addr, name, entry)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}

@app.delete("/vaults/{name}")
def delete_vault(name: str, key: Optional[str] = None):
    addr = _caller_address(key)
    if not addr:
        return {"error": "sign in to use vaults", "code": 403}
    try:
        return get_mod().vaults.rm(addr, name)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}

# ── toolboxes (snap-on skill bundles) ────────────────────────────────

@app.get("/toolboxes")
def list_toolboxes():
    """All toolboxes (built-in presets + custom) and what's snapped on."""
    mod = get_mod()
    return {"toolboxes": mod.toolboxes.items(), "snapped": mod.snapped()}

@app.get("/toolboxes/{name}")
def get_toolbox(name: str):
    mod = get_mod()
    try:
        box = mod.toolboxes.get(name).to_dict()
        box["resolved"] = mod.toolboxes.get(name).resolve(mod.toolboxes.known())
        return box
    except KeyError:
        return {"error": f"toolbox not found: {name}", "available": mod.toolboxes.ls()}

@app.post("/toolboxes")
def create_toolbox(req: ToolboxRequest):
    """Create or update a custom toolbox. Admin."""
    try:
        return get_mod().forward('toolbox_add', key=req.key, name=req.name,
                                 tools=req.tools, description=req.description)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}

@app.delete("/toolboxes/{name}")
def delete_toolbox(name: str, key: Optional[str] = None):
    try:
        return get_mod().forward('toolbox_rm', key=key, name=name)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/toolboxes/{name}/snap")
def snap_toolbox(name: str, key: Optional[str] = None):
    """Snap a toolbox onto the live agent. Admin."""
    try:
        return get_mod().forward('snap', key=key, name=name)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

@app.post("/toolboxes/unsnap")
def unsnap_all(key: Optional[str] = None):
    """Detach every box and drop any hand-picked list — back to all tools."""
    try:
        return get_mod().forward('unsnap', key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/toolboxes/{name}/unsnap")
def unsnap_toolbox(name: str, key: Optional[str] = None):
    try:
        return get_mod().forward('unsnap', key=key, name=name)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

# ── tools (shipped skills + custom shell tools, in one registry) ─────
# A custom tool runs shell, so writing one is host-only — library skills are
# documents for exactly that reason. Reading the registry is public.

@app.get("/tools")
def list_tools():
    """Every tool the agent can call, in one list the console can render."""
    mod = get_mod()
    active = mod.active_skills()          # None = nothing filtered out
    schemas = mod.skills.schema()
    tools = [{"name": n, "kind": "skill", "builtin": True,
              "description": (schemas.get(n) or {}).get("description", ""),
              "params": (schemas.get(n) or {}).get("params", {}),
              "active": active is None or n in active}
             for n in mod.skills.ls()]
    tools += [{**t, "builtin": False,
               "active": active is None or t["name"] in active}
              for t in mod.tools.items()]
    return {"tools": tools, "snapped": mod.snapped(),
            "toolboxes": mod.toolboxes.items(), "host": mod._owner}

@app.post("/tools")
def save_tool(req: ToolRequest):
    """Create or update a custom tool. Host (or a granted admin) only."""
    try:
        return get_mod().forward('tool_add', key=req.key, name=req.name,
                                 command=req.command, description=req.description,
                                 params=req.params, cwd=req.cwd, timeout=req.timeout)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}

@app.delete("/tools/{name}")
def delete_tool(name: str, key: Optional[str] = None):
    try:
        return get_mod().forward('tool_rm', key=key, name=name)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/tools/select")
def select_tools(req: SelectRequest):
    """Pin the loadout to an exact tool list — the console's per-tool switch.

    `tools: null` (or an empty list) hands the set back to whatever toolboxes
    are snapped on. Admin, same as snapping: it changes what the model gets.
    """
    try:
        return get_mod().forward('select', key=req.key, tools=req.tools)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/tools/{name}/run")
def run_tool(name: str, req: ToolRunRequest):
    """Execute one custom tool — the console's 'try it' button."""
    try:
        return {"tool": name,
                "result": get_mod().forward('tool_run', key=req.key, name=name,
                                            params=req.params)}
    except PermissionError as e:
        return {"tool": name, "error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"tool": name, "error": str(e)}

# ── memory subsystem (working/episodic/semantic layers, own process) ─

@app.get("/memory/state")
def memory_state():
    """Layer counts + session for the agent's memory subsystem."""
    return get_mod().forward('memory_state')

@app.get("/memory/recall")
def memory_recall(q: str, k: int = 5):
    """Durable facts scored against a query."""
    return {"query": q, "facts": get_mod().forward('recall', query=q, k=k)}

@app.get("/memory/facts")
def memory_facts():
    return {"facts": get_mod().forward('facts')}

@app.get("/memory/episodes")
def memory_episodes(n: int = 50, session: Optional[str] = None):
    """Recent episode trail (every step the agent executed)."""
    return {"episodes": get_mod().forward('episodes', n=n, session=session)}

@app.post("/memory/remember")
def memory_remember(req: FactRequest):
    """Store a durable fact future runs can recall. Admin."""
    try:
        return {"fact": get_mod().forward('remember', key=req.key, name=req.name,
                                          content=req.content, tags=req.tags)}
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.delete("/memory/facts/{fid}")
def memory_forget(fid: str, key: Optional[str] = None):
    try:
        return get_mod().forward('forget', key=key, id=fid)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/memory/serve")
def memory_serve(key: Optional[str] = None, port: Optional[int] = None):
    """Start the memory service as its own process (:50119). Admin."""
    try:
        return get_mod().forward('memory_serve', key=key, port=port)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

# ── run (delegates to mod.forward('run')) ────────────────────────────

def _run_chain(mod, req: RunRequest, on_step=None, on_chain_step=None):
    """Execute a multi-agent chain, feeding each step's summary into the next."""
    chain_results = []
    context = req.query
    for i, step in enumerate(req.chain):
        step_agent = step.get("agent", "default")
        step_prompt = step.get("prompt", "")
        if on_chain_step:
            on_chain_step(i, step_agent)
        if i == 0:
            step_query = f"{step_prompt}\n\nUser request: {context}" if step_prompt else context
        else:
            prev_summary = chain_results[-1].get("summary", "")
            step_query = f"{step_prompt}\n\nUser request: {context}\n\nPrevious step output: {prev_summary}" if step_prompt else context
        try:
            result = mod.forward('run',
                key=req.key,
                query=step_query,
                model=req.model,
                provider=req.provider,
                steps=req.steps,
                agent_type=step_agent,
                temperature=req.temperature,
                safety=req.safety,
                free=req.free,
                memory_ids=req.memory_ids,
                skill_ids=req.skill_ids,
                on_step=on_step,
            )
            summary = ""
            if isinstance(result, list):
                for s in result:
                    if isinstance(s, dict) and s.get("tool") == "finish":
                        summary = s.get("params", {}).get("summary", "")
            chain_results.append({"step": i, "agent": step_agent, "result": result, "summary": summary})
        except Exception as e:
            chain_results.append({"step": i, "agent": step_agent, "error": str(e), "summary": f"Error: {e}"})
    return chain_results


@app.post("/run")
def run_agent(req: RunRequest):
    """Run the agent loop. Agent resolution happens in Mod._run()."""
    mod = get_mod()
    resolved_agent = req.agent_type or req.agent or mod.default_agent(req.key)
    # a harness agent runs on its own CLI, so it needs no provider key here
    if mod.model is None and not mod.harness_for(resolved_agent):
        return {"error": "No API key configured for the selected provider — add or unlock a key in the Builder (model node)."}

    # chain execution
    if req.chain and len(req.chain) > 0:
        task = _task_create(req, chain=True, agent=resolved_agent)
        results = _run_chain(mod, req, on_step=lambda s: _task_step(task, s))
        errs = [r.get("error") for r in results if r.get("error")]
        _task_finish(task, 'error' if errs else 'done',
                     errs[0] if errs else (results[-1].get("summary", "") if results else ""))
        charge = _charge_run(req, task)
        return {"query": req.query, "chain": True, "task_id": task["id"],
                "results": results, "charged": charge}

    # single agent run
    task = _task_create(req, agent=resolved_agent)
    try:
        result = mod.forward('run',
            key=req.key,
            query=req.query,
            model=req.model,
            provider=req.provider,
            steps=req.steps,
            skills=req.skills,
            toolbox=req.toolbox or req.toolboxes,
            agent_type=resolved_agent,
            temperature=req.temperature,
            safety=req.safety,
            free=req.free,
            prompt=req.prompt,
            memory_ids=req.memory_ids,
            skill_ids=req.skill_ids,
            images=req.images,
            on_step=lambda s: _task_step(task, s),
        )
        _task_finish(task, _status_of(result), _summary_of(result))
        charge = _charge_run(req, task)
        return {"query": req.query, "agent_type": resolved_agent, "task_id": task["id"],
                "result": result, "charged": charge}
    except PermissionError as e:
        _task_finish(task, 'error', str(e))
        return {"query": req.query, "error": str(e), "code": 403}
    except Exception as e:
        _task_finish(task, 'error', str(e))
        return {"query": req.query, "error": str(e)}


@app.post("/run/stream")
def run_agent_stream(req: RunRequest):
    """Run the agent loop, streaming each executed step live as SSE events.

    Events (one JSON object per `data:` line):
        {"type": "step",       "step": {...}}                 — a tool step just executed
        {"type": "chain_step", "index": i, "agent": "name"}  — a chain stage is starting
        {"type": "done",       "result": [...]}               — single-agent run finished
        {"type": "done",       "chain": true, "results": []}  — chain finished
        {"type": "error",      "error": "..."}                — run failed
    """
    mod = get_mod()
    events: "queue.Queue" = queue.Queue()
    resolved_agent = req.agent_type or req.agent or mod.default_agent(req.key)

    def emit(ev):
        events.put(ev)

    task = _task_create(req, chain=bool(req.chain), agent=resolved_agent)

    def on_step(s):
        _task_step(task, s)
        emit({"type": "step", "step": s})

    def worker():
        try:
            if mod.model is None and not mod.harness_for(resolved_agent):
                _task_finish(task, 'error', 'No API key configured')
                emit({"type": "error", "error": "No API key configured for the selected provider — add or unlock a key in the Builder (model node)."})
                return
            if req.chain and len(req.chain) > 0:
                results = _run_chain(
                    mod, req,
                    on_step=on_step,
                    on_chain_step=lambda i, a: emit({"type": "chain_step", "index": i, "agent": a}),
                )
                errs = [r.get("error") for r in results if r.get("error")]
                _task_finish(task, 'error' if errs else 'done',
                             errs[0] if errs else (results[-1].get("summary", "") if results else ""))
                charge = _charge_run(req, task)
                emit({"type": "done", "chain": True, "task_id": task["id"],
                      "results": results, "charged": charge})
            else:
                result = mod.forward('run',
                    key=req.key,
                    query=req.query,
                    model=req.model,
                    provider=req.provider,
                    steps=req.steps,
                    skills=req.skills,
                    toolbox=req.toolbox or req.toolboxes,
                    agent_type=resolved_agent,
                    temperature=req.temperature,
                    safety=req.safety,
                    free=req.free,
                    prompt=req.prompt,
                    memory_ids=req.memory_ids,
                    skill_ids=req.skill_ids,
                    images=req.images,
                    on_step=on_step,
                )
                _task_finish(task, _status_of(result), _summary_of(result))
                charge = _charge_run(req, task)
                emit({"type": "done", "task_id": task["id"], "result": result,
                      "charged": charge})
        except PermissionError as e:
            _task_finish(task, 'error', str(e))
            emit({"type": "error", "error": str(e), "code": 403})
        except Exception as e:
            _task_finish(task, 'error', str(e))
            emit({"type": "error", "error": str(e)})
        finally:
            events.put(None)  # sentinel: stream over

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"  # keepalive comment so proxies don't cut the stream
                continue
            if ev is None:
                break
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 50117))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
