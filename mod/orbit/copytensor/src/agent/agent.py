"""
copytensor.agent.agent — the strat agent: a Claude that reads copytensor's
own boards (via src.agent.mcp_server) and hands back a basket of traders to
mirror.

POST /agent/ask streams the run as SSE events:
    {type: start|text|tool|tool_done|strat|done|error, ...}

`strat` is the payload of a propose_strat call — the console renders it as a
card you save into the strat library. The agent is read-only: every write
tool is denied, so a conversation can never stake, start a copy or sign
anything. Going live stays a human click.

Auth resolves in order: ANTHROPIC_API_KEY env → ~/.mod/copytensor/anthropic.key
→ Claude CLI OAuth (~/.claude/.credentials.json). If none exist the key file
is created empty (0600) and status()/ask() say where to paste a key.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Dict, Generator, List, Optional, Tuple

from . import tools

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAUDE_BIN = os.environ.get("COPYTENSOR_AGENT_BIN", "claude")
MODEL = os.environ.get("COPYTENSOR_AGENT_MODEL", "sonnet")
MAX_TURNS = int(os.environ.get("COPYTENSOR_AGENT_MAX_TURNS", "16"))
TIMEOUT_SEC = int(os.environ.get("COPYTENSOR_AGENT_TIMEOUT", "300"))

KEY_FILE = os.path.expanduser("~/.mod/copytensor/anthropic.key")
OAUTH_FILE = os.path.expanduser("~/.claude/.credentials.json")

ALLOWED_TOOLS: List[str] = [f"mcp__copytensor__{t.name}" for t in tools.TOOLS]

MCP_CONFIG = {"mcpServers": {"copytensor": {
    "command": sys.executable, "args": ["-m", "src.agent.mcp_server"],
    "cwd": ROOT, "env": {"PYTHONPATH": ROOT,
                         "COPYTENSOR_API_URL": tools.API_URL}}}}

SYSTEM_PROMPT = (
    "You are copytensor's strat agent. A *strat* is a weighted basket of "
    "Bittensor coldkeys whose dTAO subnet allocations we mirror — an index "
    "of traders rather than a bet on one. Your job is to talk the user "
    "through what they want and then deliver that basket.\n"
    "\n"
    "Rules:\n"
    "- Answer only from tool results, never from memory. Numbers you did not "
    "read this turn do not exist.\n"
    "- ct_traders is your pool. Rank on change_7d / pnl_7d, and sanity-check "
    "a candidate with ct_trader before you weight it: a book that is one "
    "subnet deep, or whose gain is really a deposit (ct_leaderboard splits "
    "market move from stake flow), is not a trader worth mirroring.\n"
    "- Say what each pick is FOR. Every trader in a basket gets a one-line "
    "`why`. If you cannot justify it, drop it.\n"
    "- Prefer 3-8 traders unless asked otherwise, and spread the weight; a "
    "basket where one name carries 80% is a single copy wearing a hat.\n"
    "- Size it in the user's own terms. When they name TAO amounts (\"40 on "
    "this one, 10 on that\"), give each trader an `alloc_tao` — that is the "
    "money the live engine puts behind them. When they give you a pot and no "
    "per-trader figures, use relative `weight`s against capital_tao. Never "
    "hand back a basket whose amounts don't add up to what they said.\n"
    "- Ask at most one clarifying question, and only when the answer would "
    "change the basket (capital, risk, a subnet thesis). Otherwise pick "
    "sensible defaults, build it, and say what you assumed.\n"
    "- Finish by calling propose_strat. That is the deliverable; prose "
    "without a proposal is a wasted turn.\n"
    "\n"
    "Style: short, concrete, no preamble. Lead with the numbers. Name "
    "subnets as \"Name (#netuid)\" and traders by label or the first 8 "
    "characters of the address. The console prints your words as plain text "
    "on a CRT — no markdown tables, no ** bold **, no headings; they render "
    "as literal punctuation. Once you have proposed, stop at a line or two: "
    "the card already lists the basket, so restating it is noise. You are "
    "read-only — you cannot stake, start a copy or move TAO. Say so plainly "
    "if asked, and point at the SAVE + ACTIVATE buttons on the card."
)


# ── auth ─────────────────────────────────────────────────────────

def ensure_auth() -> Tuple[bool, Optional[str], Optional[str], Dict[str, str]]:
    """(ready, method, hint, extra_env) — creates KEY_FILE if nothing exists."""
    if os.environ.get("ANTHROPIC_API_KEY"):
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
        f"No Anthropic auth configured — paste an API key into {KEY_FILE} "
        f"(created, 0600) or run `claude login` on this host."), {}


def status() -> Dict:
    ready, method, hint, _ = ensure_auth()
    return {"ready": ready, "method": method, "hint": hint, "model": MODEL,
            "max_turns": MAX_TURNS, "timeout_sec": TIMEOUT_SEC,
            "tools": [t.name for t in tools.TOOLS], "api": tools.API_URL}


# ── run ──────────────────────────────────────────────────────────

def build_cmd(question: str, session_id: Optional[str] = None) -> List[str]:
    cmd = [
        CLAUDE_BIN, "-p", question,
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--strict-mcp-config", "--mcp-config", json.dumps(MCP_CONFIG),
        "--allowedTools", ",".join(ALLOWED_TOOLS),
        "--append-system-prompt", SYSTEM_PROMPT,
    ]
    # Talking to the agent is the point: every follow-up resumes the same
    # session, so "drop the bottom two" knows which two.
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


class _Run:
    """Translates one claude stream-json run into console events."""

    def __init__(self):
        # tool_use id -> tool name, so a result can be matched to its call.
        self.calls: Dict[str, str] = {}

    def events(self, msg: Dict) -> Generator[Dict, None, None]:
        t = msg.get("type")
        if t == "system" and msg.get("subtype") == "init":
            yield {"type": "start", "model": msg.get("model"),
                   "session_id": msg.get("session_id"),
                   "tools": sum(1 for x in msg.get("tools", [])
                                if str(x).startswith("mcp__copytensor__"))}
        elif t == "assistant":
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    yield {"type": "text", "text": c["text"]}
                elif c.get("type") == "tool_use":
                    name = str(c.get("name", "")).replace("mcp__copytensor__", "")
                    self.calls[c.get("id")] = name
                    yield {"type": "tool", "name": name, "args": c.get("input", {})}
        elif t == "user":
            content = msg.get("message", {}).get("content")
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                name = self.calls.get(c.get("tool_use_id"), "")
                err = bool(c.get("is_error"))
                yield {"type": "tool_done", "name": name, "error": err}
                if name == tools.STRAT_TOOL and not err:
                    strat = _strat_from(c)
                    if strat:
                        yield {"type": "strat", "strat": strat}
        elif t == "result":
            yield {"type": "done", "answer": msg.get("result") or "",
                   "session_id": msg.get("session_id"),
                   "turns": msg.get("num_turns"), "ms": msg.get("duration_ms"),
                   "cost_usd": msg.get("total_cost_usd")}


def _strat_from(tool_result: Dict) -> Optional[Dict]:
    """Pull the validated basket back out of a propose_strat tool result."""
    content = tool_result.get("content")
    parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
    for c in parts:
        text = c.get("text") if isinstance(c, dict) else None
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("traders"):
            return parsed
    return None


def ask(question: str, session_id: Optional[str] = None) -> Generator[Dict, None, None]:
    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {"type": "error", "error": hint}
        return
    env = {**os.environ, **extra}
    # Keep the child from thinking it is nested inside a Claude Code session.
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    try:
        proc = subprocess.Popen(build_cmd(question, session_id), cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        yield {"type": "error", "error": f"{CLAUDE_BIN} CLI not found on this host"}
        return
    watchdog = threading.Timer(TIMEOUT_SEC, proc.kill)
    watchdog.start()
    run = _Run()
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
            for ev in run.events(msg):
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


def ask_sync(question: str, session_id: Optional[str] = None) -> Dict:
    """Run to completion and return {answer, strat, tools, session_id}."""
    out: Dict = {"answer": "", "strat": None, "tools": [], "session_id": session_id}
    for ev in ask(question, session_id):
        if ev["type"] == "tool":
            out["tools"].append(ev["name"])
        elif ev["type"] == "strat":
            out["strat"] = ev["strat"]
        elif ev["type"] == "start" and ev.get("session_id"):
            out["session_id"] = ev["session_id"]
        elif ev["type"] == "done":
            out["answer"] = ev["answer"]
            out["session_id"] = ev.get("session_id") or out["session_id"]
        elif ev["type"] == "error":
            out["error"] = ev["error"]
    return out
