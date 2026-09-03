"""
agent api - thin FastAPI gateway over mod.forward()

All logic lives in agent/mod.py. The API just dispatches to forward().

Endpoints:
    GET  /health       - health check
    GET  /status       - module status
    GET  /schema       - tool schemas for the LLM
    GET  /agents       - list agent personas
    GET  /agents/{name} - get agent config
    POST /agents       - create agent    PUT /agents/{name}  DELETE /agents/{name}
    POST /agents/import - install a shared agent from its localfs CID
    GET  /harnesses    - external agent CLIs (claude code, codex) + availability
    GET  /chains       - list chain presets
    GET  /library      - unified index: prompts + tool docs + memory + agents (q/kind/tag filters)
    POST /library/upload      - upload a file as a prompt/tool doc/memory note/agent
    POST /library/upload/file - the same, as a multipart file post
    POST /library/import      - install anything from a shared localfs CID
    GET  /library/formats     - what an upload may look like + docs/uploads.md
    GET  /prompts      - prompt library      POST /prompts  DELETE /prompts/{id}
    POST /prompts/import - install a shared prompt from its localfs CID
    GET  /memory       - memory notes        POST /memory   DELETE /memory/{id}
    POST /memory/import - install a shared memory note from its localfs CID
    GET  /discover     - scan GitHub/npm/MCP/Glama/awesome-lists for tools (q/sources/kind)
    GET  /discover/sources - the source catalog + GitHub token state
    GET  /discover/item?id= - full record for one result (SKILL.md paths, readme)
    GET  /discover/doc?id=  - preview the document an install would add
    POST /discover/install  - install a scanned result into the library
    POST /discover/token    - owner: store a GitHub token (lifts rate limits)
    GET  /tools/installed  - external tool documents installed from the aggregator
    POST /tools/import     - install an external tool document from its CID
    DELETE /tools/installed/{id} - uninstall one
    GET  /conversations - the caller's saved console conversations (localfs-pinned)
    POST /conversations - upsert a conversation  DELETE /conversations/{id}
    POST /conversations/import - restore a shared conversation from its CID
    GET  /vaults       - the caller's key-value vaults (store-module-backed)
    POST /vaults       - create a vault          DELETE /vaults/{name}
    GET  /vaults/{name}?reveal= - entries (private values masked unless reveal)
    POST /vaults/{name}/entries - upsert {entry, value, private}
    DELETE /vaults/{name}/entries/{entry} - remove one entry
    GET  /vaults/public?address=&name= - anyone: a vault's public entries
    GET  /parts        - the agent box: model, memory module, toolbox, tools, prompt
    GET  /toolboxes    - tool bundles        POST /toolboxes  DELETE /toolboxes/{name}
    POST /toolboxes/{name}/snap   - snap a bundle onto the agent (unsnap to detach)
    POST /toolboxes/unsnap        - detach everything, back to the full tool set
    GET  /tools        - the whole registry: built-in + custom (?mods=1 adds the fleet)
    GET  /tools/mods   - the fleet on its own (?q= filters), one tool per module
    POST /tools        - host: create/update a custom tool  DELETE /tools/{name}
    POST /tools/{name}/run - host: execute one tool (the console's test run)
    POST /tools/select - host: pin the loadout to an exact list (null = toolboxes)
    GET  /memory/state - memory subsystem layers (working/episodic/semantic)
    GET  /memory/modules   - the memory modules an agent can be built with
    GET  /memory/retrieve?q= - retrieval across every layer at once, ranked
    GET  /memory/recall?q= - facts scored against a query
    POST /memory/remember  - store a durable fact   DELETE /memory/facts/{id}
    GET  /memory/episodes  - step trail    POST /memory/serve - own process :50119
    GET  /modules      - the fleet + each module's visibility (anyone)
    GET  /modules/{name}/tree - file list of a public module (anyone)
    GET  /modules/{name}/file?path= - one source file of a public module
    POST /modules/{name}/visibility - owner: flip one module public/private
    POST /modules/visibility - owner: flip the whole fleet + the default
    POST /modules/{name}/seal|unseal|restore - owner: the encrypted blob
    POST /privacy/key  - owner: fleet key state/export/import/passphrase
    GET  /whoami       - resolve a signed token to an address + role
    GET  /memory/exchanges - what you and the agent have said to each other,
                        scoped to your address (or your session, anonymous)
    GET  /credits      - deposit info + caller's credit balance/history
    POST /credits/deposit - verify a USDT/USDC/ETH tx hash, credit the on-chain sender
    GET  /credits/price   - ETH/USD the next native deposit is priced at
    GET  /owners          - owner: the owner and every co-owner
    POST /owners          - owner: add/remove a co-owner (owner rights + owner credits)
    POST /credits/grant   - owner: top up (+) or deduct (-) ANY account
    GET  /credits/treasury - owner: deposits in, provider credits out, margin kept
    POST /credits/topup   - owner: record API credits bought at a provider
    POST /credits/topup/verify - owner: book a top-up read off the provider key
    POST /credits/withdraw - owner: take earned margin out of the float
    POST /credits/config  - owner: set fee_rate / pricing knobs
    GET  /arena        - the ranked board + what the background process is doing
    GET  /arena/tasks  - the task pool, and this season's slice of it
    GET  /arena/matches?limit=&agent=&task= - recent matches, newest first
    GET  /arena/agents/{name} - one agent's record (rating, per-task, matches)
    GET  /arena/models - the same matches ranked by model: score, latency,
                         throughput, spend — plus the catalog to play
    GET  /arena/model?model= - one model's record (per-task, who it beat)
    GET  /arena/board/tasks  - per task, the models that played it, ranked
    POST /arena/gauntlet - admin: one agent, one task set, N models
    POST /arena/run    - admin: play a match (agent=, task=) or a whole round
    POST /arena/config - admin: the board's knobs + scheduler on/off
    POST /arena/tasks/draft - signed in: a description -> a task spec, written
                        by the task-builder agent (nothing is stored)
    POST /arena/tasks  - signed in: save a hand-written task into the pool
    DELETE /arena/tasks/{slug} - its author, or the host
    GET  /arena/openarena - the openarena bridge: up?, its pool, its entrants
    GET  /arena/openarena/tasks/{slug} - one task there, hidden cases hidden
    POST /arena/openarena/tasks - signed in: upload a task in that schema
    DELETE /arena/openarena/tasks/{slug} - its author, or the host
    GET  /arena/openarena/sources - benchmarks it can pull off the web
    POST /arena/openarena/import - signed in: a benchmark in as tasks
                        (preview=true converts and keeps nothing)
    POST /arena/openarena/enter - owner: our agent on openarena's own board
    GET  /tasks        - server-side task registry (running + recent runs)
    GET  /tasks/{id}   - one task with its step trace
    GET  /tasks/{id}/images - thumbnails of the images attached to that run
    POST /mcp          - MCP (Model Context Protocol) over Streamable HTTP:
                        the same handlers as JSON-RPC 2.0, 20 tools + resources
    GET  /mcp/schema   - the MCP tool list + how to connect a client
    POST /forward      - mod protocol entry point
    POST /run          - run the full agent loop
    POST /run/stream   - run the agent loop, streaming steps live (SSE)
    GET  /browser/models - LFM repos a runtime can load (liquidai catalog)
    POST /browser/completion - a tab answering a `browser` run's model request

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
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

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

from src import mcp             # the MCP server — same handlers, JSON-RPC over them
from src.liquid import BROWSER   # the mailbox a browser-model run waits on
from src.privacy.mod import SealError   # sealing failures map to a 400, not a 500

app = FastAPI(title="Agent API", version=VERSION, description="Autonomous coding agent API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── request models ───────────────────────────────────────────────────

class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: dict = {}
    key: Optional[str] = None

class RunRequest(BaseModel):
    query: str
    # both unset by default: the module resolves the provider (local first —
    # Mod.default_provider) and then that provider's own default model. A
    # hardcoded frontier model here meant every caller who named none spent.
    model: Optional[str] = None
    provider: Optional[str] = None
    steps: int = 10
    tools: Optional[List[str]] = None
    toolbox: Optional[str] = None           # toolbox name to snap on for this run
    toolboxes: Optional[List[str]] = None   # or several — tools = union of boxes
    temperature: float = 0.0
    safety: bool = False
    free: bool = False
    agent: Optional[str] = None
    agent_type: Optional[str] = None
    chain: Optional[List[dict]] = None
    prompt: Optional[str] = None          # system prompt override (library prompt or free text)
    memory: Optional[str] = None            # memory module for this run (default | ephemeral | dotted path)
    memory_ids: Optional[List[str]] = None  # library memory note ids injected as context
    tool_ids: Optional[List[str]] = None    # installed tool-doc ids injected as context
    images: Optional[List[str]] = None      # pasted images (data: URLs) — needs a vision model
    thumbs: Optional[List[str]] = None      # tiny copies of the same images, for the task registry
    browser_session: Optional[str] = None   # tab id a `browser` run generates in
    session: Optional[str] = None           # console conversation — makes the run a remembered exchange
    harness_args: Optional[dict] = None     # runner-specific knobs for a harness agent (chainmod: project, address, network)
    key: Optional[str] = None

class ToolRunRequest(BaseModel):
    name: str
    params: dict = {}
    key: Optional[str] = None

class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    goal: str = ""
    icon: str = ">_"
    tools: Optional[List[str]] = None
    model: Optional[str] = None
    memory: Optional[str] = None    # memory module: 'default' | 'ephemeral' | dotted path
    harness: Optional[str] = None   # 'claude' | 'codex' — run on that CLI instead
    key: Optional[str] = None

class AgentUpdateRequest(BaseModel):
    description: Optional[str] = None
    goal: Optional[str] = None
    icon: Optional[str] = None
    tools: Optional[List[str]] = None
    model: Optional[str] = None
    harness: Optional[str] = None
    memory: Optional[str] = None # memory module the agent thinks with
    clear_tools: bool = False    # explicit: reset to every tool
    clear_model: bool = False    # explicit: reset to default model
    clear_memory: bool = False   # explicit: back to the default memory module
    clear_harness: bool = False  # explicit: back to this module's own loop
    key: Optional[str] = None

class DefaultAgentRequest(BaseModel):
    """Pick the agent an unnamed run lands on. `name: null` clears the pick."""
    name: Optional[str] = None
    key: Optional[str] = None

class GrantRequest(BaseModel):
    address: str
    actions: Optional[List[str]] = None  # default: ['run', 'tool_run']
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

class ToolInstallRequest(BaseModel):
    id: str                               # discover result id, e.g. gh:owner/repo:skills/pdf
    path: Optional[str] = None            # explicit SKILL.md path within the repo
    key: Optional[str] = None

class ToolDocImportRequest(BaseModel):
    cid: str
    key: Optional[str] = None

class UploadRequest(BaseModel):
    text: str                             # the file's contents
    filename: Optional[str] = None        # helps detect the kind
    kind: Optional[str] = None            # prompt|tool|memory|agent, else auto
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
    network: str = "base"             # base | ethereum
    provider: Optional[str] = None    # earmark: openrouter | venice
    key: Optional[str] = None

class OwnersRequest(BaseModel):
    op: str = "add"                   # add | rm
    address: str
    key: Optional[str] = None

class CreditGrantRequest(BaseModel):
    address: str
    amount: float
    note: str = ""
    key: Optional[str] = None

class TopupRequest(BaseModel):
    provider: str                     # openrouter | venice
    amount: float                     # USD of API credits bought
    ref: str = ""                     # receipt / invoice id, for the books
    note: str = ""
    key: Optional[str] = None

class TopupVerifyRequest(BaseModel):
    provider: str = "openrouter"      # whose key to read the purchase off
    key: Optional[str] = None

class WithdrawRequest(BaseModel):
    amount: float
    note: str = ""
    key: Optional[str] = None

class CreditConfigRequest(BaseModel):
    fee_rate: Optional[float] = None          # 0.05 = a 5% margin over cost
    price_per_step: Optional[float] = None    # fallback price for unpriced runs
    cost_multiplier: Optional[float] = None   # safety factor on the estimate
    deposit_address: Optional[str] = None
    key: Optional[str] = None

class ArenaRunRequest(BaseModel):
    agent: Optional[str] = None      # None = the whole field
    task: Optional[str] = None       # None = this season's rotation
    model: Optional[str] = None
    steps: Optional[int] = None
    free: Optional[bool] = None
    key: Optional[str] = None

class TaskDraftRequest(BaseModel):
    """A plain description in, a task spec out — see /arena/tasks/draft."""
    description: str
    model: Optional[str] = None
    provider: Optional[str] = None
    free: bool = False
    steps: int = 4                   # the drafting agent's own budget, not the task's
    # which schema to draft: 'agent' grades the trace, 'openarena' grades a
    # program against test cases. `schema` is BaseModel's own name, so the
    # field is spelled out and the wire keeps the short one
    task_schema: str = Field('agent', alias='schema')
    key: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

class TaskSaveRequest(BaseModel):
    title: str
    prompt: str
    description: str = ""
    steps: Optional[int] = None      # the step budget agents get on this task
    files: Optional[dict] = None     # fixture seeded into the scratch dir
    scorers: Optional[List[dict]] = None
    slug: Optional[str] = None       # set = edit that task in place
    key: Optional[str] = None

class OpenArenaTaskRequest(BaseModel):
    """A task in the openarena schema — a statement plus the cases that grade
    it. Stored in the openarena module, judged by its sandbox."""
    title: str
    statement: str
    mode: str = "io"                 # io (stdin/stdout) | unit (imported)
    language: str = "any"            # any | python | javascript | bash
    tests: List[dict] = []           # {name, stdin, expect, hidden} | {name, program}
    starter: str = ""
    tags: Optional[List[str]] = None
    slug: Optional[str] = None
    timeout_ms: Optional[int] = None
    key: Optional[str] = None


class BenchImportRequest(BaseModel):
    """Pull a published benchmark in as openarena tasks. `preview` converts and
    keeps nothing — always the first call."""
    source: str = "humaneval"
    limit: int = 10
    offset: int = 0
    preview: bool = False
    url: Optional[str] = None        # json / html sources
    dataset: Optional[str] = None    # hf source
    config: Optional[str] = None
    split: Optional[str] = None
    style: Optional[str] = None      # humaneval | asserts | io | html
    language: Optional[str] = None
    max_cases: Optional[int] = None
    hide_after: Optional[int] = None
    split_asserts: Optional[bool] = None
    tags: Optional[List[str]] = None
    slug_prefix: Optional[str] = None
    refresh: bool = False
    key: Optional[str] = None


class OpenArenaEnterRequest(BaseModel):
    """Enter one of our agents as a competitor on openarena's own board."""
    agent: str
    name: Optional[str] = None
    model: Optional[str] = None
    steps: Optional[int] = None
    free: Optional[bool] = None
    key: Optional[str] = None


