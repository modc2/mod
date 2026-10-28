"""
feedback - a builder/reviewer feedback loop, as one agent.

One task, N rounds. Each round a BUILDER agent does the task; then a REVIEWER
agent inspects what is on disk and writes a critique ending in a verdict. The
next round's builder gets the original task plus that critique, so every round
starts from the last one's work and the last one's review:

    round 1:  builder(task)              -> reviewer -> review 1
    round 2:  builder(task + review 1)   -> reviewer -> review 2
    round 3:  builder(task + review 2)   -> reviewer -> review 3 (final report)

Builder and reviewer are themselves agents in the agent protocol (any mod that
answers GET /agents + POST /run/stream) — by default both are orbit/claudecode.
This module only orchestrates: it never edits a file itself.

It speaks the same contract, so a console that mounts agent mods (orbit/build's
roster) runs it like any other agent:

    GET  /agents        roster (one agent: feedback)
    GET  /params        the panel schema — rounds (default 3), builder, reviewer, ...
    POST /run/stream    SSE step/chain_step/done/error, orbit/agent shape
    GET  /tasks/{id}    cost readback

Usage:
    import mod as m
    fb = m.mod('feedback')()
    fb.run('add a /health route', path='/repo', rounds=3, on_event=print)
"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import mod as m
    print = m.print
except ImportError:
    m = None

DEFAULT_ROUNDS = 3
MAX_ROUNDS = 10
# where builder/reviewer run when nobody names an agent
DEFAULT_AGENT = "claudecode"
DEFAULT_BASES = {"claudecode": "http://127.0.0.1:51260"}
# per-stage wall clock, handed to the sub-agent
DEFAULT_TIMEOUT = 1800
MAX_TIMEOUT = 7200
# a review is fed to the next builder — keep it a prompt, not a novel
MAX_REVIEW_CHARS = 8000

REVIEWER_PROMPT = """You are the REVIEWER in a builder/reviewer feedback loop.
A builder agent just worked on the task below. Inspect the actual result in the
working directory — read the changed files, run the tests or the build if there
are any, check `git diff` / `git status` when it is a repo. Judge whether the task
is done, correct, and well made.

DO NOT edit, create or delete any files. You only review; the builder fixes.

Answer with:
1. A short assessment (what was done, what is wrong or missing).
2. A numbered list of concrete, actionable changes for the builder — name files,
   functions and behaviours. Most important first. Skip nitpicks if there are
   real problems.
3. A final line, exactly one of:
   VERDICT: PASS     (nothing important left to change)
   VERDICT: REVISE   (the builder should do another round)"""

BUILDER_ROUND_NOTE = """--- FEEDBACK LOOP · round {round}/{rounds} ---
You are the BUILDER. A previous round already worked on this task and its changes
are on disk — build on them, do not start over. A reviewer inspected that work
and wrote the review below. Address every point it raises (if you disagree with
one, say why in your final summary).

