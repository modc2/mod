"""MCP over the same API — one handler set, two front doors.

An agent that speaks MCP shouldn't have to learn this module's REST shape, and
this module shouldn't grow a second implementation of everything so it can. So
every tool here calls the *same* function the HTTP route calls: `tools/call
liquidai_chat` and `POST /chat` run one code path, hold one gate, and land one
line in the ledger (tagged `via: mcp`, which is the only difference).

Transport is Streamable HTTP: JSON-RPC 2.0 in a POST body, a JSON object back,
notifications answered with 202 and no body. No SSE — nothing here streams, and
`liquidai_chat` drains the generator the way /v1/chat/completions does, for the
same reason: one streaming encoder in this module, on /chat, or the two drift.

Auth is the module's own session token as the bearer, so an MCP client is gated
exactly like a browser: reads are open, spending compute needs a session,
touching weights or the key vault needs the owner.
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional

try:
    from . import arena, auth, catalog, cloud, keys, ledger, providers, server_rt
except ImportError:  # pragma: no cover
    import arena, auth, catalog, cloud, keys, ledger, providers, server_rt

PROTOCOL = "2025-06-18"
SERVER = {"name": "liquidai", "title": "Liquid AI — every LFM, three runtimes",
          "version": "0.3.0"}


class ToolError(Exception):
    """A tool that failed for a reason the caller can act on."""


def _str(desc: str, default: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "string", "description": desc}
    if default is not None:
        out["default"] = default
    return out


def _num(desc: str, default: Any = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "number", "description": desc}
    if default is not None:
        out["default"] = default
    return out


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None):
    return {"type": "object", "properties": props, "required": required or []}


# ── the tools ────────────────────────────────────────────────────────
#
# `need` is the gate each one goes through — the same three levels the HTTP
# routes use, so a tool can never be a way around the door.

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "liquidai_health",
        "need": "open",
        "description": "Is this module up: uptime, catalog freshness, whether the "
                       "server runtime works, which model is resident.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_providers",
        "need": "open",
        "description": "Every provider behind this module — browser, this box, "
                       "Liquid's cloud, HuggingFace — with health, keys and the "
                       "share of calls each carried.",
        "inputSchema": _schema({"window_hours": _num("traffic window", 24)}),
    },
    {
        "name": "liquidai_models",
        "need": "open",
        "description": "The LFM catalog, one row per model rather than per repo. "
                       "Filter by runtime (browser|server|edge), kind "
                       "(text|vision|audio|embed), family or a substring.",
        "inputSchema": _schema({
            "runtime": _str("browser | server | edge"),
            "kind": _str("text | vision | audio | embed"),
            "family": _str("LFM2 | LFM2.5 | …"),
            "q": _str("substring of the model id"),
            "limit": _num("rows", 50),
        }),
    },
    {
        "name": "liquidai_model",
        "need": "open",
        "description": "One model in full: every format (torch/gguf/onnx/mlx), "
                       "every quant, and which of them are already on this disk.",
        "inputSchema": _schema({"id": _str("model id, e.g. LFM2.5-350M")}, ["id"]),
    },
    {
        "name": "liquidai_runtimes",
        "need": "open",
        "description": "What the browser, this box and the cloud can each run "
                       "right now.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_chat",
        "need": "session",
        "description": "Ask an LFM. runtime=server runs it on this box, "
                       "runtime=cloud on inference.liquid.ai with the box's key. "
                       "Browser models can't be run from here — they need a tab.",
        "inputSchema": _schema({
            "prompt": _str("the question"),
            "model": _str("HF repo (server) or cloud model id",
                          "LiquidAI/LFM2.5-350M"),
            "runtime": _str("server | cloud", "server"),
            "system": _str("system prompt"),
            "max_tokens": _num("cap on the answer", 256),
            "temperature": _num("0 is greedy", 0.3),
        }, ["prompt"]),
    },
    {
        "name": "liquidai_embed",
        "need": "session",
        "description": "Sentence vectors from an LFM encoder, plus the cosine "
                       "matrix between every pair.",
        "inputSchema": _schema({
            "texts": {"type": "array", "items": {"type": "string"},
                      "description": "sentences to embed"},
            "model": _str("encoder repo", "LiquidAI/LFM2.5-Encoder-230M"),
        }, ["texts"]),
    },
    {
        "name": "liquidai_local_models",
        "need": "open",
        "description": "LFM weights already on this box's disk, with sizes.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_pull",
        "need": "owner",
        "description": "Download a LiquidAI/* repo's weights onto this box, in "
                       "the background. Poll liquidai_pulls for progress.",
        "inputSchema": _schema({"repo": _str("LiquidAI/…")}, ["repo"]),
    },
    {
        "name": "liquidai_pulls",
        "need": "open",
        "description": "Download progress for weights being pulled.",
        "inputSchema": _schema({"repo": _str("narrow to one repo")}),
    },
    {
        "name": "liquidai_load",
        "need": "owner",
        "description": "Make a repo the resident server-side model, evicting the "
                       "last one. The box holds exactly one.",
        "inputSchema": _schema({"repo": _str("LiquidAI/…")}, ["repo"]),
    },
    {
        "name": "liquidai_unload",
        "need": "owner",
        "description": "Free the resident model and its memory.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_cloud_models",
        "need": "open",
        "description": "Models the box's Liquid Cloud key can reach.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_keys",
        "need": "open",
        "description": "Which BYOK keys this box holds — masked, always.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_calls",
        "need": "open",
        "description": "The call ledger: every call this module has answered, "
                       "newest first, with provider, model, caller, latency and "
                       "tokens. Prompt text is never recorded.",
        "inputSchema": _schema({
            "provider": _str("browser | server | cloud | huggingface | liquidai"),
            "model": _str("substring of the model id"),
            "via": _str("console | mcp | openai | cli | api"),
            "failed_only": {"type": "boolean", "description": "errors only",
                            "default": False},
            "since_minutes": _num("how far back", 1440),
            "limit": _num("rows", 50),
        }),
    },
    {
        "name": "liquidai_call_stats",
        "need": "open",
        "description": "Rollups over the ledger — per provider, per model, per "
                       "caller, per transport, and calls per hour.",
        "inputSchema": _schema({"window_hours": _num("window", 24)}),
    },
    {
        "name": "liquidai_arena_games",
        "need": "open",
        "description": "Games models compete at — rule-scored rounds, no judge "
                       "model.",
        "inputSchema": _schema({}),
    },
    {
        "name": "liquidai_arena_match",
        "need": "session",
        "description": "Run up to four models through a game and score every "
                       "round.",
        "inputSchema": _schema({
            "game": _str("game id"),
            "models": {"type": "array", "items": {"type": "string"},
                       "description": "up to four repos"},
            "runtime": _str("server | cloud", "server"),
        }, ["game", "models"]),
    },
    {
        "name": "liquidai_arena_leaderboard",
        "need": "open",
        "description": "Best run per model per game.",
        "inputSchema": _schema({"game": _str("narrow to one game")}),
    },
    {
        "name": "liquidai_whoami",
        "need": "open",
        "description": "Who this bearer is to the module, and who owns the box.",
        "inputSchema": _schema({}),
    },
]

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def catalogue() -> List[Dict[str, Any]]:
    """The tool list as MCP wants it — `need` is ours and stays out of it."""
    return [{k: v for k, v in tool.items() if k != "need"} for tool in TOOLS]


# ── handlers ─────────────────────────────────────────────────────────

def _gate(token: Optional[str], need: str):
    if need == "open":
        return auth.read(token) or {}
    ok, why, session = auth.gate(token, need)
    if not ok:
        raise ToolError(f"{why} — send your liquidai session token as the bearer")
    return session or {}


def _chat(args: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
    """Drains /chat's stream, exactly as the OpenAI face does."""
    runtime = args.get("runtime", "server")
    model = args.get("model") or "LiquidAI/LFM2.5-350M"
    messages: List[Dict[str, Any]] = []
    if args.get("system"):
        messages.append({"role": "system", "content": args["system"]})
    messages.append({"role": "user", "content": args["prompt"]})

    if runtime == "browser":
        raise ToolError("runtime='browser' runs in a tab — an MCP client has no "
                        "tab to run it in. Use server or cloud.")
    if runtime == "cloud":
        key = keys.get("cloud")
        if not key:
            raise ToolError("no cloud key on this box — POST /keys or set LIQUID_API_KEY")
        stream = cloud.generate(key, model, messages,
                                int(args.get("max_tokens", 256)),
                                float(args.get("temperature", 0.3)), 0.95)
    elif runtime == "server":
        avail = server_rt.available()
        if not avail["ok"]:
            raise ToolError(f"server runtime unavailable: {avail.get('error')}")
        try:
            server_rt.resolve(model)
        except server_rt.UnservableModel as e:
            raise ToolError(str(e))
        stream = server_rt.generate(model, messages,
                                    int(args.get("max_tokens", 256)),
                                    float(args.get("temperature", 0.3)), 0.95)
    else:
        raise ToolError(f"unknown runtime {runtime!r}")

    text, tail = "", {}
    for event in stream:
        if event.get("type") == "token":
            text += event["text"]
        elif event.get("type") == "done":
            tail = event
        elif event.get("type") == "error":
            raise ToolError(event.get("error", "generation failed"))
    return {"text": text, "model": model, "runtime": runtime,
            "stats": {k: v for k, v in tail.items() if k != "type"}}