class ArenaGauntletRequest(BaseModel):
    """One agent, one set of tasks, every model in turn — the round the model
    board is built out of."""
    # ids, or {"model": ..., "provider": ...} where the catalog isn't the
    # module's default
    models: List[Any]
    agent: Optional[str] = None            # None = the first eligible agent
    tasks: Optional[List[str]] = None      # None = this season's rotation
    steps: Optional[int] = None
    free: bool = False                     # FREE MODE would ignore every id here
    key: Optional[str] = None


class ArenaConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    free: Optional[bool] = None            # run matches on zero-cost models
    model: Optional[str] = None
    steps: Optional[int] = None
    period_hours: Optional[float] = None   # 24 = a round a day
    poll_seconds: Optional[int] = None     # how fast a new agent is spotted
    tasks_per_round: Optional[int] = None
    max_matches: Optional[int] = None
    harnesses: Optional[bool] = None       # let CLI-backed agents compete
    scheduler: Optional[bool] = None       # start/stop the background process
    openarena: Optional[bool] = None       # pull openarena's tasks into the pool
    openarena_tasks: Optional[int] = None  # how many of them (0 = all)
    openarena_steps: Optional[int] = None  # step budget on a program task
    key: Optional[str] = None

class BrowserCompletionRequest(BaseModel):
    """A tab answering the generation request a `browser` run is blocked on."""
    id: str                            # the request id from the model_request event
    text: Optional[str] = None
    error: Optional[str] = None