REVIEW OF ROUND {prev}:
{review}"""

VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*(PASS|REVISE)", re.I)


def verdict_of(text: str) -> str:
    """'PASS' | 'REVISE' | '?' — the LAST verdict line wins."""
    found = VERDICT_RE.findall(text or "")
    return found[-1].upper() if found else "?"


def clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def agent_bases() -> Dict[str, str]:
    """name -> base url for every agent this box can call: orbit/build's
    mounted-agent roster (~/.mod/build/agent_mods.json) over the defaults.
    'feedback' itself is never a valid stage — that would recurse forever."""
    bases = dict(DEFAULT_BASES)
    p = Path(os.path.expanduser("~/.mod/build/agent_mods.json"))
    try:
        for e in json.loads(p.read_text()):
            if e.get("value") and e.get("base_url"):
                bases[str(e["value"])] = str(e["base_url"]).rstrip("/")
    except Exception:
        pass
    bases.pop("feedback", None)
    return bases


class Mod:
    description = "builder/reviewer feedback loop agent"
    name = "feedback"
    label = "Feedback loop"

    def __init__(self, **kwargs):
        self.config = kwargs

    # ── the agent contract ───────────────────────────────────────────

    def agents(self) -> Dict[str, Any]:
        return {"agents": [{
            "name": self.name,
            "label": self.label,
            "description": ("Runs the task for N rounds: a builder agent does it, a reviewer "
                            "agent critiques it, the next builder round gets the critique."),
            "module": "feedback",
            "available": True,
            "params": [f["name"] for f in self.params()["fields"]],
        }]}

    def params(self) -> Dict[str, Any]:
        """The panel schema, in orbit/agent's GET /params shape."""
        names = sorted(agent_bases())
        opts = [{"value": n, "label": n} for n in names]
        return {
            "agent": "feedback",
            "module": "orbit/feedback",
            "title": "FEEDBACK PARAMS",
            "version": "0.1.0",
            "icon": "↻",
            "accent": "#34d399",
            "note": "Builder does the task, reviewer critiques, repeat. rounds = how many builder passes.",
            "fields": [
                {"name": "rounds", "label": "ROUNDS", "type": "number", "default": DEFAULT_ROUNDS,
                 "min": 1, "max": MAX_ROUNDS, "step": 1,
                 "hint": "How many builder passes; a reviewer runs after each one."},
                {"name": "builder", "label": "BUILDER", "type": "select", "default": DEFAULT_AGENT,
                 "options": opts, "hint": "The agent that does the work each round."},
                {"name": "reviewer", "label": "REVIEWER", "type": "select", "default": DEFAULT_AGENT,
                 "options": opts, "hint": "The agent that critiques the work between rounds (told not to edit)."},
                {"name": "stop_on_pass", "label": "STOP ON PASS", "type": "toggle", "default": False,
                 "hint": "End early when the reviewer says VERDICT: PASS instead of running every round."},
                {"name": "builder_model", "label": "BUILDER MODEL", "type": "text", "default": "",
                 "placeholder": "claude-opus-5-5", "hint": "Model for the builder (blank = the agent's default)."},
                {"name": "reviewer_model", "label": "REVIEWER MODEL", "type": "text", "default": "",
                 "placeholder": "claude-sonnet-5", "hint": "Model for the reviewer (blank = the agent's default)."},
            ],
        }

    # ── one stage: a whole run on another agent ──────────────────────

    @staticmethod
    def _stage(base: str, body: Dict[str, Any], emit: Callable[[dict], None],
               stop: threading.Event) -> Dict[str, Any]:
        """POST {base}/run/stream, relay its steps, return
        {summary, cost, error}."""
        import requests

        out = {"summary": "", "cost": 0.0, "error": None, "task_id": None}
        try:
            r = requests.post(f"{base}/run/stream", json=body, stream=True,
                              timeout=(10, MAX_TIMEOUT))
        except Exception as e:
            out["error"] = f"agent at {base} unreachable: {e}"
            return out
        if r.status_code >= 400:
            out["error"] = f"agent at {base} HTTP {r.status_code}: {r.text[:300]}"
            return out
        texts: List[str] = []
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if stop.is_set():
                    break
                if not raw or not raw.startswith("data: "):
                    continue
                try:
                    ev = json.loads(raw[6:])
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "step":
                    step = ev.get("step") or {}
                    p = step.get("params") or {}
                    if step.get("tool") == "finish":
                        out["summary"] = str(p.get("summary") or "")
                        usage = p.get("usage") or {}
                        if usage.get("cost"):
                            out["cost"] = float(usage["cost"])
                    elif step.get("tool") == "think" and p.get("thought"):
                        texts.append(str(p["thought"]))
                    emit({"type": "step", "step": step})
                elif kind == "done":
                    out["task_id"] = ev.get("task_id")
                    usage = ev.get("usage") or {}
                    if usage.get("cost") and not out["cost"]:
                        out["cost"] = float(usage["cost"])
                    charged = ev.get("charged") or {}
                    if isinstance(charged, dict) and charged.get("charged") and not out["cost"]:
                        out["cost"] = float(charged["charged"])
                elif kind == "error":
                    out["error"] = str(ev.get("error") or "unknown agent error")
        except Exception as e:
            out["error"] = out["error"] or f"stream from {base} broke: {e}"
        finally:
            r.close()
        # an agent that ended without a finish step: its last thought is the answer
        if not out["summary"] and texts:
            out["summary"] = texts[-1]
        return out

    # ── the loop ─────────────────────────────────────────────────────

    def run(self, query: str, rounds: int = DEFAULT_ROUNDS, builder: str = DEFAULT_AGENT,
            reviewer: str = DEFAULT_AGENT, stop_on_pass: bool = False,
            builder_model: str = None, reviewer_model: str = None, model: str = None,
            prompt: str = None, path: str = None, timeout: int = DEFAULT_TIMEOUT,
            on_event: Callable[[dict], None] = None,
            stop: threading.Event = None, **kwargs) -> Dict[str, Any]:
        """Run the feedback loop to completion.

        Args:
            query: the task (a mounting console prepends its working directory)
            rounds: builder passes, 1..10 (a reviewer runs after each)
            builder / reviewer: agent names (see params() for what is reachable)
            stop_on_pass: end early on the reviewer's VERDICT: PASS
            builder_model / reviewer_model: per-role model; `model` is the fallback
            prompt: the console's system prompt — given to the builder only
            path: working directory forwarded to both agents
            on_event: called with every SSE-shaped event (step/chain_step)
        Returns {rounds: [...], cost, verdict, summary}.
        """
        try:
            rounds = int(rounds or DEFAULT_ROUNDS)
        except (TypeError, ValueError):
            rounds = DEFAULT_ROUNDS
        rounds = max(1, min(rounds, MAX_ROUNDS))
        timeout = max(60, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
        stop = stop or threading.Event()
        bases = agent_bases()
        for role, who in (("builder", builder), ("reviewer", reviewer)):
            if who not in bases:
                raise ValueError(f"{role} agent '{who}' is not reachable here — pick one of: "
                                 f"{', '.join(sorted(bases))}")

        def emit(ev: dict):
            if on_event:
                try:
                    on_event(ev)
                except Exception:
                    pass

        def say(text: str):
            emit({"type": "step", "step": {"tool": "think", "params": {"thought": text}}})

        history: List[Dict[str, Any]] = []
        total = 0.0
        stage = 0
        review = ""
        for r in range(1, rounds + 1):
            if stop.is_set():
                break
            # ── builder ──
            q = query if r == 1 else (
                query + "\n\n" + BUILDER_ROUND_NOTE.format(
                    round=r, rounds=rounds, prev=r - 1,
                    review=clip(review, MAX_REVIEW_CHARS)))
            emit({"type": "chain_step", "index": stage,
                  "agent": f"builder ({builder}) · round {r}/{rounds}"})
            stage += 1
            b_body = {"query": q, "timeout": timeout}
            if prompt:
                b_body["prompt"] = prompt
            if builder_model or model:
                b_body["model"] = builder_model or model
            if path:
                b_body["path"] = path
            b = self._stage(bases[builder], b_body, emit, stop)
            total += b["cost"]
            rec = {"round": r, "builder": b["summary"], "builder_cost": b["cost"]}
            history.append(rec)
            if b["error"]:
                rec["error"] = b["error"]
                raise RuntimeError(f"round {r} builder failed: {b['error']}")
            if stop.is_set():
                break

            # ── reviewer ──
            emit({"type": "chain_step", "index": stage,
                  "agent": f"reviewer ({reviewer}) · round {r}/{rounds}"})
            stage += 1
            rv_query = (f"{query}\n\n--- WHAT THE BUILDER REPORTED (round {r}/{rounds}) ---\n"
                        f"{clip(b['summary'], MAX_REVIEW_CHARS) or '(no summary)'}\n\n"
                        "Review the work now on disk for the task above.")
            rv_body = {"query": rv_query, "prompt": REVIEWER_PROMPT, "timeout": timeout}
            if reviewer_model or model:
                rv_body["model"] = reviewer_model or model
            if path:
                rv_body["path"] = path
            rv = self._stage(bases[reviewer], rv_body, emit, stop)
            total += rv["cost"]
            if rv["error"]:
                rec["error"] = rv["error"]
                raise RuntimeError(f"round {r} reviewer failed: {rv['error']}")
            review = rv["summary"]
            verdict = verdict_of(review)
            rec.update(review=review, verdict=verdict, reviewer_cost=rv["cost"])
            emit({"type": "step", "step": {"tool": "verdict", "params": {
                "round": r, "of": rounds, "verdict": verdict}}})
            if stop_on_pass and verdict == "PASS":
                say(f"reviewer passed round {r} — stopping early (stop_on_pass)")
                break

        verdicts = " → ".join(h.get("verdict", "?") for h in history)
        final = history[-1].get("verdict", "?") if history else "?"
        summary = (f"↻ Feedback loop · {len(history)}/{rounds} rounds · {builder} ⇄ {reviewer}\n"
                   f"Verdicts: {verdicts}\n\n"
                   f"Final review (round {len(history)}):\n{review or '(none)'}")
        emit({"type": "step", "step": {"tool": "finish", "params": {
            "summary": summary, "usage": {"cost": round(total, 6)}}}})
        return {"rounds": history, "cost": total, "verdict": final, "summary": summary}

    # ── server ───────────────────────────────────────────────────────

    def serve(self, port: int = None, host: str = "127.0.0.1"):
        """Serve the agent contract. Loopback-only: a run drives builder and
        reviewer agents with their permissions off, so reaching the port IS
        the access control (same rule as orbit/claudecode)."""
        import queue as _queue

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse
        import uvicorn

        if port is None:
            try:
                cfg = json.loads((Path(__file__).parent / "config.json").read_text())
                port = int(cfg.get("port") or 51470)
            except Exception:
                port = 51470

        mod_self = self
        tasks: Dict[str, Dict[str, Any]] = {}
        app = FastAPI(title="feedback", docs_url=None, redoc_url=None)

        @app.get("/agents")
        def agents():
            return mod_self.agents()

        @app.get("/params")
        def params():
            return mod_self.params()

        @app.get("/tasks/{task_id}")
        def task(task_id: str):
            t = tasks.get(task_id)
            if not t:
                return JSONResponse({"error": "no such task"}, status_code=404)
            return t

        def run_kwargs(body: Dict[str, Any]) -> Dict[str, Any]:
            def b(v):
                return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            kw = {
                "query": str(body.get("query") or ""),
                "rounds": body.get("rounds") or DEFAULT_ROUNDS,
                "builder": body.get("builder") or DEFAULT_AGENT,
                "reviewer": body.get("reviewer") or DEFAULT_AGENT,
                "stop_on_pass": b(body.get("stop_on_pass") or False),
                "builder_model": body.get("builder_model") or None,
                "reviewer_model": body.get("reviewer_model") or None,
                # the console's "auto" chip is a router hint, not a model id
                "model": (body.get("model") if body.get("model") not in (None, "", "auto") else None),
                "prompt": body.get("prompt") or body.get("goal") or None,
                "path": body.get("path") or body.get("work_dir") or None,
                "timeout": body.get("timeout") or DEFAULT_TIMEOUT,
            }
            return kw

        @app.post("/run/stream")
        async def run_stream(request: Request):
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
            kw = run_kwargs(body)
            if not kw["query"]:
                return JSONResponse({"error": "query is required"}, status_code=400)
            task_id = uuid.uuid4().hex[:12]
            tasks[task_id] = {"id": task_id, "status": "running", "cost": 0.0,
                              "started_at": time.time(), "finished_at": None,
                              "rounds": kw["rounds"]}
            q: "_queue.Queue" = _queue.Queue()
            stop = threading.Event()

            def worker():
                t = tasks[task_id]
                try:
                    res = mod_self.run(**kw, on_event=q.put, stop=stop)
                    t.update(status="done", cost=float(res["cost"]), verdict=res["verdict"])
                    q.put({"type": "done", "result": res["rounds"], "task_id": task_id,
                           "charged": None, "usage": {"cost": round(res["cost"], 6)}})
                except Exception as e:
                    t["status"] = "error"
                    q.put({"type": "error", "error": str(e), "task_id": task_id})
                finally:
                    t["finished_at"] = time.time()
                    q.put(None)

            threading.Thread(target=worker, daemon=True).start()

            def gen():
                try:
                    while True:
                        ev = q.get()
                        if ev is None:
                            break
                        yield f"data: {json.dumps(ev)}\n\n"
                finally:
                    # console detached (cancel) — no further stages start
                    stop.set()

            return StreamingResponse(gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})

        uvicorn.run(app, host=host, port=port, log_level="warning")

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, query: str = None, **kwargs) -> Any:
        """forward()          -> the params schema
           forward('task')    -> run the loop here, returning its record"""
        if not query:
            return self.params()
        return self.run(query, **kwargs)
