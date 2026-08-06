"""
hyperliquid.agent — "ask Hyperliquid": a Claude agent whose entire toolbox is
this module's own MCP server (`hyperliquid-api --stdio`).

The agent holds no privileges of its own. It speaks to the same JSON-RPC tool
surface any MCP client gets, the stdio transport forwards the caller's mod
protocol token to the REST API, and `auth.rs` decides what that token may do.
Signed out, the agent can only read what the public routes already serve.

Two modes:
    ask   — read-only. Only GET-backed tools are on the allowlist; anything
            that signs, spends or mutates stored state is explicitly denied,
            so a question can never place an order.
    act   — the full tool surface. Requires a token, and the caller has to opt
            in per run (`act=True` / `HL_AGENT_ACT=1`).

The allow/deny split is derived from the live `GET /mcp/schema` — the same
table `mcp.rs` publishes — so there is no second tool list to drift.

Auth for the model resolves in order: ANTHROPIC_API_KEY env →
~/.mod/hyperliquid/anthropic.key → Claude CLI OAuth (~/.claude/.credentials.json).
If none exist the key file is created empty (0600) and status()/ask() say so.

CLI (this is what the Rust `/ask` route drives):
    python3 agent.py --status
    echo "<question>" | python3 agent.py --stream [--act]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")

CLAUDE_BIN = os.environ.get("HL_AGENT_BIN", "claude")
MODEL = os.environ.get("HL_AGENT_MODEL", "sonnet")
MAX_TURNS = int(os.environ.get("HL_AGENT_MAX_TURNS", "16"))
TIMEOUT_SEC = int(os.environ.get("HL_AGENT_TIMEOUT", "300"))

KEY_FILE = os.path.expanduser("~/.mod/hyperliquid/anthropic.key")
OAUTH_FILE = os.path.expanduser("~/.claude/.credentials.json")

MCP_SERVER = "hyperliquid"
TOOL_PREFIX = f"mcp__{MCP_SERVER}__"

# The agent reasons over Hyperliquid, not over this host. Local file and shell
# tools are denied outright — its only reach is the MCP server.
LOCAL_TOOLS = ["Bash", "Read", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

SYSTEM_PROMPT = (
    "You are the Hyperliquid module's desk analyst. Every fact you state must "
    "come from a tool call in this conversation — never from memory, and never "
    "from a price you assume. Reach for hl_top_traders / hl_analyze_trader for "
    "trader questions, hl_mids / hl_candles / hl_orderbook for market data, "
    "hl_list_vaults / hl_vault_details for vaults, hl_user_state / hl_user_fills "
    "/ hl_user_pnl for an account. Lead with the numbers, keep it short, format "
    "USD compactly ($1.2M), and show addresses as 0x1234…abcd. If a tool returns "
    "401 the user is signed out — say so and name the wallet action they need. "
    "Orders are signed by a per-wallet agent key the master wallet must approve "
    "first: check hl_agent_status before trying to trade."
)

ACT_PROMPT = (
    " ACTION MODE: write tools are enabled and act on the signed-in wallet's "
    "real funds. Confirm size, coin and side against the user's words before "
    "calling one, never place an order the user did not ask for, and after any "
    "write report exactly what came back. Prefer one order over several."
)


# ─── tool policy — derived from the module's own MCP schema ──────────────

def _schema(api_url: str) -> Dict[str, Any]:
    r = requests.get(f"{api_url}/mcp/schema", timeout=10)
    r.raise_for_status()
    return r.json()


def tool_policy(api_url: str) -> Tuple[List[str], List[str]]:
    """(read tools, write tools) as claude-CLI tool names.

    A tool is a read when the mod fn it fronts is served by GET; everything
    else signs, spends or mutates stored state.
    """
    reads, writes = [], []
    for t in _schema(api_url).get("tools", []):
        (reads if t.get("method") == "GET" else writes).append(TOOL_PREFIX + t["name"])
    return sorted(reads), sorted(writes)


def _api_binary() -> str:
    for profile in ("release", "debug"):
        p = os.path.join(API_DIR, "target", profile, "hyperliquid-api")
        if os.path.exists(p):
            return p
    return ""


def mcp_config(api_url: str, token: str) -> Dict[str, Any]:
    """stdio MCP server config — the same one `mod.py::mcp_config` publishes,
    with the caller's token filled in."""
    env = {"HL_API_URL": api_url}
    if token:
        env["HYPERLIQUID_TOKEN"] = token
    return {"mcpServers": {MCP_SERVER: {
        "command": _api_binary(), "args": ["--stdio"], "env": env}}}


# ─── model auth ──────────────────────────────────────────────────────────