class VaultCreateRequest(BaseModel):
    name: str
    key: Optional[str] = None

class VaultEntryRequest(BaseModel):
    entry: str
    value: str
    private: bool = True
    key: Optional[str] = None

class VisibilityRequest(BaseModel):
    visibility: str                      # 'public' | 'private'
    passphrase: Optional[str] = None     # if the fleet key is passphrase-wrapped
    key: Optional[str] = None

class SealRequest(BaseModel):
    passphrase: Optional[str] = None
    force: bool = False
    key: Optional[str] = None

class PrivacyKeyRequest(BaseModel):
    op: str = 'state'                    # state | export | import | passphrase
    passphrase: Optional[str] = None
    current: Optional[str] = None
    key_b64: Optional[str] = None
    key: Optional[str] = None


# ── lazy mod singleton ───────────────────────────────────────────────

_mod = None

def get_mod():
    global _mod
    if _mod is None:
        from src.mod import Mod
        _mod = Mod()
    return _mod


def signed_in(key) -> bool:
    """Did this request carry an identity at all?

    A key of None means "the process itself" to mod.forward — which is how a
    local CLI call is the host. Over HTTP that is wrong: this API is proxied to
    the open internet, so a request with no key is an anonymous stranger, not
    the server. Host-only routes say so with this before they dispatch.
    """
    return bool(key and str(key).strip())


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


def _task_provider(req: "RunRequest") -> str:
    """The provider this run will actually use — local unless asked otherwise."""
    if req.provider:
        return req.provider
    try:
        return get_mod().default_provider()
    except Exception:
        return "openrouter"


def _task_model(req: "RunRequest") -> str:
    """The model this run will actually use.

    A FREE MODE run ignores req.model and resolves a zero-cost one, so the
    requested model would be a lie in the ledger and in the console's trace.
    Resolved on the request's own provider — a free id from another catalog
    would be a lie too. A request that named no model at all is answered the
    same way the loop answers it: with that provider's default.
    """
    mod = get_mod()
    if not req.free:
        if req.model:
            return req.model
        try:
            return mod.DEFAULT_MODELS.get(_task_provider(req)) or ''
        except Exception:
            return ''
    try:
        return mod.free_model(_task_provider(req)) or req.model
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
        "provider": _task_provider(req),
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


MAX_CALLS = 60         # per-call cost rows kept on a task


def _task_usage(t: dict, call: dict) -> None:
    """Record what one model call cost, as the run makes it.

    The run tally is only readable once the run is over; a caller watching a
    long run wants the number while it is being spent, so each call lands
    here (and on the stream) the moment it resolves.
    """
    if not isinstance(call, dict):
        return
    with TASKS_LOCK:
        row = {k: call.get(k) for k in
               ("call", "step", "model", "cost", "priced",
                "prompt_tokens", "completion_tokens")}
        t.setdefault("calls", []).append(row)
        if len(t["calls"]) > MAX_CALLS:
            t["calls"] = t["calls"][-MAX_CALLS:]
        t["cost"] = round(float(call.get("total") or 0.0), 8)
        t["tokens"] = int(t.get("tokens") or 0) + int(call.get("prompt_tokens") or 0) \
            + int(call.get("completion_tokens") or 0)


def _usage_of(t: dict) -> dict:
    """What this run cost on the provider key — the number the console shows
    under the answer. Present whether or not anyone was charged for it: an
    owner run costs real money too, it is just not billed to a ledger."""
    with TASKS_LOCK:
        calls = list(t.get("calls") or [])
        # the model that actually answered, not the one that was asked for —
        # FREE MODE resolves its own, and a run billed on what it really used
        # should say what it really used
        return {"cost": t.get("cost"), "tokens": t.get("tokens") or 0,
                "calls": len(calls),
                "model": (calls[-1].get("model") if calls else None) or t.get("model"),
                "provider": t.get("provider"),
                "charged": t.get("charged"),
                "priced": all(c.get("priced") for c in calls) if calls else False,
                "per_call": calls}


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


def _run_budget(req: "RunRequest", t: dict):
    """A spend ceiling for a run that will be billed, or None if it won't be.

    Charges are clamped to the balance, so without this a dust account could
    start an Opus run and leave the module holding the overrun. The ceiling is
    what the caller can actually pay for at the current margin.
    """
    if req.free or not t.get("user"):
        return None
    mod = get_mod()
    try:
        # a provider that can't cost us anything gets no ceiling: an LFM in
        # the caller's own tab shouldn't stop on an empty credit balance
        if mod.is_free_provider(req.provider) or mod.is_owner(req.key):
            return None
        balance = mod.credits.balance(t["user"])
    except Exception:
        return None
    fee = 1 + max(0.0, mod.credits.fee_rate)
    return lambda cost: cost * fee < balance


def _charge_run(req: "RunRequest", t: dict) -> Optional[dict]:
    """Bill a finished non-owner run against the caller's credit ledger.

    Owners run free on their own key; `free` runs (free models) cost
    nothing. Everyone else pays what the run actually burned on the
    module's provider key plus the margin — that is what turns their
    deposit into the OpenRouter/Venice credits the run just spent.

    The cost comes off this thread's meter, so it has to be read whether
    or not the caller is billed: an unread tally would be handed to the
    next run on this thread.
    """
    mod = get_mod()
    try:
        usage = mod.meter.take()
    except Exception:
        usage = {}
    # every run reports what it burned on the key, billed or not — an owner
    # run costs real money too, it just isn't charged to anyone
    if usage.get("calls"):
        with TASKS_LOCK:
            if usage.get("priced"):
                t["cost"] = round(usage.get("cost", 0.0), 8)
            t["tokens"] = int(usage.get("prompt_tokens") or 0) + \
                int(usage.get("completion_tokens") or 0)
    if req.free or not t.get("user"):
        return None
    try:
        if mod.is_free_provider(req.provider) or mod.is_owner(req.key):
            return None
        usage = dict(usage or {}, steps=t["steps"])
        charge = mod.charge_run(t["user"], usage, note=t["query"][:80])
        if charge.get("charged"):
            with TASKS_LOCK:
                t["charged"] = charge["charged"]
        return charge
    except Exception:
        return None


# ── routes (thin wrappers over mod.forward) ──────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "module": "agent", "version": app.version,
            "mcp": {"endpoint": "POST /mcp", "schema": "GET /mcp/schema",
                    "tools": len(mcp.TOOLS)}}

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

@app.get("/schema")
def get_schema():
    """The tool schemas the LLM is handed for the current loadout."""
    return get_mod().tool_schema()

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
            "models": mod.provider_models(key),
            "default_model": default_model,
            "configured": info.get("configured", False),
            "encrypted": info.get("encrypted", False),
            "unlocked": info.get("unlocked", False),
            "remembered": info.get("remembered", False),
            "keyless": info.get("keyless", False),
            # where the compute is: the hosted providers bill, these three don't
            "runtime": mod.LOCAL_RUNTIMES.get(key),
            "hint": mod.LOCAL_HINTS.get(key),
            "free": key in mod.LOCAL_PROVIDERS,
        })
    # local first: a console that opens on a provider nobody has to pay for is
    # the whole point of shipping the LFM runtimes (Mod.default_provider)
    return {"providers": providers, "default": mod.default_provider()}

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
        state = (mod.LOCAL_HINTS.get(key, "no key needed") if info.get("keyless") else
                 "ready" if info.get("configured") else
                 "locked" if info.get("encrypted") and not info.get("unlocked") else "no key")
        providers.append({"value": key, "label": key, "hint": state})
        models_by[key] = mod.provider_models(key)
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
             "default": mod.default_provider(), "options": providers},
            {"name": "model", "label": "MODEL", "type": "select", "depends": "provider",
             "options_by": models_by, "default_by": default_by},
            {"name": "toolbox", "label": "TOOLBOX", "type": "select", "default": None,
             "options": [{"value": None, "label": "auto", "hint": "persona default"}] + toolboxes},
            {"name": "steps", "label": "MAX STEPS", "type": "number",
             "default": 10, "min": 1, "max": 50, "step": 1,
             "hint": "agent-loop iterations — more steps, more provider spend"},
            {"name": "temperature", "label": "TEMP", "type": "number",
             "default": 0.0, "min": 0.0, "max": 2.0, "step": 0.1},
            {"name": "safety", "label": "SAFETY REVIEW", "type": "toggle", "default": False,
             "hint": "safety agent reviews each plan"},
            {"name": "free", "label": "FREE MODE", "type": "toggle", "default": False,
             "hint": "free models only; run is never billed"},
        ],
        "credits": {"info": "/credits", "deposit": "/credits/deposit",
                    "price": "/credits/price",
                    "treasury": "/credits/treasury", "topup": "/credits/topup",
                    "topup_verify": "/credits/topup/verify",
                    "balance": "/balance", "whoami": "/whoami"},
    }

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
    return {"owner": mod._owner, "has_owner": bool(mod._owner),
            "co_owners": list(getattr(mod, "_co_owners", []))}

