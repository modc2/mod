"""
BlocTime ask agent — the "what is this?" guide behind the console's
bottom-right orb.

It is an explainer, not an operator: the Claude CLI runs with NO tools
(`--tools ""`, no MCP servers), so a question can never sign, read files or
touch the chain. Everything it knows rides in the system prompt — the
whitepaper (read from disk per request, so edits land live), the deployed
addresses from config.json, and whatever live numbers the browser already
had on screen (sent as `context`). Answers stream back as SSE.

Auth for the model mirrors bt/agent.py: ANTHROPIC_API_KEY (never an inherited
sk-ant-oat session token) → ~/.mod/bloctime/anthropic.key → root's CLI login
→ the owner session build publishes in ~/.mod/build/private/claude_host.json.
"""

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple

MODULE_DIR = Path(__file__).parent.parent
WHITEPAPER_PATH = MODULE_DIR / "whitepaper.md"
CONFIG_PATH = MODULE_DIR / "config.json"

CLAUDE_BIN = os.environ.get("BLOCTIME_AGENT_BIN", "/usr/local/bin/claude")
MODEL = os.environ.get("BLOCTIME_AGENT_MODEL", "sonnet")
TIMEOUT_SEC = int(os.environ.get("BLOCTIME_AGENT_TIMEOUT", "120"))

KEY_FILE = Path(os.path.expanduser("~/.mod/bloctime/anthropic.key"))
OAUTH_FILE = Path(os.path.expanduser("~/.claude/.credentials.json"))
HOST_CRED_FILE = Path(os.path.expanduser("~/.mod/build/private/claude_host.json"))
EXPIRY_MARGIN = 120

# It spends model credits and the console is public, so: short questions,
# a short memory, a per-caller budget and a ceiling on concurrent runs.
MAX_QUESTION = 1500
MAX_HISTORY = 8
MAX_CONTEXT = 2500
RATE_PER_HOUR = int(os.environ.get("BLOCTIME_AGENT_RATE", "30"))
MAX_CONCURRENT = 3

STARTERS: List[str] = [
    "What is BlocTime, in plain English?",
    "If I lock $100 for a month, what do I get?",
    "How does the weekly pot pay out?",
    "Can I get my money out early?",
    "What can the owner of a deployment do?",
    "How do I run my own BlocTime?",
]

GUIDE = """You are the BlocTime guide: a friendly explainer that lives in the \
bottom-right corner of the BlocTime console. People open you when they don't \
understand what this thing is.

How to answer:
- Plain language first. Most people asking have never staked anything. Lead \
with a one or two sentence answer, then add detail only if it helps.
- Use concrete numbers. BLOC = dollars locked x seconds locked (times the \
deployment's multiplier, 1x by default). $1 for 1 day = 86,400 BLOC. Do the \
arithmetic for people when they give amounts.
- Be honest about risk: locks cannot be exited early; a deployment owner can \
set the price and curve; a treasury owner can withdraw the reserve; the \
official instance is on a TESTNET (Base Sepolia) and the tokens have no real \
value. Never give investment advice.
- Point at the console when useful. Its tabs are: Intro, Whitepaper, Stake \
(lock tokens, see the curve), Rewards (weekly pot, voting power, \
delegation), Market (other people's deployments, USE to switch onto one), \
Deploy (launch your own from your wallet on any EVM chain), Bridge, and \
Contracts (raw ABI playground). The menu button top-right holds the wallet, \
network picker and themes.
- You cannot see wallets, sign, or call contracts. If asked to do something, \
explain the steps the person takes in the console.
- If something isn't covered below, say you don't know rather than guessing.
- Keep it short: a few sentences or a small list. Markdown **bold**, `code` \
and "- " lists render; avoid headings and tables. No emoji.
"""


# ------------------------------------------------------------------ auth

