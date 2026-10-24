"""
copytensor.agent.agent — the desk agent: a Claude that reads copytensor's
own boards (via src.agent.mcp_server), hands back baskets of traders to
mirror, and — with your approval, every single time — works the book.

POST /agent/ask streams the run as SSE events:
    {type: start|text|tool|tool_done|strat|approval|approval_done|ping|done|error}

`strat` is the payload of a propose_strat call: a card you save into the
strat library. `approval` is a WRITE the agent wants to make, parked by
src/agent/approvals.py — it has not run and will not run until the console
POSTs a decision. Reads and a dry-run sync are ungated; everything that
touches the copy book, the watchlist or the chain is not.

Two things make the gate real rather than decorative:

* It lives in the MCP dispatcher, below this file — the agent cannot route
  around it, and neither can a resumed session or another MCP client.
* A decline comes back as the tool result, so "no, 40 τ is too much" is a
  turn of the conversation, not an exception. The agent adapts and re-asks.

While a request is parked the run makes no progress and prints nothing, so
the timeout watchdog stops counting and the stream emits `ping` frames to
hold the connection open for as long as the human takes.

Auth resolves in order: ANTHROPIC_API_KEY env → ~/.mod/copytensor/anthropic.key
→ Claude CLI OAuth (~/.claude/.credentials.json). If none exist the key file
is created empty (0600) and status()/ask() say where to paste a key.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Generator, List, Optional, Tuple

from . import approvals
from . import tools

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAUDE_BIN = os.environ.get("COPYTENSOR_AGENT_BIN", "claude")
MODEL = os.environ.get("COPYTENSOR_AGENT_MODEL", "sonnet")
MAX_TURNS = int(os.environ.get("COPYTENSOR_AGENT_MAX_TURNS", "16"))
TIMEOUT_SEC = int(os.environ.get("COPYTENSOR_AGENT_TIMEOUT", "300"))

KEY_FILE = os.path.expanduser("~/.mod/copytensor/anthropic.key")
OAUTH_FILE = os.path.expanduser("~/.claude/.credentials.json")

# The agent gets the WHOLE toolbox now — reads and ops alike. What keeps it
# safe is not a short list, it is the approval gate every write goes through.
ALLOWED_TOOLS: List[str] = [f"mcp__copytensor__{t.name}" for t in tools.ALL_TOOLS]


def mcp_config(run_id: str) -> Dict:
    """The stdio server this run talks to, pinned to its own approval run."""
    return {"mcpServers": {"copytensor": {
        "command": sys.executable, "args": ["-m", "src.agent.mcp_server"],
        "cwd": ROOT, "env": {"PYTHONPATH": ROOT,
                             "COPYTENSOR_API_URL": tools.API_URL,
                             "COPYTENSOR_MCP_SCOPE": "all",
                             # writes park under this id; the console shows
                             # the card against this very conversation
                             "COPYTENSOR_MCP_APPROVAL": run_id}}}}

SYSTEM_PROMPT = (
    "You are copytensor's desk agent. A *strat* is a weighted basket of "
    "Bittensor coldkeys whose dTAO subnet allocations we mirror — an index "
    "of traders rather than a bet on one. You research that book, propose "
    "it, and on request you work it: start copies, re-size them, pause "
    "them, sync the portfolio to the chain.\n"
    "\n"
    "THE RULE THAT OUTRANKS EVERY OTHER ONE: you never act unasked, and "
    "every write you make is approved by the human first. The approval is "
    "enforced below you — ct_create_copy, ct_resize_copy, ct_delete_copy, "
    "ct_pause_copy, ct_resume_copy, ct_watch, ct_unwatch and a live ct_sync "
    "all pause for a card in their console before they run. So:\n"
    "- Say what you are about to do, in TAO and in names, BEFORE you call "
    "the tool. The card repeats the arguments; your sentence is what makes "
    "them mean something.\n"
    "- One write per call. Never bundle three copies into an unexplained "
    "run of tool calls — propose the set in words, then do them one at a "
    "time so each can be refused on its own.\n"
    "- Preview first. ct_portfolio and ct_sync(dry_run=true) are free and "
    "show the exact trades a live pass would sign. Read one out before you "
    "ever ask for the live sync.\n"
    "- A decline is an answer, not an obstacle. Do not re-submit the same "
    "call, do not try a different tool for the same effect. Ask what to "
    "change.\n"
    "- If the human has not asked for an action this turn, do not invent "
    "one. Research and a proposal are a complete answer.\n"
    "\n"
    "Research rules:\n"
    "- Answer only from tool results, never from memory. Numbers you did "
    "not read this turn do not exist.\n"
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
    "- A basket you researched but were not told to run ends in "
    "propose_strat. That renders as a card they save and activate "
    "themselves; it stakes nothing.\n"
    "\n"
    "Money hygiene: copies need a loaded wallet (ct_wallet) and you cannot "
    "load one — that is a console action, say so. Check ct_copies before "
    "starting a copy so you do not duplicate a sleeve, and check the "
    "balance before you spend it.\n"
    "\n"
    "Style: short, concrete, no preamble. Lead with the numbers. Name "
    "subnets as \"Name (#netuid)\" and traders by label or the first 8 "
    "characters of the address. The console prints your words as plain text "
    "on a CRT — no markdown tables, no ** bold **, no headings; they render "
    "as literal punctuation. Once you have proposed, stop at a line or two: "
    "the card already lists the basket, so restating it is noise."
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


def oauth_note() -> Optional[str]:
    """A word of warning when the Claude CLI login looks stale.

    The CLI refreshes its own token, so a lapsed `expiresAt` is usually
    nothing — but when the refresh has failed, the only symptom is a run
    that answers "OAuth session expired" after you have already asked it
    something. Better to say so on the badge than in the transcript.
    """
    try:
        with open(OAUTH_FILE) as f:
            oauth = (json.load(f) or {}).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None
    now_ms = time.time() * 1000
    if (oauth.get("refreshTokenExpiresAt") or 0) < now_ms:
        return "the Claude CLI login on this host has expired — run `claude login`"
    if (oauth.get("expiresAt") or 0) < now_ms:
        return ("the Claude CLI token is past its expiry; it should refresh "
                "itself, but if a run answers 'OAuth session expired', run "
                "`claude login` on this host")
    return None


def status() -> Dict:
    ready, method, hint, _ = ensure_auth()
    return {"ready": ready, "method": method, "hint": hint, "model": MODEL,
            "max_turns": MAX_TURNS, "timeout_sec": TIMEOUT_SEC,
            "tools": [t.name for t in tools.ALL_TOOLS],
            "read_tools": [t.name for t in tools.TOOLS],
            # what it can do for you, and what it must ask about first
            "write_tools": sorted(tools.WRITE_TOOLS),
            "approval_required": True,
            "approval_ttl_sec": approvals.TTL_SEC,
            "auth_note": oauth_note() if method == "claude-cli" else None,
            "api": tools.API_URL}


# ── run ──────────────────────────────────────────────────────────

def build_cmd(question: str, session_id: Optional[str] = None,
              run_id: str = "") -> List[str]:
    cmd = [
        CLAUDE_BIN, "-p", question,
        "--output-format", "stream-json", "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--strict-mcp-config", "--mcp-config", json.dumps(mcp_config(run_id)),
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


_IDLE = object()          # the queue said nothing this tick
_PING_EVERY_SEC = 10.0    # keepalive while a human reads a card


def ask(question: str, session_id: Optional[str] = None,
        run_id: Optional[str] = None) -> Generator[Dict, None, None]:
    """One turn of the conversation, streamed.

    stdout is pumped by a thread into a queue so this loop keeps ticking
    while the agent is blocked on an approval: that is when the cards have
    to reach the console, and a plain `for line in proc.stdout` would be
    asleep exactly then.
    """
    ready, _, hint, extra = ensure_auth()
    if not ready:
        yield {"type": "error", "error": hint}
        return
    run_id = run_id or uuid.uuid4().hex[:12]
    env = {**os.environ, **extra}
    # Keep the child from thinking it is nested inside a Claude Code session.
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    try:
        proc = subprocess.Popen(build_cmd(question, session_id, run_id),
                                cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        yield {"type": "error", "error": f"{CLAUDE_BIN} CLI not found on this host"}
        return

    lines: "queue.Queue" = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    run = _Run()
    finished = timed_out = eof = False
    deadline = time.time() + TIMEOUT_SEC
    last_ping = last_decision = 0.0
    try:
        while not eof:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                line = _IDLE
            if line is None:
                eof = True
            elif line is not _IDLE:
                deadline = time.time() + TIMEOUT_SEC
                line = line.strip()
                if line:
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        msg = None
                    for ev in (run.events(msg) if msg else ()):
                        finished = finished or ev["type"] == "done"
                        yield ev

            # Parked writes and their verdicts ride the same stream as the
            # transcript, so the console needs no second channel.
            for a in approvals.take_new(run_id):
                yield {"type": "approval", "approval": a.public()}
            for a in approvals.resolved_since(run_id, last_decision):
                last_decision = a.decided_at or last_decision
                yield {"type": "approval_done", "id": a.id, "tool": a.tool,
                       "state": a.state, "note": a.note}

            if approvals.waiting(run_id):
                # A human is not a hang: hold the watchdog and the socket.
                deadline = time.time() + TIMEOUT_SEC
                if time.time() - last_ping > _PING_EVERY_SEC:
                    last_ping = time.time()
                    yield {"type": "ping", "waiting_on": "approval"}
            elif not eof and time.time() > deadline:
                timed_out = True
                proc.kill()
                break

        if timed_out:
            yield {"type": "error",
                   "error": f"agent timed out after {TIMEOUT_SEC}s of silence"}
        elif not finished:
            proc.wait(timeout=10)
            err = (proc.stderr.read() or "")[-400:].strip()
            yield {"type": "error",
                   "error": err or f"agent exited early (code {proc.returncode})"}
    finally:
        # Whatever happens — client disconnect included — nothing is left
        # parked waiting on a console that has gone away.
        for a in approvals.pending(run_id):
            approvals.decide(a.id, False, "the conversation ended before you answered")
        if proc.poll() is None:
            proc.kill()


def ask_sync(question: str, session_id: Optional[str] = None) -> Dict:
    """Run to completion and return {answer, strat, tools, approvals, ...}.

    Nothing here answers an approval, so a write asked for by a headless
    caller parks until it expires. Callers that CAN ask a human (mod.py's
    `ask` fn prompts on the terminal) should stream `ask()` instead.
    """
    out: Dict = {"answer": "", "strat": None, "tools": [], "approvals": [],
                 "session_id": session_id}
    for ev in ask(question, session_id):
        if ev["type"] == "approval":
            out["approvals"].append(ev["approval"])
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
