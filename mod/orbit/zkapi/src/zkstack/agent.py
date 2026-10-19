"""zkapi agents — named agents on the fleet's agent contract.

    GET  /agents                    {"agents": [...]}  (what orbit/build probes before mounting)
    GET  /agents/{id}               one agent, params included
    POST /agents                    create {id, name?, ...params}
    POST /agents/{id}               edit params (PUT works too)
    POST /agents/{id}/delete        remove (the shipped agent can be reset, not removed)
    POST /run | /run/stream         {query, agent_type?|agent?, model?, prompt?}  SSE: step ... done | error
    POST /agents/{id}/run/stream    same, agent picked by path
    GET  /.well-known/agent.json    the agent/1.0 card

Events are orbit/agent's ({type: step, step: {tool, params, result}} then {type: done, ...}),
so a run renders the same in the build console as anywhere else.

TWO BRAINS
    zkapi   (default) every model call IS a zkAPI request: the agent owns a devnet wallet with a
            vault deposit, proves solvency for each call, gets an anonymous lease, and settles
            the charge. Steps show the lease and the settlement. The model is whatever the lease
            provider serves (zkapi/echo unless ZKAPI_UPSTREAM_* is set).
    claude  orbit/claudecode's POST /run/stream with the agent's prompt as the goal and the
            module's API in context. Bills the host Claude account, so it only runs for the
            operator / the build owner.

PARAMS are the agent: name, description, brain, model, prompt, max_tokens, temperature,
deposit_gwei. They live in state/agents.json.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import paths

CLAUDE = os.environ.get("ZKAPI_AGENT_URL", "http://127.0.0.1:51260").rstrip("/")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
BRAINS = ("zkapi", "claude")
PARAM_KEYS = ("name", "description", "brain", "model", "prompt", "max_tokens", "temperature",
              "deposit_gwei", "icon", "color")

DEFAULT_ID = "zeke"
SHIPPED = {
    "id": DEFAULT_ID,
    "name": "Zeke",
    "icon": "Z",
    "color": "#7c9cff",
    "description": "zkapi's own agent. Every model call it makes is an anonymous, proof-paid zkAPI "
                   "request from its own vault deposit, so the provider never learns which deposit paid.",
    "brain": "zkapi",
    "model": "zkapi/echo",
    "prompt": "You are Zeke, the zkapi agent. zkapi (vbuterin/zkapi) lets people pay for metered APIs "
              "from an Ethereum vault deposit without the server learning which deposit is theirs: each "
              "request carries a Groth16 solvency proof, gets a short-lived spend-capped key, and the "
              "server signs the new balance. Answer plainly and briefly.",
    "max_tokens": 512,
    "temperature": 0.2,
    "deposit_gwei": 10_000_000,
}

CLAUDE_GOAL_TAIL = """
You are running as an agent of the zkapi module (/root/mod/mod/orbit/zkapi, API http://127.0.0.1:51420/zkapi/api).
Useful reads: GET /info, /devnet, /wallets, /networks/mainnet, /v1/models. Use curl for live facts."""


class AgentError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Agents:
    def __init__(self, devnet, state_dir: Path | None = None):
        self.devnet = devnet
        self.path = (state_dir or paths.STATE_DIR) / "agents.json"
        self._lock = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}

    # -- store -----------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        agents = data.get("agents") or {}
        if DEFAULT_ID not in agents:
            agents[DEFAULT_ID] = {**SHIPPED, "created_at": int(time.time()), "updated_at": int(time.time())}
        return {"agents": agents, "wallets": data.get("wallets") or {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)  # wallet tokens live here
        tmp.replace(self.path)

    @staticmethod
    def public(a: dict) -> dict:
        return {"id": a["id"], "name": a.get("name") or a["id"], "description": a.get("description", ""),
                "icon": a.get("icon", "Z"), "color": a.get("color", "#7c9cff"),
                "brain": a.get("brain", "zkapi"), "model": a.get("model", ""),
                "shipped": a["id"] == DEFAULT_ID,
                "params": {k: a.get(k) for k in PARAM_KEYS},
                "run": f"/zkapi/api/agents/{a['id']}/run/stream",
                "updated_at": a.get("updated_at"), "published": a.get("published")}

    def list(self) -> list[dict]:
        data = self._load()
        rows = [self.public(a) for a in data["agents"].values()]
        rows.sort(key=lambda r: (not r["shipped"], r["id"]))
        return rows

    def get(self, aid: str) -> dict:
        a = self._load()["agents"].get(aid)
        if not a:
            raise AgentError(404, f"no agent {aid!r}")
        return a

    def _clean(self, body: dict) -> dict:
        out: dict[str, Any] = {}
        for k in PARAM_KEYS:
            if k not in body or body[k] is None:
                continue
            v = body[k]
            if k == "brain":
                if v not in BRAINS:
                    raise AgentError(400, f"brain must be one of {', '.join(BRAINS)}")
            elif k == "max_tokens":
                v = max(16, min(int(v), 8192))
            elif k == "temperature":
                v = max(0.0, min(float(v), 2.0))
            elif k == "deposit_gwei":
                v = max(100_000, min(int(v), 1_000_000_000))
            else:
                v = str(v)[:8000 if k == "prompt" else 400]
            out[k] = v
        return out

    def create(self, body: dict) -> dict:
        aid = str(body.get("id") or body.get("name") or "").strip().lower().replace(" ", "-")
        if not ID_RE.match(aid):
            raise AgentError(400, "agent id: lowercase a-z 0-9 _ -, up to 32 chars")
        with self._lock:
            data = self._load()
            if aid in data["agents"]:
                raise AgentError(409, f"agent {aid!r} already exists")
            base = {k: v for k, v in SHIPPED.items() if k not in ("id", "name", "description")}
            a = {**base, "id": aid, "name": aid, "description": "", **self._clean(body),
                 "created_at": int(time.time()), "updated_at": int(time.time())}
            data["agents"][aid] = a
            self._save(data)
        return self.public(a)

    def update(self, aid: str, body: dict) -> dict:
        with self._lock:
            data = self._load()
            a = data["agents"].get(aid)
            if not a:
                raise AgentError(404, f"no agent {aid!r}")
            if body.get("reset") and aid == DEFAULT_ID:
                a = {**SHIPPED, "created_at": a.get("created_at"), "published": a.get("published")}
            a.update(self._clean(body))
            a["updated_at"] = int(time.time())
            data["agents"][aid] = a
            self._save(data)
        return self.public(a)

    def mark_published(self, aid: str, info: dict) -> None:
        with self._lock:
            data = self._load()
            for a in data["agents"].values():
                a["published"] = info
            self._save(data)

    def delete(self, aid: str) -> dict:
        if aid == DEFAULT_ID:
            raise AgentError(400, "the shipped agent can be edited or reset, not deleted")
        with self._lock:
            data = self._load()
            if data["agents"].pop(aid, None) is None:
                raise AgentError(404, f"no agent {aid!r}")
            self._save(data)
        return {"deleted": aid}

    def card(self) -> dict:
        return {"name": "zkapi agents", "version": "1.0", "protocol": "agent/1.0",
                "description": "Named agents whose model calls are paid anonymously through zkAPI.",
                "endpoints": {"agents": "/zkapi/api/agents", "run": "/zkapi/api/run",
                              "stream": "/zkapi/api/run/stream"},
                "brains": {"zkapi": "open on the devnet; every call is a proof-paid zkAPI request",
                           "claude": "operator / build owner only, via orbit/claudecode"},
                "agents": {a["id"]: a["run"] for a in self.list()}}

    # -- the agent's own wallet --------------------------------------------------------------------
    def _wallet(self, client, a: dict, emit) -> str:
        """The agent's devnet wallet, created + funded on first use, replaced once spent or closed."""
        data = self._load()
        name = data["wallets"].get(a["id"])
        if name:
            try:
                st = client.status(name)
                note = (st.get("state") or {}).get("note") or {}
                if not st.get("closed") and note and int(note.get("current_balance", 0)) > 100_000:
                    return name
            except Exception:  # noqa: BLE001 — a lost wallet is replaced below
                pass
        name = f"agent-{a['id']}-{secrets.token_hex(2)}"
        client.create_wallet(name, labels={"created_via": "agent", "agent": a["id"]})
        dep = client.deposit(name, int(a.get("deposit_gwei") or SHIPPED["deposit_gwei"]), self.devnet.sender())
        emit("deposit", {"wallet": name, "amount_gwei": dep.get("amount_gwei")},
             {"note_id": dep.get("note_id"), "tx": dep.get("transaction") or dep.get("tx_hash")})
        with self._lock:
            data = self._load()
            data["wallets"][a["id"]] = name
            self._save(data)
        return name

    # -- runs -----------------------------------------------------------------------------------------
    def run(self, body: dict, may_claude: bool, on_event: Callable[[dict], None]) -> dict:
        aid = str(body.get("agent_type") or body.get("agent") or DEFAULT_ID)
        if aid in ("default", "zkapi"):
            aid = DEFAULT_ID
        a = dict(self.get(aid))
        query = str(body.get("query") or body.get("prompt_text") or "").strip()
        if not query:
            raise AgentError(400, "query required")
        # per-run overrides from the console (its model chip, its system prompt)
        m = str(body.get("model") or "")
        if m and (a.get("brain") == "claude") == m.startswith("claude"):
            a["model"] = m
        extra = str(body.get("prompt") or "").strip()
        steps: list[dict] = []

        def emit(tool, params, result):
            st = {"tool": tool, "params": params, "result": result}
            steps.append(st)
            on_event({"type": "step", "step": st})

        emit("agent", {"id": a["id"], "name": a.get("name"), "brain": a.get("brain"), "model": a.get("model")},
             "params loaded")
        if a.get("brain") == "claude":
            if not may_claude:
                raise AgentError(403, f"{a['id']} runs on the claude brain, which bills the host account: "
                                      "operator or build owner only. Switch its brain to zkapi to run it openly.")
            return self._run_claude(a, query, extra, body, emit, steps, on_event)
        return self._run_zkapi(a, query, extra, emit, steps, on_event)

    def _run_zkapi(self, a, query, extra, emit, steps, on_event) -> dict:
        if not self.devnet.running():
            raise AgentError(503, "devnet is not running — start it on the zkapi DEVNET card")
        client = self.devnet.client()
        lock = self._run_locks.setdefault(a["id"], threading.Lock())
        with lock:  # one wallet, one in-flight request
            wallet = self._wallet(client, a, emit)
            system = "\n\n".join(x for x in (a.get("prompt"), extra) if x)
            messages = ([{"role": "system", "content": system}] if system else []) + \
                [{"role": "user", "content": query}]
            payload = {"model": a.get("model") or "zkapi/echo", "messages": messages,
                       "max_tokens": a.get("max_tokens"), "temperature": a.get("temperature")}
            res = client.chat(wallet, {k: v for k, v in payload.items() if v is not None})
        lease, s = res["lease"], res["settlement"]
        emit("zkapi:lease", {"wallet": wallet, "solvency_bound_gwei": lease["solvency_bound_gwei"]},
             {"request_nullifier": lease["request_nullifier"], "key_source": lease.get("key_source")})
        emit("zkapi:settle", {"charge_gwei": s.get("charge_gwei")}, {"balance_gwei": s.get("balance_gwei")})
        comp = res["completion"] if isinstance(res["completion"], dict) else {}
        if res["status"] != 200:
            raise AgentError(400, f"inference failed: {json.dumps(comp)[:400]}")
        answer = ((comp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        emit("finish", {"summary": answer}, answer)
        done = {"type": "done", "result": steps, "answer": answer, "agent": a["id"],
                "usage": {"model": a.get("model"), "charge_gwei": s.get("charge_gwei"),
                          "balance_gwei": s.get("balance_gwei"), "elapsed_s": res.get("elapsed_s")}}
        on_event(done)
        return done

    def _run_claude(self, a, query, extra, body, emit, steps, on_event) -> dict:
        goal = "\n\n".join(x for x in (a.get("prompt"), extra, CLAUDE_GOAL_TAIL) if x)
        req = urllib.request.Request(CLAUDE + "/run/stream", method="POST",
                                     headers={"content-type": "application/json"},
                                     data=json.dumps({"query": query, "goal": goal, "path": str(paths.MODULE_DIR),
                                                      "model": a.get("model") if str(a.get("model", "")).startswith("claude") else None,
                                                      "timeout": int(body.get("timeout") or 600)}).encode())
        try:
            resp = urllib.request.urlopen(req, timeout=1800)
        except (urllib.error.URLError, OSError) as e:
            raise AgentError(503, f"orbit/claudecode unreachable at {CLAUDE}: {e}")
        done = None
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("type") == "step":
                    steps.append(ev.get("step") or {})
                    on_event(ev)
                elif ev.get("type") == "done":
                    done = {**ev, "agent": a["id"]}
                elif ev.get("type") == "error":
                    raise AgentError(400, ev.get("error") or "agent error")
        done = done or {"type": "done", "result": steps, "agent": a["id"]}
        on_event(done)
        return done