def env_api_key() -> str:
    """An inherited ANTHROPIC_API_KEY, if it is really an API key.

    A process started from inside a Claude Code session inherits that session's
    OAuth access token (`sk-ant-oat…`) under this name. The CLI rejects it as
    an API key, so treating it as auth would strand us on "Invalid API key"
    instead of falling through to a key file or `claude login`.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return "" if key.startswith("sk-ant-oat") else key


def ensure_auth() -> Tuple[bool, Optional[str], Optional[str], Dict[str, str]]:
    """(ready, method, hint, extra_env) — creates KEY_FILE if nothing exists."""
    if env_api_key():
        return True, "api-key-env", None, {}
    try:
        key = open(KEY_FILE).read().strip()
    except OSError:
        key = ""
    if key:
        return True, "api-key-file", None, {"ANTHROPIC_API_KEY": key}
    if os.path.exists(OAUTH_FILE):
        return True, "claude-cli", None, {}
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, "w"):
            pass
        os.chmod(KEY_FILE, 0o600)
    return False, None, (
        f"no anthropic auth — paste an API key into {KEY_FILE} (created, 0600) "
        f"or run `claude login` on this host"), {}


def status(api_url: str) -> Dict[str, Any]:
    ready, method, hint, _ = ensure_auth()
    binary = _api_binary()
    out: Dict[str, Any] = {
        "ready": ready and bool(binary), "auth": method, "hint": hint,
        "model": MODEL, "max_turns": MAX_TURNS, "binary": binary,
    }
    if not binary:
        out["hint"] = "hyperliquid-api binary not built — run `cargo build --release`"
    try:
        reads, writes = tool_policy(api_url)
        out["read_tools"], out["write_tools"] = len(reads), len(writes)
    except Exception as e:  # API down → the agent has no toolbox
        out["ready"] = False
        out["hint"] = out["hint"] or f"MCP schema unreachable at {api_url}: {e}"
    return out


# ─── run ─────────────────────────────────────────────────────────────────

def build_cmd(question: str, allowed: List[str], denied: List[str], act: bool,
              api_url: str, token: str) -> List[str]:
    return [
        CLAUDE_BIN, "-p", question,
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--strict-mcp-config", "--mcp-config", json.dumps(mcp_config(api_url, token)),
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(denied),
        "--append-system-prompt", SYSTEM_PROMPT + (ACT_PROMPT if act else ""),
    ]


def _events(msg: Dict) -> Generator[Dict, None, None]:
    """Translate one claude stream-json message into console events."""
    t = msg.get("type")
    if t == "system" and msg.get("subtype") == "init":
        yield {"type": "start", "model": msg.get("model"),
               "tools": sum(1 for x in msg.get("tools", [])
                            if str(x).startswith(TOOL_PREFIX))}
    elif t == "assistant":
        for c in msg.get("message", {}).get("content", []):
            if c.get("type") == "text" and c.get("text", "").strip():
                yield {"type": "text", "text": c["text"]}
            elif c.get("type") == "tool_use":
                yield {"type": "tool",
                       "name": str(c.get("name", "")).replace(TOOL_PREFIX, ""),
                       "args": c.get("input", {})}
    elif t == "user":
        content = msg.get("message", {}).get("content")
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                yield {"type": "tool_done", "error": bool(c.get("is_error"))}
    elif t == "result":
        yield {"type": "done", "answer": msg.get("result") or "",
               "turns": msg.get("num_turns"), "ms": msg.get("duration_ms"),
               "cost_usd": msg.get("total_cost_usd")}


def ask(question: str, api_url: str = "", token: str = "",
        act: bool = False) -> Generator[Dict, None, None]:
    """Stream one agent run as console events."""
    api_url = api_url or os.environ.get("HL_API_URL", "http://127.0.0.1:8919")
    token = token or os.environ.get("HYPERLIQUID_TOKEN", "")
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "error": "ask what?"}
        return
    if act and not token:
        yield {"type": "error", "error": "action mode needs a signed-in wallet — sign in first"}
        return

    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {"type": "error", "error": hint}
        return
    if not _api_binary():
        yield {"type": "error", "error": "hyperliquid-api binary not built — run `cargo build --release`"}
        return
    try:
        reads, writes = tool_policy(api_url)
    except Exception as e:
        yield {"type": "error", "error": f"MCP schema unreachable at {api_url}: {e}"}
        return

    allowed = reads + writes if act else reads
    denied = LOCAL_TOOLS + ([] if act else writes)
    yield {"type": "ready", "tools": len(allowed), "act": act,
           "signed_in": bool(token)}

    env = {**os.environ, **extra}
    # Keep the child from thinking it is nested inside a Claude Code session —
    # including that session's OAuth token masquerading as an API key.
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    if not env_api_key():
        env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.Popen(
            build_cmd(question, allowed, denied, act, api_url, token),
            cwd=ROOT_DIR, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        yield {"type": "error", "error": f"{CLAUDE_BIN} CLI not found on this host"}
        return

    watchdog = threading.Timer(TIMEOUT_SEC, proc.kill)
    watchdog.start()
    finished = False
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ev in _events(msg):
                finished = finished or ev["type"] == "done"
                yield ev
        proc.wait(timeout=10)
        if not finished:
            err = (proc.stderr.read() or "")[-400:].strip()
            yield {"type": "error",
                   "error": err or f"agent exited early (code {proc.returncode})"}
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()


def answer(question: str, api_url: str = "", token: str = "",
           act: bool = False) -> Dict[str, Any]:
    """Run to completion and collapse the stream into one result."""
    out: Dict[str, Any] = {"question": question, "answer": "", "tools": [],
                           "act": act, "ok": False}
    for ev in ask(question, api_url, token, act):
        if ev["type"] == "tool":
            out["tools"].append({"name": ev["name"], "args": ev["args"]})
        elif ev["type"] == "done":
            out.update(ok=True, answer=ev["answer"], turns=ev.get("turns"),
                       ms=ev.get("ms"), cost_usd=ev.get("cost_usd"))
        elif ev["type"] == "error":
            out["error"] = ev["error"]
    return out


# ─── CLI (driven by the Rust /ask route) ─────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    api_url = os.environ.get("HL_API_URL", "http://127.0.0.1:8919")
    if "--status" in args:
        print(json.dumps(status(api_url)))
        return 0
    # The question arrives on stdin so it never lands in a process listing.
    question = sys.stdin.read()
    act = "--act" in args or os.environ.get("HL_AGENT_ACT") == "1"
    if "--stream" in args:
        for ev in ask(question, api_url, act=act):
            print(json.dumps(ev), flush=True)
        return 0
    print(json.dumps(answer(question, api_url, act=act)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
