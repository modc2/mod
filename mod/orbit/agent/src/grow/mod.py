"""
grow - the console adds a new tool and a new agent every minute, by itself

One tick = one matched pair: a custom tool, and an agent built to wield it.
A daemon thread ticks every `interval` seconds (default 60). Only the
module's owner can change how it grows; anyone can watch it.

Engines — where a tick's pair comes from:

    local   the hand-written recipe catalog (grow/recipes.py). No model, no
            key, no network: read-only find/grep/git tools and the agent that
            runs each. Free, safe by construction, finite (~180 pairs).
    model   the tool-builder agent drafts a tool (command checked by
            safe_command before it is filed), then the vibe-builder designs
            an agent around it. Two model runs.
    scout   a model-drafted tool, plus an agent idea scouted off the internet
            (Mod.agent_scout). Three model runs and a handful of web fetches.
    auto    local until the catalog is used up, then model.        (default)

Guards, because a timer that spends is how a fleet loses its quota (the arena
scheduler once burned a day of free-tier calls in a morning):

    model_daily_cap  model runs per UTC day; past it, model/scout ticks fall
                     back to local, or wait for tomorrow
    cooldown         a 429 / quota error parks model engines for an hour (or
                     until the reset time the provider names)
    max_tools/agents stop adding once this many grown items exist; nothing is
                     ever deleted by the grower itself — prune is a manual,
                     owner-only act
    no overlap       a tick that lands while one is running is skipped

State is off-tree (~/.mod/agent/grow/state.json): config, every grown item,
the last LOG_KEEP tick results, the day's model-run count, the cooldown.

The engine never imports the module. It talks to a host — ModHost below is
the adapter onto Mod — so grow is testable with a fake and reusable by any
module that can add a tool and an agent.
"""
import json
import re
import shlex
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .recipes import catalog

ENGINES = ("auto", "local", "model", "scout")
# model runs a tick costs on each engine — checked against the daily cap
# BEFORE the tick runs, so the cap is never overshot by a pair in flight
COST = {"local": 0, "model": 2, "scout": 3}
LOG_KEEP = 200
MIN_INTERVAL = 30
COOLDOWN = 3600

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "interval": 60,
    "engine": "auto",
    "max_tools": 500,
    "max_agents": 500,
    "model_daily_cap": 100,
    "free": True,            # model engines use the free tier by default
    "model": None,
    "provider": None,
    "theme": None,           # aims model/scout ideas; None = varied
}


def _now() -> float:
    return time.time()


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ── command safety (model-drafted tools only) ────────────────────────
# A drafted tool is a shell template the agents will call later. It is held
# to the same shape as the local recipes: a pipeline of read-only programs.
# This is an allowlist, not a denylist — anything not named here is refused.

SAFE_PROGRAMS = {
    "find", "grep", "egrep", "fgrep", "rg", "wc", "sort", "uniq", "head", "tail",
    "cut", "tr", "awk", "ls", "du", "df", "stat", "file", "cat", "md5sum",
    "sha256sum", "jq", "date", "basename", "dirname", "xargs", "column", "nl",
    "comm", "diff", "realpath", "tree", "echo", "printf", "git",
}
SAFE_GIT = {"log", "show", "diff", "status", "shortlog", "blame", "ls-files",
            "grep", "rev-list", "rev-parse", "describe"}
# flags that turn an allowed program into a writer or an executor
# (sed is left out of SAFE_PROGRAMS entirely: its w/e commands write and run)
BAD_FLAGS = {
    "sort": ("-o", "--output"), "find": ("-delete", "-fprint", "-fls", "-ok"),
    "tree": ("-o",), "rg": ("--pre",), "date": ("-s", "--set"),
    "git": ("-O", "--open-files-in-pager", "--output", "--ext-diff", "--exec"),
    "xargs": ("-I",),
}
# shell syntax that chains, substitutes, backgrounds or writes
BAD_SYNTAX = re.compile(r"(;|&&|\|\||`|\$\(|\$\{|(?<![0-9])>|>>|<|&\s*$|\n|\|&)")


