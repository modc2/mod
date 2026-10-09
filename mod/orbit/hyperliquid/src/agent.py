"""
hyperliquid.agent — "ask Hyperliquid": a Claude agent whose entire toolbox is
this module's own MCP server (`hyperliquid-api --stdio`).

The agent holds no privileges of its own. It speaks to the same JSON-RPC tool
surface any MCP client gets, the stdio transport forwards the caller's mod
protocol token to the REST API, and `auth.rs` decides what that token may do.
Signed out, the agent can only read what the public routes already serve.

Four modes:
    ask    — read-only. Only GET-backed tools are on the allowlist; anything
             that signs, spends or mutates stored state is explicitly denied,
             so a question can never place an order. The Read tool is allowed,
             scoped to this module's own directory, so the agent can also
             answer questions about the module's code and design.
    act    — the full tool surface. Requires a token, and the caller has to
             opt in per run (`act=True` / `HL_AGENT_ACT=1`).
    chat   — the general chatbot: any question, answered from the model's own
             knowledge, with the read toolbox still on hand for live market
             facts. Writes are ALWAYS denied in chat (act is ignored), and a
             `session` id from a prior turn resumes the conversation, so the
             thread keeps context across messages.
    strats — the strat copilot: finds, explains, backtests, creates and
             manages strats (copy-trader / vault positions in the invest
             book). Multi-turn like chat, but act is HONORED — with actions
             on and a signed-in wallet it can really invest, pause, resume,
             add, withdraw and close. Read-only without act, like ask.

Every mode also carries the NAV protocol: the console renders markdown links
whose target starts with "/" as in-app navigation, so the agent can walk the
user to the right page instead of describing where it is.

The allow/deny split is derived from the live `GET /mcp/schema` — the same
table `mcp.rs` publishes — so there is no second tool list to drift.

Auth for the model resolves in order: ANTHROPIC_API_KEY env →
~/.mod/hyperliquid/anthropic.key → ~/.mod/hyperliquid/claude_oauth_token
(long-lived setup token) → the claude mod's credential keeper
(~/.mod/build/private/claude_host.json, a self-refreshing token the build/claude
console publishes for CLI-spawning mods) → Claude CLI OAuth
(~/.claude/.credentials.json). If none exist the key file is created empty
(0600) and status()/ask() say so.

CLI (this is what the Rust `/ask` route drives):
    python3 agent.py --status
    echo "<question>" | python3 agent.py --stream [--act | --chat]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")

# Pin the system CLI by absolute path: apps launched via npx get ancestor
# node_modules/.bin shims prepended to PATH, and the fleet carries a stale one.
_SYSTEM_CLAUDE = "/usr/local/bin/claude"
CLAUDE_BIN = os.environ.get("HL_AGENT_BIN") or (
    _SYSTEM_CLAUDE if os.path.exists(_SYSTEM_CLAUDE) else "claude")
MODEL = os.environ.get("HL_AGENT_MODEL", "sonnet")
MAX_TURNS = int(os.environ.get("HL_AGENT_MAX_TURNS", "16"))
TIMEOUT_SEC = int(os.environ.get("HL_AGENT_TIMEOUT", "300"))

KEY_FILE = os.path.expanduser("~/.mod/hyperliquid/anthropic.key")
# Long-lived setup token (sk-ant-oat01…) — outlives the interactive login in
# ~/.claude/.credentials.json, which expires and strands pm2-spawned children.
OAUTH_TOKEN_FILE = os.path.expanduser("~/.mod/hyperliquid/claude_oauth_token")
OAUTH_FILE = os.path.expanduser("~/.claude/.credentials.json")
# The claude mod's credential keeper: a 5-min loop on this host republishes a
# valid Claude token here (source: root login or the owner's self-refreshing
# session), so CLI-spawning mods don't die on "OAuth session expired".
KEEPER_FILE = os.path.expanduser("~/.mod/build/private/claude_host.json")

MCP_SERVER = "hyperliquid"
TOOL_PREFIX = f"mcp__{MCP_SERVER}__"

# The agent reasons over Hyperliquid and over this module's own code — nothing
# else on this host. Shell, writes and the open web are denied outright; its
# reach is the MCP server plus read-only access to the module directory (no
# secrets live there — wallet keys are under ~/.mod), so "how does the board
# cache work?" is answerable with file:line receipts.
LOCAL_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Grep", "Glob"]
CODE_READ = f"Read(/{ROOT_DIR}/**)"  # permission rule: // prefix = absolute path

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

# Every mode gets this: the console turns markdown links whose href starts
# with "/" into real in-app navigation, so the agent can hand the user a door
# instead of describing the hallway.
NAV_PROMPT = (
    "\n\nNAVIGATION: when you mention a trader, vault, strat or page, add a "
    "markdown link the console will render as an in-app button. Routes: "
    "[the strats board](/strats), [a trader](/trader/0x…), [a vault](/vaults/0x…), "
    "[the invest book](/invest), [one position](/invest/<id>), [wallet & deposits](/wallet), "
    "[top traders](/), [the market screen](/market). Use the trader/vault's name or "
    "short address as the link text. Only link routes from this list, always "
    "starting with /."
)

# The strat copilot: a strat here is money following an account — copy a
# trader's book, or an HL vault — managed as a Position in the invest book.
# This prompt is the product knowledge; auth.rs still decides what the
# caller's token may actually do.
STRATS_PROMPT = (
    "You are the strat copilot for the Hyperliquid console. A STRAT is money "
    "that follows an account: copy a trader (mirror their whole book, scaled "
    "to the user's dollars) or back an HL vault. Your job: help the user "
    "FIND strats, UNDERSTAND them, TEST them, CREATE them, and MANAGE the "
    "ones they run. Every number you state must come from a tool call.\n"
    "FIND: hl_strats_board ranks both kinds — rec_score multiplies the "
    "trailing 1d, 7d and 30d returns as ratios, so a strat must be green on "
    "every horizon to score; hl_top_traders and hl_list_vaults go deeper; "
    "hl_analyze_trader and hl_vault_details open one up.\n"
    "TEST: before recommending ANY strat, back it: hl_backtest_trader "
    "(address, capital, days) answers 'what would $N have done' and returns "
    "named data checks — surface failed checks honestly; hl_backtest_strats "
    "backs the whole board at once.\n"
    "CREATE: always hl_invest_preview first (trader, amount) — it says what "
    "$N can actually copy and what is too small — then hl_invest with "
    "{investor, kind: 'trader'|'vault', target, amount_usd, mode}. Suggest "
    "mode 'paper' for a first try: it simulates with no real orders. Live "
    "needs USDC on Hyperliquid and an approved agent key — check "
    "hl_agent_status, and send the user to [wallet](/wallet) if not.\n"
    "MANAGE: hl_invest_portfolio (investor = the signed-in address) is the "
    "user's book — equity, pnl, status per position; hl_invest_position "
    "opens one; hl_invest_pause / hl_invest_resume / hl_invest_add / "
    "hl_invest_withdraw / hl_invest_close change it. State what changed "
    "after every write, exactly as the tool reported it.\n"
    "If write tools are not available, say the ACTIONS toggle (and a "
    "signed-in wallet) is what enables create/manage, and keep helping with "
    "reads. Never invent an address, never size a position the user didn't "
    "state, confirm amount and target in your words before any write. Keep "
    "answers short, USD compact ($1.2M), addresses as 0x1234…abcd."
)

# Chat mode is the opposite contract from the desk analyst: general questions
# are welcome and answered from the model's own knowledge — the tools are an
# upgrade for live facts, not a requirement for speaking.
CHAT_PROMPT = (
    "You are a helpful chatbot living inside the Hyperliquid trading console. "
    "Answer any question the user asks — trading or not — clearly and "
    "concisely, from your own knowledge. When the question touches live "
    "Hyperliquid data (prices, traders, vaults, an account), prefer a tool "
    "call over memory: hl_mids / hl_candles / hl_orderbook for markets, "
    "hl_top_traders / hl_analyze_trader for traders, hl_list_vaults for "
    "vaults. All tools are read-only in this mode — if the user asks you to "
    "trade or move funds, say the Ask page's action mode is where that lives. "
    "Keep answers short, format USD compactly ($1.2M), and show addresses as "
    "0x1234…abcd."
)


def source_map(cap: int = 220) -> str:
    """Compact file listing for the system prompt, so the model can Read the
    right file without needing a Glob or Grep tool."""
    skip = {"target", "node_modules", ".next", "__pycache__", ".git", "out",
            ".pytest_cache"}
    out: List[str] = []
    for base, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        rel = os.path.relpath(base, ROOT_DIR)
        for f in sorted(files):
            if f.endswith((".pyc", ".lock", ".tsbuildinfo", ".log")):
                continue
            out.append(f if rel == "." else os.path.join(rel, f))
            if len(out) >= cap:
                out.append("…")
                return "\n".join(out)
    return "\n".join(out)


def code_prompt() -> str:
    return (
        "\n\nCODE QUESTIONS: you may also explain this module itself — its "
        "features, design and behavior. The Read tool is enabled, read-only, "
        f"for the module directory ({ROOT_DIR}); cite answers as path:line. "
        "Map: src/api/src/*.rs is the Rust REST API (traders.rs = leaderboard "
        "board + cache, sync.rs = background refresher, live_engine.rs = "
        "copy-trade engine, mcp.rs = this tool server), src/app/app is the "
        "Next.js console, src/mod.py the orchestrator, src/agent.py this "
        "agent, src/strats the strat classes, docs/ the guides. Files:\n"
        + source_map()
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


def keeper_token() -> str:
    """A live token from the claude mod's credential keeper, or ''.

    The keeper file is republished every ~5 min; trust it only while it says
    ready and its expiry (with a minute of slack) is still ahead.
    """
    try:
        with open(KEEPER_FILE) as f:
            k = json.load(f)
    except (OSError, ValueError):
        return ""
    if not (isinstance(k, dict) and k.get("ready") and k.get("token")):
        return ""
    exp = k.get("expires_at")
    if isinstance(exp, (int, float)) and exp < time.time() + 60:
        return ""
    return str(k["token"])


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
    try:
        tok = open(OAUTH_TOKEN_FILE).read().strip()
    except OSError:
        tok = ""
    if tok:
        return True, "oauth-token-file", None, {"CLAUDE_CODE_OAUTH_TOKEN": tok}
    tok = keeper_token()
    if tok:
        return True, "claude-mod-keeper", None, {"CLAUDE_CODE_OAUTH_TOKEN": tok}
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
              api_url: str, token: str, mode: str = "ask",
              session: str = "") -> List[str]:
    if mode == "chat":
        prompt = CHAT_PROMPT + NAV_PROMPT
    elif mode == "strats":
        prompt = STRATS_PROMPT + (ACT_PROMPT if act else "") + NAV_PROMPT
    else:
        prompt = SYSTEM_PROMPT + code_prompt() + (ACT_PROMPT if act else "") + NAV_PROMPT
    cmd = [
        CLAUDE_BIN, "-p", question,
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--strict-mcp-config", "--mcp-config", json.dumps(mcp_config(api_url, token)),
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(denied),
        "--append-system-prompt", prompt,
    ]
    if session:
        cmd += ["--resume", session]
    return cmd


def _events(msg: Dict) -> Generator[Dict, None, None]:
    """Translate one claude stream-json message into console events."""
    t = msg.get("type")
    if t == "system" and msg.get("subtype") == "init":
        yield {"type": "start", "model": msg.get("model"),
               "session_id": msg.get("session_id"),
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
               "session_id": msg.get("session_id"),
               "turns": msg.get("num_turns"), "ms": msg.get("duration_ms"),
               "cost_usd": msg.get("total_cost_usd")}


def _fail_text(ev: Dict) -> str:
    return str(ev.get("text") or ev.get("answer") or "").strip()


def _auth_failed(ev: Dict) -> bool:
    """True when a text/done event is really the CLI reporting dead auth.

    The CLI surfaces a revoked/expired credential as the run's *answer*
    ("Failed to authenticate. API Error: 401 …"), cost $0 — anchored so a
    genuine model reply can't trip it.
    """
    if ev.get("type") not in ("text", "done"):
        return False
    return _fail_text(ev).startswith("Failed to authenticate")


def _spawn_run(cmd: List[str], env: Dict[str, str],
               hold_auth_failures: bool = False) -> Generator[Dict, None, List[Dict]]:
    """One CLI run: spawn, watchdog, translate, stream.

    Returns (via StopIteration value) the auth-failure events it held back
    instead of yielding, so the caller can decide to retry or to error.
    """
    held: List[Dict] = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        yield {"type": "error", "error": f"{CLAUDE_BIN} CLI not found on this host"}
        return held

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
                if hold_auth_failures and _auth_failed(ev):
                    held.append(ev)
                    continue
                finished = finished or ev["type"] == "done"
                yield ev
        proc.wait(timeout=10)
        if not finished and not held:
            err = (proc.stderr.read() or "")[-400:].strip()
            yield {"type": "error",
                   "error": err or f"agent exited early (code {proc.returncode})"}
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
    return held


def ask(question: str, api_url: str = "", token: str = "",
        act: bool = False, mode: str = "ask",
        session: str = "") -> Generator[Dict, None, None]:
    """Stream one agent run as console events."""
    api_url = api_url or os.environ.get("HL_API_URL", "http://127.0.0.1:8919")
    token = token or os.environ.get("HYPERLIQUID_TOKEN", "")
    mode = mode if mode in ("ask", "chat", "strats") else "ask"
    # Chat can never write — a conversation must not be one typo from an
    # order. The strat copilot DOES honor act: creating and managing strats
    # is its whole second half, and the same token + per-run opt-in gate it.
    if mode == "chat":
        act = False
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "error": "ask what?"}
        return
    if act and not token:
        yield {"type": "error", "error": "action mode needs a signed-in wallet — sign in first"}
        return

    ready, method, hint, extra = ensure_auth()
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

    allowed = (reads + writes if act else reads) + [CODE_READ]
    denied = LOCAL_TOOLS + ([] if act else writes)
    yield {"type": "ready", "tools": len(allowed), "act": act, "mode": mode,
           "signed_in": bool(token)}

    # Keep the child from thinking it is nested inside a Claude Code session —
    # CLAUDECODE / CLAUDE_CODE_* (child-session markers, a dead parent's
    # socket) make the spawned CLI skip its own OAuth refresh; `extra` is
    # re-applied after the sweep so the auth tier we chose survives it.
    env = {k: v for k, v in os.environ.items()
           if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
    env.update(extra)
    if not env_api_key():
        env.pop("ANTHROPIC_API_KEY", None)
    cmd = build_cmd(question, allowed, denied, act, api_url, token,
                    mode=mode, session=session)

    # A credential can look fine on disk and still be revoked — the CLI only
    # finds out mid-run, and reports it as the *answer*. Hold those events
    # back and retry once on the claude mod keeper's self-refreshing token;
    # if that is also dead (or there is no keeper), surface a real error
    # instead of letting "Failed to authenticate" pose as a model reply.
    retry_tok = "" if method == "claude-mod-keeper" else keeper_token()
    held = yield from _spawn_run(cmd, env, hold_auth_failures=True)
    if not held:
        return
    if retry_tok:
        yield {"type": "retry",
               "reason": "model auth stale — retrying via the claude mod's credential keeper"}
        env["CLAUDE_CODE_OAUTH_TOKEN"] = retry_tok
        env.pop("ANTHROPIC_API_KEY", None)
        held = yield from _spawn_run(cmd, env, hold_auth_failures=True)
    if held:
        yield {"type": "error", "error": (
            f"{_fail_text(held[0])} — model auth is dead on this host: paste a "
            f"fresh `claude setup-token` into {OAUTH_TOKEN_FILE}, or sign the "
            "claude console back in so its credential keeper recovers")}


def answer(question: str, api_url: str = "", token: str = "",
           act: bool = False, mode: str = "ask",
           session: str = "") -> Dict[str, Any]:
    """Run to completion and collapse the stream into one result."""
    out: Dict[str, Any] = {"question": question, "answer": "", "tools": [],
                           "act": act, "ok": False}
    for ev in ask(question, api_url, token, act, mode=mode, session=session):
        if ev["type"] == "tool":
            out["tools"].append({"name": ev["name"], "args": ev["args"]})
        elif ev["type"] == "done":
            out.update(ok=True, answer=ev["answer"], turns=ev.get("turns"),
                       ms=ev.get("ms"), cost_usd=ev.get("cost_usd"),
                       session_id=ev.get("session_id"))
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
    env_mode = os.environ.get("HL_AGENT_MODE", "")
    if "--chat" in args or env_mode == "chat":
        mode = "chat"
    elif "--strats" in args or env_mode == "strats":
        mode = "strats"
    else:
        mode = "ask"
    session = os.environ.get("HL_AGENT_SESSION", "")
    if "--stream" in args:
        for ev in ask(question, api_url, act=act, mode=mode, session=session):
            print(json.dumps(ev), flush=True)
        return 0
    print(json.dumps(answer(question, api_url, act=act, mode=mode,
                            session=session)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