def _models(args: Dict[str, Any]) -> Dict[str, Any]:
    rows = catalog.load()["models"]
    if args.get("runtime"):
        rows = [m for m in rows if args["runtime"] in m["runtimes"]]
    if args.get("kind"):
        rows = [m for m in rows if m["kind"] == args["kind"]]
    if args.get("family"):
        rows = [m for m in rows if m["family"].lower() == args["family"].lower()]
    if args.get("q"):
        rows = [m for m in rows if args["q"].lower() in m["id"].lower()]
    limit = int(args.get("limit", 50))
    return {"count": len(rows), "models": [
        {"id": m["id"], "kind": m["kind"], "params_b": m["params_b"],
         "runtimes": m["runtimes"], "role": m["role"], "downloads": m["downloads"],
         "server_repo": m.get("torch_repo"), "browser_repo": m.get("onnx_repo")}
        for m in rows[:limit]]}


def _calls(args: Dict[str, Any]) -> Dict[str, Any]:
    since = time.time() - float(args.get("since_minutes", 1440)) * 60
    out = ledger.query(
        provider=args.get("provider"), model=args.get("model"),
        via=args.get("via"), since=since,
        ok=(False if args.get("failed_only") else None),
        limit=int(args.get("limit", 50)))
    return out


HANDLERS: Dict[str, Callable[[Dict[str, Any], Optional[str]], Any]] = {
    "liquidai_health": lambda a, t: _health(),
    "liquidai_providers": lambda a, t: providers.table(float(a.get("window_hours", 24))),
    "liquidai_models": lambda a, t: _models(a),
    "liquidai_model": lambda a, t: (catalog.get(a["id"])
                                    or _missing(f"no LFM model named {a['id']}")),
    "liquidai_runtimes": lambda a, t: {
        "browser": {"runtime": "browser", "ok": True,
                    "engine": "transformers.js (WebGPU)"},
        "server": server_rt.available(),
        "cloud": cloud.available(keys.get("cloud")),
    },
    "liquidai_chat": lambda a, t: _chat(a, t),
    "liquidai_embed": lambda a, t: server_rt.embed(
        a.get("model") or "LiquidAI/LFM2.5-Encoder-230M", a["texts"], True),
    "liquidai_local_models": lambda a, t: {"models": server_rt.local_models(),
                                           "cache": server_rt.cache_root()},
    "liquidai_pull": lambda a, t: (server_rt.pull(a["repo"])
                                   if a["repo"].startswith("LiquidAI/")
                                   else _missing("this module only pulls LiquidAI/* repos")),
    "liquidai_pulls": lambda a, t: {"pulls": server_rt.pull_status(a.get("repo"))},
    "liquidai_load": lambda a, t: server_rt.load_model(a["repo"]),
    "liquidai_unload": lambda a, t: server_rt.unload(),
    "liquidai_cloud_models": lambda a, t: {"models": cloud.models(
        keys.get("cloud") or _missing("no cloud key on this box"))},
    "liquidai_keys": lambda a, t: keys.status(),
    "liquidai_calls": lambda a, t: _calls(a),
    "liquidai_call_stats": lambda a, t: ledger.stats(float(a.get("window_hours", 24))),
    "liquidai_arena_games": lambda a, t: {"games": arena.games()},
    "liquidai_arena_match": lambda a, t: _match(a),
    "liquidai_arena_leaderboard": lambda a, t: arena.leaderboard(a.get("game")),
    "liquidai_whoami": lambda a, t: {**auth.me(t), "owner_state": auth.owner_state()},
}