def safe_command(command: str) -> Optional[str]:
    """None if the template is a read-only pipeline, else the reason it isn't.

    `2>/dev/null` is the one redirect allowed (stripped before checking).
    find's -exec/-execdir and xargs must hand off to an allowed program too.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return "empty command"
    if len(cmd) > 600:
        return "command too long"
    cmd = cmd.replace("2>/dev/null", "")
    m = BAD_SYNTAX.search(cmd)
    if m:
        return f"disallowed shell syntax: {m.group(0)!r}"
    if "system(" in cmd or "getline" in cmd:
        return "awk system()/getline is not allowed"
    for seg in cmd.split("|"):
        try:
            toks = shlex.split(seg.replace("{", "").replace("}", ""))
        except ValueError as e:
            return f"unparseable: {e}"
        if not toks:
            return "empty pipeline segment"
        why = _safe_argv(toks)
        if why:
            return why
    return None


def _safe_argv(toks: List[str]) -> Optional[str]:
    prog = toks[0].rsplit("/", 1)[-1]
    if prog not in SAFE_PROGRAMS:
        return f"program not allowed: {prog}"
    args = toks[1:]
    for a in args:
        if any(a == f or a.startswith(f) and not a.startswith(f + "-")
               for f in BAD_FLAGS.get(prog, ())) and not (prog == "find" and a == "-o"):
            return f"{prog} {a} writes or executes"
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")
                    and a not in ("-C",)), "")
        # `git -C {path} log` — skip the -C operand
        if "-C" in args:
            i = args.index("-C")
            rest = [a for a in args[i + 2:] if not a.startswith("-")]
            sub = rest[0] if rest else ""
        if sub not in SAFE_GIT:
            return f"git {sub or '?'} is not read-only"
    if prog == "find":
        for flag in ("-exec", "-execdir"):
            if flag in args:
                i = args.index(flag)
                if i + 1 >= len(args):
                    return f"find {flag} without a program"
                why = _safe_argv(args[i + 1:])
                if why:
                    return why
    if prog == "xargs":
        rest = [a for a in args if not a.startswith("-")]
        if rest:
            return _safe_argv(rest)
    return None


TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s[:n].strip("-")


def rate_limited(err: Any) -> bool:
    s = str(err or "").lower()
    return any(k in s for k in ("429", "rate limit", "rate-limit", "quota",
                                "too many requests", "free-models-per-day"))


# ── the engine ───────────────────────────────────────────────────────

class Grow:
    """Config, state and one tick. Knows nothing about Mod — see ModHost."""
    description = "Adds a tool and an agent every interval; owner-tuned, quota-guarded"

    def __init__(self, dir: str = None):
        self.dir = Path(dir) if dir else Path.home() / ".mod" / "agent" / "grow"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / "state.json"
        self._lock = threading.Lock()        # state file
        self._tick_lock = threading.Lock()   # one tick at a time
        self._state = self._load()
        self.ticking_since: Optional[float] = None

    # ── state ────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        try:
            st = json.loads(self._path.read_text())
        except Exception:
            st = {}
        st.setdefault("config", {})
        st.setdefault("grown", [])
        st.setdefault("log", [])
        st.setdefault("day", {"date": _day(), "model_runs": 0})
        st.setdefault("spent", [])   # recipe ids tried, whatever the outcome
        return st

    def _save(self):
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=1, default=str))
            tmp.replace(self._path)

    def config(self) -> Dict[str, Any]:
        return {**DEFAULTS, **{k: v for k, v in self._state["config"].items()
                               if k in DEFAULTS}}

    def set_config(self, **changes) -> Dict[str, Any]:
        """Validate and persist. Unknown keys raise — a typo should not
        silently leave the grower running on the old value. None = reset."""
        bad = [k for k in changes if k not in DEFAULTS]
        if bad:
            raise ValueError(f"unknown grow setting(s): {', '.join(bad)} "
                             f"(have: {', '.join(DEFAULTS)})")
        cfg = dict(self._state["config"])
        for k, v in changes.items():
            if v is None:
                cfg.pop(k, None)
                continue
            if k == "engine":
                v = str(v).lower()
                if v not in ENGINES:
                    raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
            elif k == "interval":
                v = max(MIN_INTERVAL, int(v))
            elif k in ("max_tools", "max_agents", "model_daily_cap"):
                v = max(0, int(v))
            elif k in ("enabled", "free"):
                v = bool(v)
            else:
                v = str(v).strip()[:120] or None
                if v is None:
                    cfg.pop(k, None)
                    continue
            cfg[k] = v
        self._state["config"] = cfg
        self._save()
        return self.config()

    def _roll_day(self):
        if self._state["day"].get("date") != _day():
            self._state["day"] = {"date": _day(), "model_runs": 0}

    def cooling(self) -> bool:
        return float(self._state.get("cooldown_until") or 0) > _now()

    def _cooldown(self, err: Any):
        until = _now() + COOLDOWN
        m = re.search(r"resets?\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)", str(err))
        if m:
            try:
                until = time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            except Exception:
                pass
        self._state["cooldown_until"] = until
        self._state["cooldown_reason"] = str(err)[:200]

    def grown(self, kind: str = None) -> List[Dict[str, Any]]:
        g = self._state["grown"]
        return [x for x in g if x.get("kind") == kind] if kind else list(g)

    def log(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._state["log"][:max(1, int(limit))]

    # ── choosing ─────────────────────────────────────────────────────

    def next_recipe(self, taken_tools: set, taken_agents: set) -> Optional[Dict]:
        """First recipe whose tool name is free. (An agent name that is taken
        gets a suffix at create time; a taken tool name means someone already
        has that tool, so the recipe is skipped.)"""
        done = set(self._state["spent"])
        for r in catalog():
            if r["id"] in done or r["tool"]["name"] in taken_tools:
                continue
            return r
        return None

    def remaining_local(self, taken_tools: set) -> int:
        done = set(self._state["spent"])
        return sum(1 for r in catalog()
                   if r["id"] not in done and r["tool"]["name"] not in taken_tools)

    def pick_engine(self, cfg: Dict, local_left: int) -> tuple:
        """(engine, why). None means this tick does nothing, and why says so."""
        self._roll_day()
        want = cfg["engine"]
        used = int(self._state["day"].get("model_runs", 0))

        def model_ok(engine):
            if self.cooling():
                return False, "model engines cooling down after a rate limit"
            if used + COST[engine] > int(cfg["model_daily_cap"]):
                return False, f"daily model cap reached ({used}/{cfg['model_daily_cap']})"
            return True, ""

        if want == "local":
            return ("local", "") if local_left else (None, "local catalog used up — "
                                                     "switch engine to auto/model to keep growing")
        if want == "auto":
            if local_left:
                return "local", ""
            ok, why = model_ok("model")
            return ("model", "") if ok else (None, why)
        ok, why = model_ok(want)
        if ok:
            return want, ""
        if local_left:
            return "local", f"fell back to local: {why}"
        return None, why

    # ── one tick ─────────────────────────────────────────────────────

    def tick(self, host, force: bool = False) -> Dict[str, Any]:
        """Grow one pair. `force` runs even when disabled (owner's manual tick)."""
        if not self._tick_lock.acquire(blocking=False):
            return {"skipped": "busy", "since": self.ticking_since}
        self.ticking_since = _now()
        started = _now()
        res: Dict[str, Any] = {"t": started}
        try:
            cfg = self.config()
            if not cfg["enabled"] and not force:
                return {**res, "skipped": "disabled"}
            n_tools, n_agents = len(self.grown("tool")), len(self.grown("agent"))
            want_tool = n_tools < int(cfg["max_tools"])
            want_agent = n_agents < int(cfg["max_agents"])
            if not (want_tool or want_agent):
                return {**res, "skipped": f"cap reached ({n_tools} tools, {n_agents} agents)"}
            taken_tools, taken_agents = host.taken_tools(), host.taken_agents()
            engine, why = self.pick_engine(cfg, self.remaining_local(taken_tools))
            if why:
                res["note"] = why
            if engine is None:
                return {**res, "skipped": why}
            res["engine"] = engine
            if COST[engine]:
                self._state["day"]["model_runs"] = int(self._state["day"]["model_runs"]) + COST[engine]
            try:
                if engine == "local":
                    self._grow_local(host, res, taken_tools, taken_agents,
                                     want_tool, want_agent)
                else:
                    self._grow_model(host, engine, cfg, res, want_tool, want_agent)
            except PermissionError:
                raise
            except Exception as e:
                res["error"] = str(e)[:300]
            for k in ("error", "tool_error", "agent_error"):
                if res.get(k) and rate_limited(res[k]):
                    self._cooldown(res[k])
                    res["cooldown_until"] = self._state["cooldown_until"]
                    break
            return res
        finally:
            res["elapsed"] = round(_now() - started, 2)
            if "skipped" not in res or res["skipped"] != "busy":
                self._state["last_tick"] = res
                self._state["log"] = [res, *self._state["log"]][:LOG_KEEP]
                try:
                    self._save()
                except Exception as e:
                    print(f"grow: could not persist state: {e}")
            self.ticking_since = None
            self._tick_lock.release()

    def _remember(self, kind: str, name: str, engine: str, **extra):
        self._state["grown"].append({"kind": kind, "name": name, "engine": engine,
                                     "t": _now(), **extra})

    def _grow_local(self, host, res, taken_tools, taken_agents, want_tool, want_agent):
        r = self.next_recipe(taken_tools, taken_agents)
        if r is None:
            raise RuntimeError("local catalog used up")
        res["recipe"] = r["id"]
        # spent whatever happens next — a recipe that fails to file must not
        # be retried every minute forever
        self._state["spent"].append(r["id"])
        tool = r["tool"]["name"]
        if want_tool:
            try:
                host.add_tool(r["tool"])
                self._remember("tool", tool, "local", recipe=r["id"])
                res["tool"] = tool
            except Exception as e:
                res["tool_error"] = str(e)[:300]
        if want_agent and (res.get("tool") or tool in host.taken_tools()):
            spec = dict(r["agent"], name=self._free(r["agent"]["name"], taken_agents))
            try:
                host.add_agent(spec)
                name = spec["name"]
                self._remember("agent", name, "local", recipe=r["id"], tool=tool)
                res["agent"] = name
            except Exception as e:
                res["agent_error"] = str(e)[:300]

    def _grow_model(self, host, engine, cfg, res, want_tool, want_agent):
        tool = None
        if want_tool:
            try:
                spec = host.draft_tool(cfg, avoid=sorted(host.taken_tools()))
                why = self._check_tool(spec, host.taken_tools())
                if why:
                    raise ValueError(f"drafted tool refused: {why}")
                host.add_tool(spec)
                tool = spec["name"]
                self._remember("tool", tool, engine)
                res["tool"] = tool
            except Exception as e:
                res["tool_error"] = str(e)[:300]
        if want_agent:
            try:
                made = (host.scout_agent(cfg) if engine == "scout"
                        else host.vibe_agent(cfg, tool=tool))
                name = made.get("name")
                if not name:
                    raise RuntimeError(made.get("error") or "no agent came back")
                self._remember("agent", name, engine, tool=tool)
                res["agent"] = name
            except Exception as e:
                res["agent_error"] = str(e)[:300]

    @staticmethod
    def _check_tool(spec: Dict, taken: set) -> Optional[str]:
        if not isinstance(spec, dict):
            return "not a tool spec"
        name = str(spec.get("name") or "")
        if not TOOL_NAME.match(name):
            return f"bad name {name!r}"
        if name in taken:
            return f"name taken: {name}"
        return safe_command(spec.get("command"))

    @staticmethod
    def _free(name: str, taken: set) -> str:
        if name not in taken:
            return name
        i = 2
        while f"{name}-{i}" in taken:
            i += 1
        return f"{name}-{i}"

    # ── reporting / pruning ──────────────────────────────────────────

    def status(self, host=None, scheduler=None) -> Dict[str, Any]:
        self._roll_day()
        cfg = self.config()
        taken = host.taken_tools() if host else set()
        out = {
            "config": cfg,
            "engines": list(ENGINES),
            "grown": {"tools": len(self.grown("tool")),
                      "agents": len(self.grown("agent"))},
            "local_left": self.remaining_local(taken),
            "local_total": len(catalog()),
            "today": dict(self._state["day"],
                          cap=cfg["model_daily_cap"]),
            "cooling": self.cooling(),
            "cooldown_until": self._state.get("cooldown_until") if self.cooling() else None,
            "cooldown_reason": self._state.get("cooldown_reason") if self.cooling() else None,
            "last_tick": self._state.get("last_tick"),
            "ticking": self.ticking_since is not None,
            "recent": [x for x in reversed(self._state["grown"][-20:])
                       if x["kind"] in ("tool", "agent")],
        }
        if scheduler is not None:
            out["scheduler"] = scheduler.status()
        return out

    def prune(self, host, kind: str = "all", count: int = None) -> Dict[str, Any]:
        """Remove grown items, oldest first. Only items the grower made —
        a user's own tool or agent is never touched."""
        kinds = ("tool", "agent") if kind in ("all", None) else (kind,)
        if any(k not in ("tool", "agent") for k in kinds):
            raise ValueError("kind must be tool, agent or all")
        removed, errors = [], []
        for k in kinds:
            items = self.grown(k)
            if count is not None:
                items = items[:max(0, int(count))]
            for it in items:
                try:
                    (host.rm_tool if k == "tool" else host.rm_agent)(it["name"])
                    removed.append({"kind": k, "name": it["name"]})
                except KeyError:
                    removed.append({"kind": k, "name": it["name"], "gone": True})
                except Exception as e:
                    errors.append({"kind": k, "name": it["name"], "error": str(e)[:200]})
                    continue
                self._state["grown"] = [x for x in self._state["grown"]
                                        if not (x["kind"] == k and x["name"] == it["name"])]
        self._save()
        return {"removed": removed, "errors": errors}


class Scheduler:
    """One daemon thread; a tick every `interval` measured from tick START, so
    a slow tick doesn't drift the cadence — and never two at once."""

    def __init__(self, grow: Grow, host):
        self.grow, self.host = grow, host
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_at = 0.0
        self.next_at = 0.0
        self.last_error: Optional[str] = None

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, delay: float = 20.0) -> Dict[str, Any]:
        if self.running():
            return self.status()
        self._stop.clear()
        self.started_at = _now()
        self.next_at = _now() + delay
        self._thread = threading.Thread(target=self._loop, args=(delay,),
                                        name="grow-scheduler", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        return self.status()

    def _loop(self, delay: float):
        # let the API finish booting — and a crash-looping process never
        # gets as far as spending anything
        if self._stop.wait(delay):
            return
        while True:
            t0 = _now()
            try:
                self.grow.tick(self.host)
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)[:300]
            interval = max(MIN_INTERVAL, int(self.grow.config()["interval"]))
            self.next_at = t0 + interval
            if self._stop.wait(max(1.0, self.next_at - _now())):
                return

    def status(self) -> Dict[str, Any]:
        return {"running": self.running(), "started_at": self.started_at or None,
                "next_at": self.next_at if self.running() else None,
                "last_error": self.last_error}