def _oauth_expires_at() -> Optional[float]:
    try:
        o = json.loads(OAUTH_FILE.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return 0
    if not o.get("accessToken"):
        return 0
    ms = o.get("expiresAt")
    return ms / 1000 if isinstance(ms, (int, float)) and ms > 0 else None


def _host_session() -> Optional[str]:
    try:
        v = json.loads(HOST_CRED_FILE.read_text())
    except (OSError, ValueError):
        return None
    tok = v.get("token") if v.get("source") == "owner-session" else None
    if tok and (v.get("expires_at") or 0) > time.time() + EXPIRY_MARGIN:
        return tok
    return None


def ensure_auth() -> Tuple[bool, Optional[str], Dict[str, str]]:
    """(ready, method, extra_env)."""
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key and not env_key.startswith("sk-ant-oat"):
        return True, "api-key-env", {}
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        key = ""
    if key:
        return True, "api-key-file", {"ANTHROPIC_API_KEY": key}
    exp = _oauth_expires_at()
    if exp is None or (exp and exp > time.time() + EXPIRY_MARGIN):
        return True, "claude-cli", {}
    tok = _host_session()
    if tok:
        return True, "build-session", {"CLAUDE_CODE_OAUTH_TOKEN": tok}
    return False, None, {}


def _child_env(extra: Dict[str, str]) -> Dict[str, str]:
    """Strip any parent Claude Code session wiring (see bt/agent.py)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE_CODE_") or k in ("CLAUDECODE", "CLAUDE_PID"))}
    if env.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-oat"):
        env.pop("ANTHROPIC_API_KEY")
    env.update(extra)
    return env


def status() -> Dict:
    ready, method, _ = ensure_auth()
    return {
        "ready": ready and os.path.exists(CLAUDE_BIN),
        "auth": method,
        "model": MODEL,
        "starters": STARTERS,
        "hint": None if ready else "The agent has no Claude login on this host yet.",
    }


# ------------------------------------------------------------- limits

_hits: Dict[str, deque] = defaultdict(deque)
_running = 0


def admit(caller: str) -> Optional[str]:
    """None if the caller may ask now, else the reason they can't."""
    now = time.time()
    q = _hits[caller]
    while q and q[0] < now - 3600:
        q.popleft()
    if len(q) >= RATE_PER_HOUR:
        return f"That's {RATE_PER_HOUR} questions this hour — try again a bit later."
    if _running >= MAX_CONCURRENT:
        return "The guide is busy answering others — try again in a moment."
    q.append(now)
    return None


# ------------------------------------------------------------- prompt

def _deployments() -> str:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return ""
    net = cfg.get("network", "testnet")
    c = (cfg.get("contracts") or {}).get(net) or {}
    if not c:
        return ""
    return (f"Official instance: network {net} (chainId {c.get('chainId')}, "
            f"Base Sepolia testnet), BlocTime contract {c.get('bloctime')}, "
            f"native token {c.get('nativeToken')}.")


def system_prompt(context: Optional[Dict]) -> str:
    try:
        paper = WHITEPAPER_PATH.read_text()
    except OSError:
        paper = "(whitepaper.md missing)"
    parts = [GUIDE, "\n<whitepaper>\n" + paper + "\n</whitepaper>\n", _deployments()]
    if context:
        blob = json.dumps(context, default=str)[:MAX_CONTEXT]
        parts.append(
            "\nWhat the person's console shows right now (live, may be partial; "
            "token amounts are 18-decimal wei strings unless formatted):\n" + blob)
    return "\n".join(p for p in parts if p)


def build_prompt(question: str, history: List[Dict]) -> str:
    turns = []
    for m in (history or [])[-MAX_HISTORY:]:
        role = "Person" if m.get("role") == "user" else "Guide"
        text = str(m.get("text") or "")[:MAX_QUESTION * 2]
        if text:
            turns.append(f"{role}: {text}")
    q = question.strip()[:MAX_QUESTION]
    if not turns:
        return q
    return ("Conversation so far:\n" + "\n\n".join(turns)
            + "\n\nNow answer the person's new question:\n" + q)


# --------------------------------------------------------------- run

def _sse(ev: Dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


async def stream(question: str, history: List[Dict],
                 context: Optional[Dict]) -> AsyncGenerator[str, None]:
    """SSE events: {type:'delta',text} … then {type:'done',text} or {type:'error',error}."""
    global _running
    ready, method, extra = ensure_auth()
    if not ready:
        yield _sse({"type": "error", "error": "The guide has no Claude login on this host yet — ask the operator to sign in at /build/auth."})
        return
    if not os.path.exists(CLAUDE_BIN):
        yield _sse({"type": "error", "error": "The claude CLI is not installed on this host."})
        return

    cmd = [
        CLAUDE_BIN, "-p", build_prompt(question, history),
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--model", MODEL,
        "--max-turns", "1",
        "--tools", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "",
        "--system-prompt", system_prompt(context),
    ]
    _running += 1
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_child_env(extra), cwd="/tmp", limit=4 * 1024 * 1024)
        text = ""
        final = None
        deadline = time.time() + TIMEOUT_SEC
        while True:
            left = deadline - time.time()
            if left <= 0:
                yield _sse({"type": "error", "error": "The guide took too long — try a shorter question."})
                return
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=left)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = msg.get("type")
            if t == "stream_event":
                ev = msg.get("event") or {}
                d = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    text += d.get("text", "")
                    yield _sse({"type": "delta", "text": d.get("text", "")})
            elif t == "result":
                final = msg
        await proc.wait()
        if final and final.get("is_error"):
            err = str(final.get("result") or final.get("subtype") or "error")
            yield _sse({"type": "error", "error": _friendly(err)})
            return
        answer = (final or {}).get("result") or text
        if not answer:
            err = (await proc.stderr.read()).decode(errors="replace")[-400:]
            yield _sse({"type": "error", "error": _friendly(err) or "No answer came back."})
            return
        yield _sse({"type": "done", "text": answer, "auth": method})
    finally:
        _running -= 1
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


def _friendly(err: str) -> str:
    low = err.lower()
    if any(m in low for m in ("authenticate", "oauth", "invalid api key", "/login")):
        return "The guide's Claude login on this host has lapsed — ask the operator to sign in at /build/auth."
    if "rate" in low and "limit" in low:
        return "The model is rate-limited right now — try again in a minute."
    return err.strip()[:300]