def _missing(why: str):
    raise ToolError(why)


def _health() -> Dict[str, Any]:
    try:
        cat = catalog.load()
        cat_state = {"ok": True, "models": cat["count"], "source": cat["source"]}
    except Exception as e:
        cat_state = {"ok": False, "error": str(e)}
    return {"ok": True, "version": SERVER["version"], "catalog": cat_state,
            "server_runtime": server_rt.available()["ok"],
            "resident": server_rt.loaded(), "auth": auth.owner_state()}


def _match(args: Dict[str, Any]) -> Dict[str, Any]:
    models = args.get("models") or []
    if not models:
        raise ToolError("no models entered")
    if len(models) > 4:
        raise ToolError("four entrants a match — the box is not a cluster")
    runtime = args.get("runtime", "server")
    if runtime == "cloud":
        key = keys.get("cloud")
        if not key:
            raise ToolError("no cloud key on this box")

        def runner(model, messages, max_tokens, temperature, top_p):
            return cloud.generate(key, model, messages, max_tokens, temperature, top_p)
    else:
        if not server_rt.available()["ok"]:
            raise ToolError("server runtime unavailable")
        runner = server_rt.generate
    results = [arena.play(args["game"], model, runner) for model in models]
    return {"game": args["game"], "runtime": runtime,
            "results": sorted(results, key=lambda r: (-r["score"], r["sec_per_round"]))}