# ── the adapter onto orbit/agent's Mod ───────────────────────────────

TOOL_BUILDER = "tool-builder"


class ModHost:
    """What grow needs from the module, and nothing more.

    Everything runs as the module's OWN key (mod.key — the server key), not
    key=None: inside a built Mod, None resolves to no address and creating an
    agent says "sign in first". Grown agents are therefore filed under the
    server address, and the owner, as host, manages all of them."""

    def __init__(self, mod):
        self.mod = mod

    @property
    def key(self):
        return getattr(self.mod, "key", None)

    def taken_tools(self) -> set:
        return set(self.mod.tools.ls())

    def taken_agents(self) -> set:
        return set(self.mod.agents.ls())

    def add_tool(self, spec: Dict) -> Dict:
        return self.mod.tools.add(spec["name"], spec["command"],
                                  spec.get("description", ""), spec.get("params"),
                                  None, int(spec.get("timeout") or 60),
                                  owner=getattr(self.mod, "_owner", None))

    def add_agent(self, spec: Dict) -> Dict:
        return self.mod.agents.create(name=spec["name"], description=spec.get("description", ""),
                                      goal=spec.get("goal", ""), icon=spec.get("icon", "✦"),
                                      tools=spec.get("tools"), key=self.key)

    def rm_tool(self, name: str):
        if not self.mod.tools.custom.exists(name):
            raise KeyError(name)
        return self.mod.tools.rm(name)

    def rm_agent(self, name: str):
        return self.mod.agents.remove(name, key=self.key)

    def draft_tool(self, cfg: Dict, avoid: List[str]) -> Dict:
        mod = self.mod
        query = ("Design ONE new read-only shell tool for a coding agent console."
                 + (f"\nTHEME: {cfg['theme']}" if cfg.get("theme") else "")
                 + "\nTOOL NAMES ALREADY TAKEN (do not reuse or near-duplicate):\n"
                 + ", ".join(avoid[-300:]))
        trace = mod._draft_trace(query=query, agent_type=TOOL_BUILDER,
                                 model=cfg.get("model"), provider=cfg.get("provider"),
                                 free=bool(cfg.get("free")), steps=3, key=self.key,
                                 path=str(Path.home() / ".mod" / "agent"))
        answer = mod._answer_text([trace] if isinstance(trace, list) else [])
        spec = parse_tool_json(answer)
        if spec is None and isinstance(trace, list):
            spec = mod._spec_scan(trace, parse_tool_json)
        if spec is None:
            err = next((str(s.get("error")) for s in (trace or [])
                        if isinstance(s, dict) and s.get("error")), "")
            raise RuntimeError(err or "the tool-builder returned no tool spec")
        return spec

    def vibe_agent(self, cfg: Dict, tool: str = None) -> Dict:
        if tool:
            t = self.mod.tools.custom.get(tool)
            brief = (f"An agent built around the `{tool}` tool ({t.description}). "
                     f"It runs that tool first, reads what it surfaces, and reports "
                     f"concrete findings with file:line. Read-only. Include `{tool}` "
                     f"in its tools.")
        else:
            brief = ("A small, sharp, read-only agent for a coding console that does "
                     "one job no generic helper does well"
                     + (f", on the theme: {cfg['theme']}" if cfg.get("theme") else "") + ".")
        out = self.mod.agent_vibe(brief, model=cfg.get("model"), provider=cfg.get("provider"),
                                  free=bool(cfg.get("free")), steps=4, save=True, key=self.key)
        if out.get("error"):
            raise RuntimeError(out["error"])
        return {"name": out["draft"]["name"]}

    def scout_agent(self, cfg: Dict) -> Dict:
        run = self.mod.agent_scout(theme=cfg.get("theme"), reads=2, vibe=True, save=True,
                                   model=cfg.get("model"), provider=cfg.get("provider"),
                                   free=bool(cfg.get("free")), key=self.key)
        if run.get("status") != "done" or not run.get("saved"):
            raise RuntimeError(run.get("error") or run.get("vibe_error")
                               or "scout made no agent")
        return {"name": (run.get("draft") or {}).get("name")}


def parse_tool_json(text: str) -> Optional[Dict[str, Any]]:
    """The tool spec out of the tool-builder's answer — needs name + command."""
    text = str(text or "")
    cands = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        cands.append(text[a:b + 1])
    for raw in cands:
        try:
            out = json.loads(raw)
        except Exception:
            continue
        if isinstance(out, dict) and out.get("name") and out.get("command"):
            out["name"] = _slug(out["name"])
            params = out.get("params")
            out["params"] = params if isinstance(params, dict) else None
            out["description"] = str(out.get("description") or "")[:300]
            return out
    return None