@app.get("/owners")
def get_owners(key: Optional[str] = None):
    """The owner and every co-owner. Owner-only."""
    try:
        return get_mod().owners('list', key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/owners")
def set_owners(req: OwnersRequest):
    """Add or remove a co-owner — owner standing plus the owner's credits.

    Primary owner only: a co-owner can spend the owner's credits but cannot
    mint another co-owner.
    """
    try:
        return get_mod().owners(req.op, address=req.address, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e), "code": 400}

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
    """Verify a USDT/USDC/ETH transfer to the deposit address by tx hash.

    Credits go to the ON-CHAIN SENDER of the transfer (so a hash can't
    be claimed by someone else), and each hash is credited only once. ETH
    is priced at the Chainlink ETH/USD feed of the chain it landed on.
    `provider` earmarks the deposit for the openrouter or venice key.
    """
    try:
        return get_mod().credit_deposit(req.tx_hash, req.network, req.provider)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"verification failed: {e}"}

@app.get("/credits/price")
def credit_price(network: str = "base"):
    """ETH in USD — what a native deposit on `network` is credited at right now."""
    try:
        return get_mod().credit_price(network)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

@app.post("/credits/grant")
def credit_grant(req: CreditGrantRequest):
    """Top up (+) or deduct (−) any account's credits. Owner only.

    The owner funds the provider keys directly, so they never buy credits
    for themselves — this is how they hand credit to a guest address and
    take it back. A deduction is clamped at zero.
    """
    try:
        return get_mod().credit_grant(req.address, req.amount, req.note, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.get("/credits/treasury")
def credits_treasury(key: Optional[str] = None, live: bool = True):
    """Owner: deposits in, provider credits out, margin kept.

    `topup_needed` is the operative number — unspent guest credits at cost,
    minus what the OpenRouter/Venice keys still hold.
    """
    try:
        return get_mod().credits_treasury(key, live=live)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except Exception as e:
        return {"error": str(e)}

@app.post("/credits/topup")
def credit_topup(req: TopupRequest):
    """Owner: record API credits bought at a provider out of the deposit float."""
    try:
        return get_mod().credit_topup(req.provider, req.amount, req.ref,
                                      req.note, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/credits/topup/verify")
def credit_topup_verify(req: TopupVerifyRequest):
    """Owner: book a top-up by reading it back off the provider key.

    Neither provider sells credits over an API, so the purchase itself
    happens on their page (`topup.url` in the treasury); this confirms it
    landed and books the amount that actually arrived.
    """
    try:
        return get_mod().credit_topup_verify(req.provider, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/credits/withdraw")
def credit_withdraw(req: WithdrawRequest):
    """Owner: take earned margin out of the float."""
    try:
        return get_mod().credit_withdraw(req.amount, req.note, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.post("/credits/config")
def credit_config(req: CreditConfigRequest):
    """Owner: set the margin (fee_rate) and the pricing knobs."""
    try:
        return get_mod().credit_config(
            key=req.key,
            **{k: v for k, v in (("fee_rate", req.fee_rate),
                                 ("price_per_step", req.price_per_step),
                                 ("cost_multiplier", req.cost_multiplier),
                                 ("deposit_address", req.deposit_address))
               if v is not None})
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

@app.delete("/tasks")
def clear_tasks(status: str = "finished"):
    """Drop task rows from the registry: 'error', 'done', 'finished' or 'all'.

    A run that failed for a reason you've already dealt with is just noise in
    everyone's list, and the registry is in-memory anyway — dismissing is the
    same act as letting it age out, done on purpose. Running tasks are never
    dropped: the row is how you watch them.
    """
    keep = {"running"}
    wanted = {"error": {"error"}, "done": {"done"},
              "finished": {"done", "error"},
              "all": {"done", "error", "cancelled"}}.get(status)
    if wanted is None:
        return {"error": f"unknown status {status!r}",
                "options": ["error", "done", "finished", "all"]}
    with TASKS_LOCK:
        gone = [tid for tid, t in TASKS.items()
                if t.get("status") in wanted and t.get("status") not in keep]
        for tid in gone:
            TASKS.pop(tid, None)
        left = len(TASKS)
    return {"cleared": len(gone), "remaining": left, "status": status}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """Dismiss one task row. A running task is left alone — cancel it first."""
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return {"error": f"unknown task: {task_id}"}
        if t.get("status") == "running":
            return {"error": "task is still running — cancel it before dismissing it",
                    "code": 409}
        TASKS.pop(task_id, None)
        return {"deleted": task_id, "remaining": len(TASKS)}

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

# ── module visibility: public modules anyone can audit ──────────────
#
# The three reads are deliberately open. A module is a thing you are asked
# to trust with a host; "public" has to mean a stranger can walk its source
# without an account, or it means nothing. The writes are owner-only and
# say so in mod.py, not here.

@app.get("/modules")
def list_modules(q: str = ""):
    """The fleet with each module's visibility. Anyone.

    Private modules are listed by name with nothing else — that they exist
    is not the secret.
    """
    return get_mod().forward('modules', q=q)

@app.get("/modules/{name}/tree")
def module_tree(name: str):
    """File list of a public module — where an audit starts."""
    try:
        return get_mod().forward('module_tree', name=name)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e), "code": 404}
    except ValueError as e:
        return {"error": str(e), "code": 400}

@app.get("/modules/{name}/file")
def module_file(name: str, path: str):
    """One source file out of a public module."""
    try:
        return get_mod().forward('module_file', name=name, path=path)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e), "code": 404}
    except ValueError as e:
        return {"error": str(e), "code": 400}

@app.post("/modules/{name}/visibility")
def set_module_visibility(name: str, req: VisibilityRequest):
    """Owner: flip one module. private seals it, public unseals it."""
    return _privacy_write('module_visibility', req.key, name=name,
                          visibility=req.visibility, passphrase=req.passphrase)

@app.post("/modules/visibility")
def set_all_visibility(req: VisibilityRequest):
    """Owner: flip the whole fleet, and the default new modules inherit."""
    return _privacy_write('modules_visibility', req.key,
                          visibility=req.visibility, passphrase=req.passphrase)

@app.post("/modules/{name}/seal")
def seal_module(name: str, req: SealRequest):
    """Owner: re-seal a private module after editing it."""
    return _privacy_write('module_seal', req.key, name=name,
                          passphrase=req.passphrase)

@app.post("/modules/{name}/unseal")
def unseal_module(name: str, req: SealRequest):
    """Owner: drop the blob and put the tree back under git."""
    return _privacy_write('module_unseal', req.key, name=name)

@app.post("/modules/{name}/restore")
def restore_module(name: str, req: SealRequest):
    """Owner: unpack a sealed blob back into source — the clone side."""
    return _privacy_write('module_restore', req.key, name=name,
                          passphrase=req.passphrase, force=req.force)

