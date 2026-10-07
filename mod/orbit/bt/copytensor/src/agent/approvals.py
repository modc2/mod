"""
copytensor.agent.approvals — the human in the loop.

The agent may now call the ops tools (start a copy, re-size one, sync the
book to the chain), but every one of those is a *write*, and a write does
not happen until a human says so. This module is that gate.

    agent run                       API process                 console
    ─────────                       ───────────                 ───────
    ct_create_copy(...)
      └─ mcp subprocess ── POST /agent/approvals ──▶ create()
                                                     state=pending ──▶ SSE
                        ◀─ /agent/approvals/{id}/wait   (blocks)        card
                                                     decide() ◀── POST decision
      tool result = the trade, or "declined: ..."

Two properties worth keeping:

* The gate is server-side. It is not a confirm() the console draws over a
  call that already went out — the call is *parked* here and never reaches
  the API route unless somebody approves it. A second MCP client, a resumed
  session or a curl against /mcp hits the same gate.
* A decline is a tool RESULT, not a crash. The model reads "declined by the
  human: too much TAO" and answers to it, which is the whole point of an
  agent you argue with rather than one you fight.

The store is in-process (single uvicorn worker, same process as the SSE
generator that shows the cards). Pending records are capped and swept, so a
browser that walks away leaks nothing: an undecided request expires into
`declined` after its TTL.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# How long a request waits for a human before it declines itself. Long
# enough to read a basket and think; short enough that a forgotten tab does
# not hold a claude subprocess open all day.
TTL_SEC = float(os.environ.get("COPYTENSOR_APPROVAL_TTL", "600"))

# Ceiling on remembered records — decided ones are kept a while so a
# reloading console can still render what it just approved.
KEEP = 200
KEEP_DECIDED_SEC = 900.0

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"


@dataclass
class Approval:
    """One parked write, and what became of it."""
    id: str
    run_id: str
    tool: str
    args: Dict
    summary: str
    risk: str                     # low | medium | high — how the card is drawn
    created: float
    ttl: float
    state: str = PENDING
    note: str = ""                # the human's reason, handed to the model
    decided_at: Optional[float] = None
    _seen: bool = field(default=False, repr=False)

    def expires_in(self) -> float:
        return max(0.0, self.created + self.ttl - time.time())

    def public(self) -> Dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        d["expires_in"] = round(self.expires_in(), 1)
        return d


_lock = threading.Condition()
_store: Dict[str, Approval] = {}


def _sweep_locked() -> None:
    now = time.time()
    for a in list(_store.values()):
        if a.state == PENDING and a.expires_in() <= 0:
            a.state = DECLINED
            a.note = a.note or "no answer before the request expired"
            a.decided_at = now
    if len(_store) > KEEP:
        stale = [a for a in _store.values()
                 if a.state != PENDING and now - (a.decided_at or now) > KEEP_DECIDED_SEC]
        for a in sorted(stale, key=lambda x: x.decided_at or 0)[:len(_store) - KEEP]:
            _store.pop(a.id, None)


def create(run_id: str, tool: str, args: Dict, summary: str = "",
           risk: str = "medium", ttl: Optional[float] = None) -> Approval:
    """Park a write and return the record the console will render."""
    a = Approval(id=uuid.uuid4().hex[:12], run_id=run_id or "-", tool=tool,
                 args=args or {}, summary=summary or tool, risk=risk,
                 created=time.time(), ttl=float(ttl or TTL_SEC))
    with _lock:
        _sweep_locked()
        _store[a.id] = a
        _lock.notify_all()
    return a


def get(approval_id: str) -> Optional[Approval]:
    with _lock:
        _sweep_locked()
        return _store.get(approval_id)


def decide(approval_id: str, approve: bool, note: str = "") -> Optional[Approval]:
    """Answer a parked write. Deciding twice keeps the FIRST answer."""
    with _lock:
        _sweep_locked()
        a = _store.get(approval_id)
        if a is None or a.state != PENDING:
            return a
        a.state = APPROVED if approve else DECLINED
        a.note = (note or "").strip()
        a.decided_at = time.time()
        _lock.notify_all()
        return a


def wait(approval_id: str, timeout: float = 25.0) -> Optional[Approval]:
    """Block until this request is decided, or the timeout lapses.

    Callers poll this in chunks (the MCP client does 25 s at a time), so a
    wait longer than any HTTP read timeout still survives.
    """
    deadline = time.time() + timeout
    with _lock:
        while True:
            _sweep_locked()
            a = _store.get(approval_id)
            if a is None or a.state != PENDING:
                return a
            left = min(deadline, a.created + a.ttl) - time.time()
            if left <= 0:
                _sweep_locked()
                return _store.get(approval_id)
            _lock.wait(left)


def pending(run_id: Optional[str] = None) -> List[Approval]:
    with _lock:
        _sweep_locked()
        rows = [a for a in _store.values() if a.state == PENDING
                and (run_id is None or a.run_id == run_id)]
    return sorted(rows, key=lambda a: a.created)


def waiting(run_id: str) -> bool:
    """Is this run parked on a human? The watchdog must not count that time."""
    return bool(pending(run_id))


def take_new(run_id: str) -> List[Approval]:
    """Pending requests this run has not streamed to the console yet.

    The SSE generator is otherwise blocked reading the agent's stdout, and a
    parked tool produces no stdout — so the cards come from here.
    """
    out = []
    with _lock:
        _sweep_locked()
        for a in sorted(_store.values(), key=lambda x: x.created):
            if a.run_id == run_id and a.state == PENDING and not a._seen:
                a._seen = True
                out.append(a)
    return out


def resolved_since(run_id: str, after: float) -> List[Approval]:
    """Decisions for this run made after `after` — so the card can settle."""
    with _lock:
        _sweep_locked()
        rows = [a for a in _store.values()
                if a.run_id == run_id and a.state != PENDING
                and (a.decided_at or 0) > after]
    return sorted(rows, key=lambda a: a.decided_at or 0)


def reset() -> None:
    """Tests only."""
    with _lock:
        _store.clear()