# ── JSON-RPC ─────────────────────────────────────────────────────────

def call_tool(name: str, args: Dict[str, Any],
              token: Optional[str] = None) -> Dict[str, Any]:
    """Run one tool through its gate. Used by tools/call and by the CLI."""
    tool = TOOL_BY_NAME.get(name)
    if not tool:
        raise ToolError(f"unknown tool {name!r} — {len(TOOLS)} available")
    _gate(token, tool["need"])
    return HANDLERS[name](args or {}, token)


def _result(payload: Any) -> Dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)[:120000]
    return {"content": [{"type": "text", "text": text}]}


def _error_result(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle(payload: Dict[str, Any], token: Optional[str] = None,
           on_call: Optional[Callable[[str, Dict[str, Any]], None]] = None
           ) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message in, one response out — or None for a notification."""
    method = payload.get("method")
    rpc_id = payload.get("id")
    params = payload.get("params") or {}

    if method is None:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32600, "message": "no method"}}
    if method.startswith("notifications/"):
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER,
            "instructions": (
                "Liquid AI's whole model family behind one API. Start with "
                "liquidai_models to see the catalog (bare ids like LFM2.5-350M, "
                "folded across HF repos), liquidai_providers to see where a run "
                "can go and what each place costs, then liquidai_chat to run "
                "one. liquidai_calls is the ledger of everything this module "
                "has answered — provider, model, latency, tokens, never prompts."
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": catalogue()})
    if method == "resources/list":
        return ok({"resources": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"unknown method {method!r}"}}

    name = params.get("name") or ""
    args = params.get("arguments") or {}
    if on_call:
        on_call(name, args)
    try:
        return ok(_result(call_tool(name, args, token)))
    except ToolError as e:
        # An MCP error belongs in the result, not in the JSON-RPC envelope: the
        # envelope means "the protocol broke", and "no cloud key" didn't.
        return ok(_error_result(str(e)))
    except Exception as e:
        return ok(_error_result(f"{type(e).__name__}: {e}"))


def descriptor(base: str) -> Dict[str, Any]:
    """What a GET on /mcp says — enough to wire a client up by hand."""
    return {
        "server": SERVER,
        "protocol": PROTOCOL,
        "transport": "streamable-http (POST JSON-RPC)",
        "endpoint": f"{base}/mcp",
        "auth": "Authorization: Bearer <liquidai session token> — reads are open",
        "tools": [{"name": t["name"], "need": t["need"],
                   "description": t["description"]} for t in TOOLS],
        "client_config": {
            "mcpServers": {
                "liquidai": {"type": "http", "url": f"{base}/mcp",
                             "headers": {"Authorization": "Bearer <token>"}}
            }
        },
    }