@app.post("/privacy/key")
def privacy_key(req: PrivacyKeyRequest):
    """Owner: the fleet key — state, export, import, or set a passphrase."""
    return _privacy_write('privacy_key', req.key, op=req.op,
                          passphrase=req.passphrase, current=req.current,
                          key_b64=req.key_b64)

def _privacy_write(action: str, key, **kwargs):
    """Owner-gated privacy call, with the failure modes mapped to codes.

    A sign-in check first: over HTTP a missing key is a stranger, not the
    host (see signed_in), and these calls decide what the world can read.
    """
    if not signed_in(key):
        return {"error": "sign in as the owner to change module visibility",
                "code": 401}
    try:
        return get_mod().forward(action, key=key, **kwargs)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e), "code": 404}
    except (ValueError, SealError) as e:
        return {"error": str(e), "code": 400}
    except Exception as e:
        return {"error": str(e), "code": 500}


# ── agents (from agents/ registry) ──────────────────────────────────

@app.get("/agents")
def list_agents(key: Optional[str] = None):
    """List all agent personas from agents/ directory.

    `default` is the one a run lands on when none is named. A signed-in
    caller's own pick wins (POST /agents/default); with none on record it is
    Claude Code for the host and the native agent for everyone else — so a
    console can preselect it instead of deciding for itself. `default_source`
    says which of the two answered, and `default_pick` is the caller's own
    pick even when it isn't runnable right now.
    """
    mod = get_mod()
    info = mod.default_agent_info(key)
    return {**mod.forward('agents'), "default": info["default"],
            "default_source": info["source"], "default_pick": info["pick"]}


@app.post("/agents/default")
def set_default_agent(req: DefaultAgentRequest):
    """Pick the agent this caller's unnamed runs land on.

    Signed-in only — the pick is remembered per address, so it follows the
    wallet across browsers rather than living in one tab's localStorage.
    """
    try:
        return get_mod().set_default_agent(req.name, key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e), "code": 400}

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
            icon=req.icon, tools=req.tools, model=req.model,
            memory=req.memory, harness=req.harness, key=req.key)
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
        tools = None if req.clear_tools else (req.tools if req.tools is not None else ...)
        model = None if req.clear_model else (req.model if req.model is not None else ...)
        harness = None if req.clear_harness else (req.harness if req.harness is not None else ...)
        memory = None if req.clear_memory else (req.memory if req.memory is not None else ...)
        result = mod.agents.update(
            name=name, description=req.description, goal=req.goal,
            icon=req.icon, tools=tools, model=model, memory=memory,
            harness=harness, key=req.key)
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

# ── library (prompts / tool docs / memory / agent market) ───────────

@app.get("/library")
def get_library(q: Optional[str] = None, kind: Optional[str] = None,
                tag: Optional[str] = None):
    """Unified filterable index across prompts, tool docs, memory, agents."""
    return get_mod().library.items(q=q, kind=kind, tag=tag)

@app.get("/library/formats")
def library_formats():
    """What an upload may look like, plus docs/uploads.md for the console."""
    return get_mod().library.formats()

@app.post("/library/upload")
def library_upload(req: UploadRequest):
    """Upload one file into the library — prompt, tool doc, note or agent.

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

# ── discover (internet-wide tool aggregator) ────────────────────────
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
        return get_mod().discover.tool_doc(id, path)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

@app.post("/discover/install")
def discover_install(req: ToolInstallRequest):
    """Install a scanned result into the library as an external tool doc.

    Signed-in callers only — the installer owns what they added."""
    try:
        return get_mod().tool_install(req.id, req.path, key=req.key)
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

@app.get("/tools/installed")
def list_installed_tools():
    """Tool documents installed from the aggregator, with their owner."""
    lib = get_mod().library
    return {"tools": [{**s, **lib.tool_owner(s)} for s in lib.installed_tools()],
            "host": lib.identity.host}

@app.post("/tools/import")
def import_installed_tool(req: ToolDocImportRequest):
    """Install a tool document from its localfs CID (the share path)."""
    try:
        return get_mod().library.tool_import(req.cid.strip(), key=req.key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

@app.delete("/tools/installed/{tool_id}")
def delete_installed_tool(tool_id: str, key: Optional[str] = None):
    """Uninstall a tool document. Whoever installed it, or the host."""
    try:
        return get_mod().library.tool_rm(tool_id, key=key)
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

# ── toolboxes (snap-on tool bundles) ─────────────────────────────────

@app.get("/parts")
def agent_parts():
    """What the agent is made of — model, memory module, toolbox, tools, prompt.

    One call for the whole box, so a console (or anyone auditing a run) can see
    every sub-component and what it could be swapped for, instead of stitching
    four endpoints together.
    """
    return get_mod().forward('parts')

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

# ── tools (built-in + custom shell tools + the fleet, in one registry) ─
# A custom tool runs shell and a fleet tool calls another module, so writing
# or running one is host-only — library tool documents are text for exactly
# that reason. Reading the registry is public.

@app.get("/tools")
def list_tools(mods: bool = False, q: str = "", limit: int = 40):
    """Every tool the agent can call, in one list the console can render.

    The default list is the loadout material: shipped tools + custom shell
    tools. `mods=true` adds the fleet — three hundred modules, so it takes a
    `q` and a `limit` and searches server-side instead of shipping the lot.
    """
    mod = get_mod()
    active = mod.active_tools()           # None = nothing filtered out
    tools = [{**t, "active": active is None or t["name"] in active}
             for t in mod.tools.items(mods=mods, q=q, limit=limit)]
    # a fleet tool that's switched on stays visible even when it's not in the
    # current page of search results — the console must be able to switch it off
    shown = {t["name"] for t in tools}
    tools += [{**mod.tools.mods.get(n), "builtin": False, "active": True,
               "params": mod.tools.mods.schema([n])[n]["params"]}
              for n in (active or []) if n not in shown and mod.tools.is_mod(n)]
    return {"tools": tools, "snapped": mod.snapped(),
            "toolboxes": mod.toolboxes.items(), "host": mod._owner,
            "fleet": len(mod.tools.mods.ls()), "mods": mods, "q": q}

@app.post("/tools")
def save_tool(req: ToolRequest):
    """Create or update a custom tool. Host (or a granted admin) only."""
    if not signed_in(req.key):
        return {"error": "sign in — a custom tool runs shell on the host", "code": 401}
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
    if not signed_in(key):
        return {"error": "sign in to change the tool registry", "code": 401}
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
    if not signed_in(req.key):
        return {"error": "sign in to change the loadout", "code": 401}
    try:
        return get_mod().forward('select', key=req.key, tools=req.tools)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}

@app.get("/tools/mods")
def list_mod_tools(q: str = "", limit: int = 60):
    """The fleet on its own: one entry per module, with the functions it
    declares. These are potential tools — off the default loadout until
    someone switches them on."""
    mod = get_mod()
    active = mod.active_tools() or []
    items = mod.tools.mods.items(q, limit)
    return {"mods": [{**e, "active": e["name"] in active} for e in items],
            "total": len(items), "fleet": len(mod.tools.mods.ls()), "q": q}

@app.post("/tools/{name}/run")
def run_tool(name: str, req: ToolRunRequest):
    """Execute one tool — the console's 'try it' button.

    A built-in is open (write tools stay inside the caller's sandbox); a
    custom shell tool or a fleet module is admin, same as creating one.
    """
    mod = get_mod()
    if mod.tools.kind(name) != 'builtin' and not signed_in(req.key):
        return {"tool": name, "code": 401,
                "error": "sign in — this call runs shell or another module on the host"}
    try:
        if mod.tools.kind(name) == 'builtin':
            if name in ('write', 'edit', 'patch'):
                allowed = mod.allowed_paths_for(req.key)
                fp = req.params.get('file_path', '')
                if fp and allowed is not None:
                    from src.mod import check_path_allowed
                    if not check_path_allowed(fp, allowed):
                        return {"tool": name, "code": 403,
                                "error": f"Permission denied: cannot write to {fp}"}
            return {"tool": name, "result": mod.run_tool(name, **req.params)}
        return {"tool": name,
                "result": mod.forward('tool_run', key=req.key, name=name,
                                      params=req.params)}
    except PermissionError as e:
        return {"tool": name, "error": str(e), "code": 403}
    except Exception as e:
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

@app.get("/memory/modules")
def memory_modules():
    """The memory modules an agent can be built with, and which is default."""
    return get_mod().forward('memories')

@app.get("/memory/retrieve")
def memory_retrieve(q: str, k: int = 5, layers: Optional[str] = None,
                    session: Optional[str] = None, min_score: Optional[float] = None,
                    key: Optional[str] = None):
    """Retrieval across every memory layer at once — the same call the agent's
    own recall tool makes, so the console shows exactly what a run would get.

    Scoped like the compiled prompt: a signed-in caller retrieves their own
    past turns, an anonymous one only the session they are sitting in.
    """
    return get_mod().forward('retrieve', key=key, query=q, k=k, session=session,
                             min_score=min_score,
                             layers=[l for l in (layers or '').split(',') if l] or None)

@app.get("/memory/facts")
def memory_facts():
    return {"facts": get_mod().forward('facts')}

@app.get("/memory/episodes")
def memory_episodes(n: int = 50, session: Optional[str] = None):
    """Recent episode trail (every step the agent executed)."""
    return {"episodes": get_mod().forward('episodes', n=n, session=session)}

@app.get("/memory/exchanges")
def memory_exchanges(n: int = 20, session: Optional[str] = None,
                     key: Optional[str] = None):
    """What this caller and the agent have said to each other.

    This is the layer the next run is compiled from, so it is scoped the same
    way: a signed-in caller gets every turn recorded under their address, an
    anonymous one gets the console session they are sitting in and nothing
    that belongs to a signed-in visitor.
    """
    return get_mod().forward('exchanges', key=key, n=n, session=session)

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

# ── arena (same tasks, one ranked board) ─────────────────────────────

@app.get("/arena")
def arena_board():
    """The ranked board plus what the background process is up to."""
    return get_mod().forward('arena')

@app.get("/arena/tasks")
def arena_tasks():
    """The task pool, and which slice of it this season plays."""
    return get_mod().forward('arena_tasks')

@app.post("/arena/tasks/draft")
def arena_task_draft(req: TaskDraftRequest):
    """Turn a plain description into a task spec with the task-builder agent.

    The draft comes back for review — nothing is stored until the caller saves
    it. This is a model run, so it needs whatever a run needs: the host, a
    granted address, or credits.
    """
    if not signed_in(req.key):
        return {"error": "sign in to draft a task", "code": 401}
    try:
        return get_mod().forward('arena_task_draft', key=req.key,
                                 description=req.description, model=req.model,
                                 provider=req.provider, free=req.free,
                                 steps=req.steps, schema=req.task_schema)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

@app.post("/arena/tasks")
def arena_task_add(req: TaskSaveRequest):
    """Store a hand-written task. It joins the pool every agent plays.

    Passing `slug` edits that task in place, which keeps the scores already
    recorded against it — and takes being its author.
    """
    if not signed_in(req.key):
        return {"error": "sign in — a task is filed under the address that wrote it",
                "code": 401}
    spec = {"title": req.title, "description": req.description, "prompt": req.prompt,
            "steps": req.steps, "setup": {"files": req.files or {}},
            "scorers": req.scorers or []}
    try:
        return {"task": get_mod().forward('arena_task_add', key=req.key,
                                          spec=spec, slug=req.slug)}
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}

@app.delete("/arena/tasks/{slug}")
def arena_task_rm(slug: str, key: Optional[str] = None):
    """Remove a hand-written task. Its author or the host."""
    try:
        return get_mod().forward('arena_task_rm', key=key, slug=slug)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": str(e)}

# ── the openarena schema (statement + graded cases, judged next door) ─

@app.get("/arena/openarena")
def arena_openarena():
    """The bridge: is openarena up, what it holds, who is entered there.

    Its own health is not folded into /arena — a neighbour that is down must
    not slow the board's polling to its timeout.
    """
    return get_mod().forward('openarena')

@app.get("/arena/openarena/tasks/{slug}")
def arena_openarena_task(slug: str):
    """One openarena task in full, as an entrant sees it — hidden cases keep
    their names and give up nothing else."""
    try:
        return get_mod().forward('openarena_task', slug=slug)
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

@app.post("/arena/openarena/tasks")
def arena_openarena_task_add(req: OpenArenaTaskRequest):
    """Upload a task in the openarena schema.

    It is stored over there, not copied here: the same task, the same hidden
    cases and the same judge whether it is played from this board or theirs.
    """
    if not signed_in(req.key):
        return {"error": "sign in — a task is filed under the address that wrote it",
                "code": 401}
    spec = {k: v for k, v in req.dict().items() if k != 'key' and v is not None}
    try:
        return {"task": get_mod().forward('openarena_task_add', key=req.key,
                                          spec=spec)}
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

@app.delete("/arena/openarena/tasks/{slug}")
def arena_openarena_task_rm(slug: str, key: Optional[str] = None):
    """Delete an openarena task. Its author, or the host — a seeded or imported
    task has no address for an author, so only the host can drop one."""
    try:
        return get_mod().forward('openarena_task_rm', key=key, slug=slug)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/arena/openarena/sources")
def arena_openarena_sources():
    """The benchmarks openarena can pull off the web."""
    try:
        return get_mod().forward('openarena_sources')
    except Exception as e:
        return {"error": str(e)}

@app.post("/arena/openarena/import")
def arena_openarena_import(req: BenchImportRequest):
    """Convert a published benchmark into tasks — HumanEval, MBPP, CodeContests,
    a HuggingFace dataset, a JSON url or a scraped problem page.

    `preview: true` converts and keeps nothing, which is the call to make first.
    """
    if not signed_in(req.key):
        return {"error": "sign in to import a benchmark into the arena", "code": 401}
    opts = {k: v for k, v in req.dict().items()
            if k not in ('key', 'source', 'preview') and v is not None}
    try:
        return get_mod().forward('openarena_import', key=req.key,
                                 source=req.source, preview=req.preview, **opts)
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

@app.post("/arena/openarena/enter")
def arena_openarena_enter(req: OpenArenaEnterRequest):
    """Enter one of our agents on openarena's own board.

    Host only: over there the entrant is made to play by calling back into this
    module's /run, which spends the host's provider key.
    """
    if not signed_in(req.key):
        return {"error": "sign in — entering an agent spends the host's key",
                "code": 401}
    try:
        return {"entrant": get_mod().forward('openarena_enter', key=req.key,
                                             agent=req.agent, name=req.name,
                                             model=req.model, steps=req.steps,
                                             free=req.free)}
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except (ValueError, KeyError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

# ── the same matches, ranked by model ────────────────────────────────

@app.get("/arena/models")
def arena_models():
    """The model board: rank, score, latency, throughput and spend per model,
    plus the catalog a gauntlet can be pointed at."""
    return get_mod().forward('arena_models')

@app.get("/arena/model")
def arena_model(model: str):
    """One model's record. A query param, not a path one — model ids carry
    slashes and colons ('nvidia/nemotron-3-ultra-550b-a55b:free')."""
    return get_mod().forward('arena_model', model=model)

@app.get("/arena/board/tasks")
def arena_task_board():
    """Every played task, hardest first, with the models ranked underneath."""
    return get_mod().forward('arena_task_board')

@app.post("/arena/gauntlet")
def arena_gauntlet(req: ArenaGauntletRequest):
    """Rank models against each other: one agent, one task set, N models.

    Host only, and the one place on the board that will run a paid model — a
    named model is not FREE MODE, so this spends the host's provider key.
    """
    if not signed_in(req.key):
        return {"error": "sign in — a gauntlet spends steps on the host's key",
                "code": 401}
    try:
        return get_mod().forward('arena_gauntlet', key=req.key, models=req.models,
                                 agent=req.agent, tasks=req.tasks, steps=req.steps,
                                 free=req.free, reason='gauntlet')
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": f"unknown task: {e}"}
    except ValueError as e:
        return {"error": str(e)}

@app.get("/arena/matches")
def arena_matches(limit: int = 50, agent: Optional[str] = None,
                  task: Optional[str] = None):
    return get_mod().forward('arena_matches', limit=limit, agent=agent, task=task)

@app.get("/arena/agents/{name}")
def arena_card(name: str):
    """One agent's record: rating, per-task scores, recent matches."""
    return get_mod().forward('arena_card', agent=name)

@app.post("/arena/run")
def arena_run(req: ArenaRunRequest):
    """Play a match now — one agent on one task, or the whole field.

    Admin: a round spends real steps on the module's provider key, so it is
    not something a passer-by gets to start.
    """
    if not signed_in(req.key):
        return {"error": "sign in — an arena round spends steps on the host's key",
                "code": 401}
    try:
        return get_mod().forward('arena_run', key=req.key, agent=req.agent,
                                 task=req.task, model=req.model, steps=req.steps,
                                 free=req.free, reason='manual')
    except PermissionError as e:
        return {"error": str(e), "code": 403}
    except KeyError as e:
        return {"error": f"unknown task: {e}"}

@app.post("/arena/config")
def arena_config(req: ArenaConfigRequest):
    """Set the board's knobs, and start or stop the background process."""
    if not signed_in(req.key):
        return {"error": "sign in to configure the arena", "code": 401}
    mod = get_mod()
    fields = {k: v for k, v in req.dict().items()
              if v is not None and k not in ('key', 'scheduler')}
    try:
        if fields:
            mod.forward('arena_config', key=req.key, **fields)
        if req.scheduler is not None:
            mod.forward('arena_scheduler', key=req.key, on=req.scheduler)
        return mod.forward('arena_status')
    except PermissionError as e:
        return {"error": str(e), "code": 403}


@app.on_event("startup")
def _start_arena():
    """Bring the board's background process up with the API.

    Off-switch: ARENA_SCHEDULER=0 in the environment, for a host that wants the
    arena on demand only. The thread waits before its first tick, so a boot
    that gets restarted twice in a row never starts a round.
    """
    if os.environ.get("ARENA_SCHEDULER", "1") in ("0", "false", "no"):
        return
    try:
        get_mod().arena_scheduler(True)
    except Exception as e:
        print(f"arena scheduler not started: {e}")


def _run_chain(mod, req: RunRequest, on_step=None, on_chain_step=None, budget=None,
               on_usage=None, on_live=None):
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
                memory=req.memory,
                memory_ids=req.memory_ids,
                tool_ids=req.tool_ids,
                on_step=on_step,
                on_usage=on_usage,
                on_live=on_live,
                budget=budget,
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
    if req.provider == 'browser':
        # nothing to talk to a tab with on this path — the bridge rides the
        # SSE stream, so a blocking call would just sit there until it timed out
        return {"query": req.query,
                "error": "browser models only run on /run/stream — the model "
                         "lives in your tab and answers over that stream"}
    # a harness agent runs on its own CLI, so it needs no provider key here
    if not mod.has_model(req.provider) and not mod.harness_for(resolved_agent):
        return {"error": "No API key configured for the selected provider — add or unlock a key in the Builder (model node)."}

    # chain execution
    if req.chain and len(req.chain) > 0:
        task = _task_create(req, chain=True, agent=resolved_agent)
        results = _run_chain(mod, req, on_step=lambda s: _task_step(task, s),
                             on_usage=lambda u: _task_usage(task, u),
                             budget=_run_budget(req, task))
        errs = [r.get("error") for r in results if r.get("error")]
        _task_finish(task, 'error' if errs else 'done',
                     errs[0] if errs else (results[-1].get("summary", "") if results else ""))
        charge = _charge_run(req, task)
        return {"query": req.query, "chain": True, "task_id": task["id"],
                "results": results, "charged": charge, "usage": _usage_of(task)}

    # single agent run
    task = _task_create(req, agent=resolved_agent)
    try:
        result = mod.forward('run',
            key=req.key,
            query=req.query,
            model=req.model,
            provider=req.provider,
            steps=req.steps,
            tools=req.tools,
            toolbox=req.toolbox or req.toolboxes,
            agent_type=resolved_agent,
            temperature=req.temperature,
            safety=req.safety,
            free=req.free,
            prompt=req.prompt,
            memory=req.memory,
            memory_ids=req.memory_ids,
            tool_ids=req.tool_ids,
            images=req.images,
            session=req.session,
            harness_args=req.harness_args,
            on_step=lambda s: _task_step(task, s),
            on_usage=lambda u: _task_usage(task, u),
            budget=_run_budget(req, task),
        )
        _task_finish(task, _status_of(result), _summary_of(result))
        charge = _charge_run(req, task)
        return {"query": req.query, "agent_type": resolved_agent, "task_id": task["id"],
                "result": result, "charged": charge, "usage": _usage_of(task)}
    except PermissionError as e:
        _task_finish(task, 'error', str(e))
        return {"query": req.query, "error": str(e), "code": 403}
    except Exception as e:
        _task_finish(task, 'error', str(e))
        return {"query": req.query, "error": str(e)}


@app.post("/browser/completion")
def browser_completion(req: BrowserCompletionRequest):
    """A tab handing back the text it generated for a waiting run.

    Open by design: the id is a one-shot secret handed out on the run's own
    stream, and delivering to it can only ever unblock the run that asked.
    """
    return BROWSER.deliver(req.id, text=req.text, error=req.error)


@app.get("/browser/models")
def browser_models(runtime: str = "browser"):
    """LFM repos a runtime can load, live from the liquidai catalog."""
    mod = get_mod()
    provider = next((p for p, rt in mod.LOCAL_RUNTIMES.items() if rt == runtime), None)
    if not provider:
        return {"runtime": runtime, "models": [], "error": f"unknown runtime {runtime!r}"}
    return {"runtime": runtime, "provider": provider,
            "models": mod.provider_models(provider),
            "default": mod.DEFAULT_MODELS.get(provider),
            "sessions": len(BROWSER.sessions())}


@app.post("/run/stream")
def run_agent_stream(req: RunRequest):
    """Run the agent loop, streaming each executed step live as SSE events.

    Events (one JSON object per `data:` line):
        {"type": "token",      "text": "..."}                 — the model's output,
            live as it streams off the provider (coalesced into small pieces).
            Raw loop output: prose plus the <STEP>{...}</STEP> scaffolding —
            renderers show the prose and fold the scaffolding into an indicator
        {"type": "model_start","step": i, "model": "..."}     — a model call just
            went out; nothing will stream until it starts answering
        {"type": "tool_start", "tool", "params", "i", "n"}    — a tool call is
            STARTING; its "step" event lands when it returns
        {"type": "step",       "step": {...}}                 — a tool step just executed
        {"type": "usage",      "usage": {...}}                — what the model call
            behind that step cost: {call, step, model, cost, total, tokens}
        {"type": "chain_step", "index": i, "agent": "name"}  — a chain stage is starting
        {"type": "model_request", "id", "model", "messages", …}  — provider
            'browser': generate this in the tab and POST the text to
            /browser/completion; the run is blocked until you do
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

    # the model's own output, forwarded as it streams. Providers hand back a
    # chunk per token; a frame per token is hundreds of SSE events a step, so
    # tokens coalesce until ~48 chars or 120ms have built up. Everything else
    # (a tool starting, a step landing) flushes first, so order is preserved.
    tok_buf: List[str] = []
    tok_at = [0.0]

    def flush_tokens():
        if tok_buf:
            text = ''.join(tok_buf)
            tok_buf.clear()
            emit({"type": "token", "text": text})

    def on_live(ev):
        kind = ev.get('event')
        if kind == 'token':
            tok_buf.append(ev.get('text') or '')
            now = time.monotonic()
            if sum(len(t) for t in tok_buf) >= 48 or now - tok_at[0] >= 0.12:
                tok_at[0] = now
                flush_tokens()
        elif kind:
            flush_tokens()
            emit({"type": kind, **{k: v for k, v in ev.items() if k != 'event'}})

    def on_step(s):
        flush_tokens()
        _task_step(task, s)
        emit({"type": "step", "step": s})

    def on_usage(u):
        # what the call that produced that step cost, on the same stream —
        # the price of a run should be watchable while it is being spent
        flush_tokens()
        _task_usage(task, u)
        emit({"type": "usage", "usage": u})

    # a browser run generates in the tab that started it: the session is bound
    # to this worker thread, so the model client parks its requests on this
    # stream and blocks until the tab POSTs the text back
    session = req.browser_session if req.provider == 'browser' else None
    if session:
        BROWSER.open(session, emit)

    def worker():
        BROWSER.bind(session)
        try:
            if not mod.has_model(req.provider) and not mod.harness_for(resolved_agent):
                _task_finish(task, 'error', 'No API key configured')
                emit({"type": "error", "error": "No API key configured for the selected provider — add or unlock a key in the Builder (model node)."})
                return
            if req.chain and len(req.chain) > 0:
                results = _run_chain(
                    mod, req,
                    on_step=on_step,
                    on_usage=on_usage,
                    on_live=on_live,
                    on_chain_step=lambda i, a: emit({"type": "chain_step", "index": i, "agent": a}),
                    budget=_run_budget(req, task),
                )
                errs = [r.get("error") for r in results if r.get("error")]
                _task_finish(task, 'error' if errs else 'done',
                             errs[0] if errs else (results[-1].get("summary", "") if results else ""))
                charge = _charge_run(req, task)
                emit({"type": "done", "chain": True, "task_id": task["id"],
                      "results": results, "charged": charge,
                      "usage": _usage_of(task)})
            else:
                result = mod.forward('run',
                    key=req.key,
                    query=req.query,
                    model=req.model,
                    provider=req.provider,
                    steps=req.steps,
                    tools=req.tools,
                    toolbox=req.toolbox or req.toolboxes,
                    agent_type=resolved_agent,
                    temperature=req.temperature,
                    safety=req.safety,
                    free=req.free,
                    prompt=req.prompt,
                    memory=req.memory,
                    memory_ids=req.memory_ids,
                    tool_ids=req.tool_ids,
                    images=req.images,
                    session=req.session,
                    harness_args=req.harness_args,
                    on_step=on_step,
                    on_usage=on_usage,
                    on_live=on_live,
                    budget=_run_budget(req, task),
                )
                flush_tokens()
                _task_finish(task, _status_of(result), _summary_of(result))
                charge = _charge_run(req, task)
                emit({"type": "done", "task_id": task["id"], "result": result,
                      "charged": charge, "usage": _usage_of(task)})
        except PermissionError as e:
            _task_finish(task, 'error', str(e))
            emit({"type": "error", "error": str(e), "code": 403})
        except Exception as e:
            _task_finish(task, 'error', str(e))
            emit({"type": "error", "error": str(e)})
        finally:
            BROWSER.bind(None)
            events.put(None)  # sentinel: stream over

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        try:
            while True:
                try:
                    ev = events.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"  # keepalive comment so proxies don't cut the stream
                    continue
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        finally:
            # the tab is gone (finished, navigated away, hit stop) — release
            # anything the run is still waiting on it for, rather than leaving
            # a worker thread parked until the bridge times out
            if session:
                BROWSER.close(session)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── MCP (Model Context Protocol) ─────────────────────────────────────
#
# The same handlers, spoken as JSON-RPC 2.0 over one endpoint. src/mcp.py holds
# the tools and the dispatch; this is only the transport — sessions, the SSE
# option, and recovering the caller's token from the Authorization header so an
# MCP client is authenticated exactly the way an HTTP client is.

MCP_SESSIONS: set = set()
MAX_MCP_SESSIONS = 500


def _mcp_key(request: Request) -> Optional[str]:
    """The caller's token: Bearer first, then the fleet's header spellings."""
    auth = request.headers.get('authorization') or ''
    if auth.lower().startswith('bearer '):
        return auth[7:].strip() or None
    for h in ('x-mod-key', 'x-agent-key', 'x-auth-token'):
        v = request.headers.get(h)
        if v:
            return v.strip()
    return request.query_params.get('key') or None


def _mcp_headers(session: Optional[str] = None) -> dict:
    h = {'MCP-Protocol-Version': mcp.PROTOCOL_VERSION}
    if session:
        h['Mcp-Session-Id'] = session
    return h


def _mcp_sse(payloads: list) -> StreamingResponse:
    """One JSON-RPC reply per SSE event, for clients that ask for a stream."""
    def gen():
        for p in payloads:
            yield f"data: {json.dumps(p, default=str)}\n\n"
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


@app.get("/mcp/schema")
def mcp_schema(request: Request):
    """The tool list and connection details, as plain JSON — what the console
    renders and what a curl reads before wiring a client up.

    The endpoint is reported for the host this request actually arrived on, so
    a read through the gateway describes the gateway rather than a localhost
    port nobody outside the box can dial. It is rebuilt from the fleet's route
    convention rather than the request path, because the proxy strips
    /api/agent before we ever see it — echoing the stripped path back would
    hand out /mcp on the bare domain, which is a different module entirely.
    """
    fwd_host = request.headers.get('x-forwarded-host')
    scheme = request.headers.get('x-forwarded-proto') or request.url.scheme
    own_port = int(os.environ.get('PORT', 50117))
    # direct only when the request landed on our own port with nothing in
    # front — a hit on the activator's :9000 is a proxy too, and takes the
    # same /api/<mod> route the gateway does
    direct = not fwd_host and (request.url.port or own_port) == own_port
    base = (f'{request.url.scheme}://{request.url.netloc}' if direct
            else f'{scheme}://{fwd_host or request.url.netloc}/api/agent')
    return {**mcp.info(base), 'tools': mcp.tool_list(),
            'resources': mcp.RESOURCES, 'instructions': mcp.INSTRUCTIONS}


@app.post("/mcp")
async def mcp_post(request: Request):
    """MCP streamable HTTP — the same JSON-RPC surface as the stdio server."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32700, 'message': 'parse error'}})

    # A session id we never issued means the client is talking to a different
    # process than the one it initialised against — usually an API restart.
    sent = request.headers.get('mcp-session-id')
    if sent and sent not in MCP_SESSIONS:
        return JSONResponse(status_code=404, headers=_mcp_headers(), content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32001, 'message': 'unknown session; reinitialize'}})

    msgs = body if isinstance(body, list) else [body]
    if not all(isinstance(x, dict) for x in msgs):
        return JSONResponse(status_code=400, content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32600, 'message': 'invalid request'}})

    session = sent
    if any(x.get('method') == 'initialize' for x in msgs):
        session = uuid.uuid4().hex
        MCP_SESSIONS.add(session)
        while len(MCP_SESSIONS) > MAX_MCP_SESSIONS:
            MCP_SESSIONS.pop()

    key = _mcp_key(request)
    replies = [r for r in (mcp.handle(x, key) for x in msgs) if r is not None]

    # Notifications only: nothing to answer, and 202 with an empty body is what
    # the spec asks for — a JSON `null` here trips strict clients.
    if not replies:
        return Response(status_code=202, headers=_mcp_headers(session))

    accept = request.headers.get('accept', '')
    if 'text/event-stream' in accept and 'application/json' not in accept:
        return _mcp_sse(replies)

    payload = replies if isinstance(body, list) else replies[0]
    return JSONResponse(payload, headers=_mcp_headers(session))


@app.delete("/mcp")
def mcp_delete(request: Request):
    """End a session. Nothing is stored against it, so this is bookkeeping."""
    MCP_SESSIONS.discard(request.headers.get('mcp-session-id') or '')
    return Response(status_code=204, headers=_mcp_headers())


@app.get("/mcp")
def mcp_get():
    """No server-initiated stream: the spec's answer for that is a plain 405."""
    return JSONResponse(status_code=405, headers=_mcp_headers(), content={
        'error': 'POST JSON-RPC here (MCP streamable HTTP); GET /mcp/schema '
                 'lists the tools. This server opens no server-initiated SSE stream'})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 50117))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
