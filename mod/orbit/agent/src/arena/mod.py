"""
arena - agents compete on the same tasks and the board keeps score

Every agent in the registry runs the same prompts, in the same scratch dir,
under the same step budget. A match is scored off the trace and the files it
left behind — deterministic scorers, no LLM judge — so two agents are compared
on what they actually did, not on how well they described it:

    score = 0.7 correctness + 0.2 reliability + 0.1 efficiency

Correctness is the task's own checks, reliability is "no errored steps and it
actually finished", efficiency is how much of the step budget it left unspent.
Agents are then rated against each other pairwise, per task, with Elo (K split
across the field), so the board ranks them instead of listing percentages.

Tasks come from three places. The eval registry next door ships suites of them —
an eval task is an arena task, with `checks` (substrings) or `scorers` (scorer
specs) saying what passing means, and an optional `setup.files` map seeded into
the scratch dir first. The next are written by hand in the Builder and kept in
tasks.json here; they carry the address that wrote them and play in the same
rounds, under the suite name `custom`.

The third are the openarena module's, under the suite `openarena` — a statement
plus graded test cases, some of them hidden, where what is scored is the program
the agent wrote rather than the trace it left. Those are graded by openarena's
own sandbox (see openarena.py next to this file): the same task, the same
hidden cases and the same judge its own competitors face, so a rating earned
here means the same thing there.

The board ranks agents. The same match log answers a second question — which
*model* was underneath, what it scored, how long it took and what it burned —
and models.py next to this file reads it back that way. A round plays one model
across the whole field, so that board is a record of one model until somebody
runs a gauntlet: the same agent, the same tasks, one model after another, which
is the only shape that makes "this model beat that one" mean anything.

Two things run without anyone asking, both in Scheduler (a daemon thread the
API starts at boot):
    - an agent that goes online is qualified within a poll of appearing, rated
      against every incumbent's last score on the same tasks
    - a full round runs once a day, rotating through the task pool

State is private and off-tree under ~/.mod/agent/arena/: config.json, the
rating table in state.json, and every match as a line of matches.jsonl.

Usage:
    arena = Arena(runner=mod.arena_run, agents=mod.agents)
    arena.tasks()                       # the task pool
    arena.add_task({...}, owner="0x..")  # a hand-written task joins the pool
    arena.run_match("builder", "code/python#0")
    arena.run_round(reason="manual")    # everyone x the round's tasks
    arena.run_gauntlet(["a", "b"])      # one agent, one task set, N models
    arena.qualify("mynewagent")         # newcomer vs the incumbents
    arena.leaderboard()                 # ranked board
    arena.model_board()                 # the same matches, ranked by model
    arena.forward()                     # mod protocol entry point
"""
import hashlib
import json
import os
import shutil
import calendar
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from src.evals.mod import Evals
    from src.evals.scorers import run_scorer, steps_of, SCORERS
    from src.agents.mod import Agents
    from src.arena import openarena as oa
    from src.arena import drills as dr
    from src.arena import models as mb
except ImportError:  # running the arena standalone
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.evals.mod import Evals
    from src.evals.scorers import run_scorer, steps_of, SCORERS
    from src.agents.mod import Agents
    from src.arena import openarena as oa
    from src.arena import drills as dr
    from src.arena import models as mb


# scoring weights — correctness dominates, but a run that errors out or burns
# the whole budget getting there is not the same result as a clean one
W_CORRECT, W_RELIABLE, W_EFFICIENT = 0.7, 0.2, 0.1

# Elo: 1200 start, K spread over the field so one round can't swing a rating
# by more than a single head-to-head would
ELO_START = 1200.0
ELO_K = 32.0
# scores inside this band are a draw — two agents that both nailed a task
# shouldn't trade rating over floating-point dust
DRAW_MARGIN = 0.02

DEFAULTS = {
    "enabled": True,
    # zero-cost models by default: the board runs itself on a timer, so it
    # must not quietly spend the host's provider credits
    "free": True,
    # …and on a hosted catalog, because "free" and "local" are different
    # cheapnesses: a run costs the host nothing either way, but LFM weights on
    # this box are minutes per step, and a round is dozens of matches. The
    # module's own default is local (Mod.default_provider); the board says
    # otherwise on purpose. Set it to `liquidai` for a board that never leaves
    # the box, or None to follow the module.
    "provider": "openrouter",
    "model": None,           # None = the agent's own model, then provider default
    "steps": 8,
    "period_hours": 24,
    "poll_seconds": 60,
    "tasks_per_round": 3,    # rotated through the pool, season by season
    "max_matches": 40,       # hard ceiling on one round
    # a second ceiling in the unit that actually costs money: a round stops
    # once its matches have burned this many tokens. 0 = no ceiling, which is
    # the default because `free` already makes a round cost $0 — set it when
    # ranking on paid models, where the bill is real
    "max_tokens": 0,
    # a round replays only what changed: an agent with a recorded score on an
    # unchanged task keeps it, and the match is not run again. Flip this on to
    # go back to replaying the whole field every round.
    "replay": False,
    "harnesses": False,      # CLI-backed agents run the host's own shell — opt in
    "agents": None,          # None = every eligible agent
    "suites": None,          # None = every eval suite
    "retries": 1,            # a voided match is replayed this many times
    # the openarena module's tasks, graded by its sandbox (openarena.py)
    "openarena": True,       # pull them into the pool at all
    # a 500-task benchmark import is a fine thing to hold and a bad thing to
    # make every round walk through — newest N join the pool, 0 = all of them
    "openarena_tasks": 24,
    # a program task is write-then-check, so it gets more room than `steps`
    "openarena_steps": oa.DEFAULT_STEPS,
    # the arena module's drill games (drills.py), graded by its own sandbox
    "arena": True,           # pull them into the pool at all
    # how many rounds of each drill, spread across its difficulty ramp. Five
    # drills x 2 rounds is ten tasks, which is a pool and not a benchmark
    "arena_rounds": dr.DEFAULT_ROUNDS,
    "arena_seed": dr.DEFAULT_SEED,
    # one question, one answer — no file to compile, no test to run
    "arena_steps": dr.DEFAULT_STEPS,
}

# how long to wait before replaying a match the provider voided — a free model
# that just said "rate limited" will say it again immediately
RETRY_PAUSE = 20.0
# how long the board sits out after a provider says the day's quota is gone,
# when the error doesn't say when it reopens. Every match played into a
# closed door is the same void, and each one burns the free-tier quota the
# console's own free runs share
RATE_LIMIT_COOLDOWN = 3600.0

MAX_MATCH_LINES = 5000

# hand-written tasks: the suite they play under, and the ceilings that keep one
# author's task from turning every round into an expensive one
CUSTOM_SUITE = "custom"
MAX_TASK_PROMPT = 4000     # characters
MAX_TASK_FILES = 10        # fixture files seeded into the scratch dir
MAX_TASK_BYTES = 40_000    # total fixture size, characters
MAX_TASK_SCORERS = 12
MAX_TASK_STEPS = 30        # every agent on the board plays this budget
# the keys a scorer spec may carry — anything else is dropped on the way in
SCORER_FIELDS = ("type", "path", "text", "pattern", "name", "n", "case", "any")


def _now() -> float:
    return time.time()


# what a bench import/preview may be told, straight through to openarena —
# everything else in the call (key, action, address) is ours and stays here
BENCH_OPTS = ("limit", "offset", "url", "dataset", "config", "split", "style",
              "map", "hide_after", "max_cases", "timeout_ms", "language",
              "split_asserts", "tags", "slug_prefix", "refresh", "dry_run")


def _bench_opts(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items()
            if k in BENCH_OPTS and v not in (None, "")}


def _tokens_of(usage: Dict[str, Any]) -> int:
    """Total tokens a run reported, both directions. Never raises."""
    total = 0
    for field in ("prompt_tokens", "completion_tokens"):
        try:
            total += int(float((usage or {}).get(field, 0) or 0))
        except (TypeError, ValueError):
            pass
    return total


class Arena:
    description = "Agent arena - same tasks, same budget, one ranked board"

    def __init__(self, runner: Callable = None, agents: "Agents" = None,
                 evals: "Evals" = None, root: str = None):
        # runner(prompt, agent, model, steps, free, path) -> (trace, usage)
        self._runner = runner
        self.agents = agents or Agents()
        self.evals = evals or Evals()
        self.root = Path(root) if root else Path.home() / ".mod" / "agent" / "arena"
        self.work = self.root / "work"
        try:
            self.work.mkdir(parents=True, exist_ok=True)
            # a match cleans up after itself, so an hour-old scratch dir is
            # debris from a process killed mid-run. Anything younger might
            # belong to a match another process is running right now.
            for d in self.work.iterdir():
                if _now() - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
        self._lock = threading.Lock()
        self._state = self._load_state()
        self._running: Optional[Dict[str, Any]] = None   # the match in flight
        self.scheduler: Optional["Scheduler"] = None

    # ── config ─────────────────────────────────────────────────────

    @property
    def _config_path(self) -> Path:
        return self.root / "config.json"

    def config(self) -> Dict[str, Any]:
        cfg = dict(DEFAULTS)
        try:
            if self._config_path.exists():
                cfg.update(json.loads(self._config_path.read_text()))
        except Exception:
            pass
        return cfg

    def set_config(self, **kwargs) -> Dict[str, Any]:
        """Update the knobs the scheduler reads on its next tick."""
        cfg = self.config()
        for k, v in kwargs.items():
            if k in DEFAULTS and v is not None:
                cfg[k] = v
        self.root.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(cfg, indent=2))
        return cfg

    # ── state ──────────────────────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self.root / "state.json"

    def _load_state(self) -> Dict[str, Any]:
        base = {"ratings": {}, "seen": [], "last_round": 0, "season": 0, "rounds": []}
        try:
            if self._state_path.exists():
                base.update(json.loads(self._state_path.read_text()))
        except Exception:
            pass
        return base

    def _save_state(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._state, indent=2))
        except Exception:
            pass

    def _rating(self, agent: str) -> Dict[str, Any]:
        r = self._state["ratings"].get(agent)
        if r is None:
            r = {"elo": ELO_START, "matches": 0, "wins": 0, "losses": 0, "draws": 0,
                 "score_sum": 0.0, "seconds_sum": 0.0, "cost_sum": 0.0,
                 "tokens_sum": 0, "first_seen": _now(), "last": 0, "per_task": {}}
            self._state["ratings"][agent] = r
        r.setdefault("per_task", {})
        return r

    # ── subjects ───────────────────────────────────────────────────

    def subjects(self) -> List[str]:
        """The agents eligible to compete, in registry order.

        Harness agents (Claude Code, Codex) hand the run to a CLI on the host
        with its approval prompts off, so they stay out unless the host opts in.
        An agent that declares `arena = False` stays out too: the board ranks
        coding, and an agent built for another job would sit at the bottom of
        it forever, taking every incumbent's rating with it.
        """
        cfg = self.config()
        picked = cfg.get("agents")
        names = []
        for name in self.agents.ls():
            try:
                info = self.agents.get(name)
            except Exception:
                continue
            if info.get("harness") and not cfg.get("harnesses"):
                continue
            if info.get("arena") is False:
                continue
            names.append(name)
        if picked:
            names = [n for n in names if n in picked]
        return names

    def icon(self, agent: str) -> str:
        try:
            return self.agents.get(agent).get("icon", ">_")
        except Exception:
            return ">_"

    def newcomers(self) -> List[str]:
        """Eligible agents the board has never seen before."""
        seen = set(self._state.get("seen", []))
        return [a for a in self.subjects() if a not in seen]

    def _mark_seen(self, agent: str) -> None:
        seen = self._state.setdefault("seen", [])
        if agent not in seen:
            seen.append(agent)
            self._save_state()

    # ── tasks ──────────────────────────────────────────────────────

    def _normalize(self, suite: str, index: int, task: Dict[str, Any]) -> Dict[str, Any]:
        """One eval task as an arena task.

        `scorers` (scorer specs) wins if the task carries it; otherwise the
        eval's `checks` substrings become `contains` scorers, so every eval
        already written scores here unchanged.
        """
        scorers = task.get("scorers")
        if not scorers:
            scorers = [{"type": "contains", "text": c} for c in task.get("checks", [])]
        prompt = task.get("prompt", "")
        title = task.get("title") or " ".join(prompt.split())[:72]
        return {
            "key": f"{suite}#{index}",
            "suite": suite,
            "index": index,
            "title": title,
            "prompt": prompt,
            "scorers": scorers,
            "setup": task.get("setup") or {},
            "steps": int(task.get("steps") or 0) or None,
        }

    def tasks(self) -> List[Dict[str, Any]]:
        """The whole task pool: every eval suite, plus the hand-written ones."""
        cfg = self.config()
        want = cfg.get("suites")
        out = []
        for suite in self.evals.ls():
            if want and suite not in want:
                continue
            try:
                spec = self.evals.get(suite)
            except Exception:
                continue
            for i, task in enumerate(spec.get("tasks", [])):
                out.append(self._normalize(suite, i, task))
        if not want or CUSTOM_SUITE in want:
            out.extend(self.custom_tasks())
        if (not want or oa.SUITE in want) and cfg.get("openarena", True):
            out.extend(self.openarena_tasks())
        if (not want or dr.SUITE in want) and cfg.get("arena", True):
            out.extend(self.arena_tasks())
        return out

    # ── arena drills (the other arena, graded by its own sandbox) ──

    def arena_tasks(self) -> List[Dict[str, Any]]:
        """The arena module's drills as arena tasks. Never raises: a neighbour
        that is down means no drill tasks this round, not a round that fell
        over."""
        cfg = self.config()
        return dr.pool(rounds=int(cfg.get("arena_rounds", dr.DEFAULT_ROUNDS) or 1),
                       seed=int(cfg.get("arena_seed", dr.DEFAULT_SEED) or 0),
                       steps=int(cfg.get("arena_steps", dr.DEFAULT_STEPS) or 0) or None)

    def arena_status(self) -> Dict[str, Any]:
        """Whether the arena is up and which drills it holds. Kept out of
        status() for the same reason openarena_status is — a dead neighbour
        must not add its timeout to every poll of this board."""
        cfg = self.config()
        out = dict(dr.status())
        out["enabled"] = bool(cfg.get("arena", True))
        out["rounds"] = int(cfg.get("arena_rounds", dr.DEFAULT_ROUNDS) or 1)
        out["seed"] = int(cfg.get("arena_seed", dr.DEFAULT_SEED) or 0)
        out["steps"] = int(cfg.get("arena_steps", dr.DEFAULT_STEPS) or 0)
        return out

    # ── openarena tasks (the module next door, graded by its sandbox) ──

    def openarena_tasks(self) -> List[Dict[str, Any]]:
        """openarena's tasks as arena tasks. Never raises: a neighbour that is
        down means no openarena tasks this round, not a round that fell over."""
        cfg = self.config()
        return oa.pool(limit=int(cfg.get("openarena_tasks", 24) or 0),
                       steps=int(cfg.get("openarena_steps", oa.DEFAULT_STEPS) or 0)
                       or None)

    def openarena_status(self) -> Dict[str, Any]:
        """Whether the neighbour is up and what it holds — one call, for the
        console. Kept out of status() so a module that is down cannot slow the
        board's own polling down to its timeout."""
        cfg = self.config()
        out = dict(oa.status())
        out["enabled"] = bool(cfg.get("openarena", True))
        out["limit"] = int(cfg.get("openarena_tasks", 24) or 0)
        out["steps"] = int(cfg.get("openarena_steps", oa.DEFAULT_STEPS) or 0)
        if out.get("available"):
            try:
                out["entrants"] = oa.entrants()
            except Exception:
                out["entrants"] = []
            out["pool"] = [{**t["openarena"], "key": t["key"], "title": t["title"],
                            "steps": t["steps"]}
                           for t in self.openarena_tasks()]
        else:
            out["entrants"], out["pool"] = [], []
        return out

    # ── hand-written tasks (the Builder's task mode) ───────────────
    #
    # A suite is a python file in the tree, which is not something a signed-in
    # visitor gets to write. These live in a JSON file instead: same shape once
    # normalized, same rounds, same scoring — but owned, editable and
    # removable by whoever wrote them.

    @property
    def _tasks_path(self) -> Path:
        return self.root / "tasks.json"

    def _load_tasks(self) -> Dict[str, Any]:
        try:
            if self._tasks_path.exists():
                data = json.loads(self._tasks_path.read_text())
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_tasks(self, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._tasks_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._tasks_path)

    def custom(self) -> List[Dict[str, Any]]:
        """The stored specs, newest first — what an editor loads."""
        return sorted(self._load_tasks().values(),
                      key=lambda t: -float(t.get("updated") or 0))

    def custom_tasks(self) -> List[Dict[str, Any]]:
        """The stored specs as arena tasks, ready to play."""
        out = []
        for spec in self.custom():
            task = self._normalize(CUSTOM_SUITE, spec["slug"], spec)
            task["owner"] = spec.get("owner")
            task["description"] = spec.get("description", "")
            task["custom"] = True
            out.append(task)
        return out

    @staticmethod
    def fingerprint(task: Dict[str, Any]) -> str:
        """What makes a task "the same task" from one round to the next: the
        prompt, the checks, the fixture and the step budget. The title is
        cosmetic and stays out; so does the suite, which is where it lives
        rather than what it asks."""
        basis = {"prompt": task.get("prompt") or "",
                 "scorers": task.get("scorers") or [],
                 "setup": task.get("setup") or {},
                 "steps": task.get("steps") or 0}
        return hashlib.sha1(json.dumps(basis, sort_keys=True,
                                       default=str).encode()).hexdigest()[:16]

    @staticmethod
    def slugify(text: str) -> str:
        out = "".join(c if c.isalnum() else "-" for c in str(text).lower())
        while "--" in out:
            out = out.replace("--", "-")
        return out.strip("-")[:48]

    def validate_task(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Check a hand-written task and return it in stored form.

        Everything here is a limit on what one task may cost the host to run:
        a task is played by every agent on the board, so an unbounded fixture
        or a 200-step budget is not the author's cost, it's the module's.
        """
        title = str(spec.get("title") or "").strip()
        prompt = str(spec.get("prompt") or "").strip()
        if not title:
            raise ValueError("a task needs a title")
        if len(prompt) < 12:
            raise ValueError("a task needs a prompt — describe what the agent must do")
        if len(prompt) > MAX_TASK_PROMPT:
            raise ValueError(f"prompt is too long (max {MAX_TASK_PROMPT} characters)")

        files = spec.get("setup", {}).get("files") if isinstance(spec.get("setup"), dict) else None
        files = files or {}
        if not isinstance(files, dict):
            raise ValueError("setup.files must be a map of filename -> contents")
        if len(files) > MAX_TASK_FILES:
            raise ValueError(f"too many fixture files (max {MAX_TASK_FILES})")
        clean_files = {}
        for name, body in files.items():
            name = str(name).strip().lstrip("/")
            if not name:
                continue
            if ".." in name or name.startswith("~"):
                raise ValueError(f"fixture path escapes the scratch dir: {name}")
            clean_files[name] = str(body)
        if sum(len(v) for v in clean_files.values()) > MAX_TASK_BYTES:
            raise ValueError(f"fixture files are too big (max {MAX_TASK_BYTES} characters total)")

        scorers = spec.get("scorers") or []
        if not isinstance(scorers, list) or not scorers:
            raise ValueError("a task needs at least one check — otherwise every "
                             "agent scores the same and the match says nothing")
        if len(scorers) > MAX_TASK_SCORERS:
            raise ValueError(f"too many checks (max {MAX_TASK_SCORERS})")
        clean_scorers = []
        for s in scorers:
            if not isinstance(s, dict):
                raise ValueError("each check must be an object with a `type`")
            kind = str(s.get("type") or "").strip()
            if kind not in SCORERS:
                raise ValueError(f"unknown check type: {kind or '(none)'} — "
                                 f"pick one of {', '.join(sorted(SCORERS))}")
            clean = {k: v for k, v in s.items() if k in SCORER_FIELDS and v not in (None, "")}
            clean["type"] = kind
            if kind.startswith("file_") and not clean.get("path"):
                raise ValueError(f"{kind} needs a `path`")
            if kind in ("contains", "regex", "file_contains", "file_not_contains",
                        "file_regex") and not (clean.get("text") or clean.get("pattern")):
                raise ValueError(f"{kind} needs `text` or `pattern`")
            if str(clean.get("path", "")).startswith("/"):
                raise ValueError("check paths are relative to the scratch dir")
            clean_scorers.append(clean)

        steps = int(spec.get("steps") or 0) or None
        if steps is not None:
            steps = max(1, min(steps, MAX_TASK_STEPS))
        return {
            "title": title[:120],
            "description": str(spec.get("description") or "").strip()[:280],
            "prompt": prompt,
            "steps": steps,
            "setup": {"files": clean_files} if clean_files else {},
            "scorers": clean_scorers,
        }

    def add_task(self, spec: Dict[str, Any], owner: str = None,
                 slug: str = None) -> Dict[str, Any]:
        """Store a hand-written task. Editing one keeps its key, so the
        ratings already recorded against it stay attached."""
        clean = self.validate_task(spec)
        store = self._load_tasks()
        # naming the slug is what makes this an edit — a new task that happens
        # to share a title is a different task, not a replacement for the one
        # already there (whose author may not even be this caller)
        requested = slug or spec.get("slug")
        slug = self.slugify(requested or clean["title"])
        if not slug:
            raise ValueError("could not make a key out of that title")
        existing = store.get(slug) if requested else None
        if existing is None:
            # a new task with a taken name gets a suffix rather than silently
            # overwriting somebody else's
            base, n = slug, 2
            while slug in store:
                slug, n = f"{base}-{n}", n + 1
        clean.update({
            "slug": slug,
            "owner": (existing or {}).get("owner") if existing else (owner or None),
            "created": (existing or {}).get("created") or _now(),
            "updated": _now(),
        })
        store[slug] = clean
        self._save_tasks(store)
        return {**self._normalize(CUSTOM_SUITE, slug, clean),
                "owner": clean["owner"], "custom": True,
                "description": clean["description"]}

    def get_custom(self, slug: str) -> Optional[Dict[str, Any]]:
        return self._load_tasks().get(self.slugify(slug))

    def remove_task(self, slug: str) -> Dict[str, Any]:
        slug = self.slugify(slug)
        store = self._load_tasks()
        if slug not in store:
            raise KeyError(f"no such task: {slug}")
        store.pop(slug)
        self._save_tasks(store)
        # the ratings keep the task's history: a task can come back, and a
        # record of a match that was really played is not ours to rewrite
        return {"removed": f"{CUSTOM_SUITE}#{slug}", "remaining": len(store)}

    def task(self, key: str) -> Dict[str, Any]:
        for t in self.tasks():
            if t["key"] == key:
                return t
        # an openarena task is playable by name even when the pool is capped
        # below it: naming one is asking for it, not browsing
        if str(key).startswith(f"{oa.SUITE}#"):
            cfg = self.config()
            slug = str(key).split("#", 1)[1]
            return oa.as_arena_task(
                oa.get_task(slug),
                steps=int(cfg.get("openarena_steps", oa.DEFAULT_STEPS) or 0) or None)
        # …and the same for a drill round the pool did not happen to include
        if str(key).startswith(f"{dr.SUITE}#"):
            cfg = self.config()
            found = dr.task(str(key),
                            seed=int(cfg.get("arena_seed", dr.DEFAULT_SEED) or 0),
                            steps=int(cfg.get("arena_steps", dr.DEFAULT_STEPS) or 0) or None)
            if found:
                return found
        raise KeyError(f"task not found: {key}")

    def round_tasks(self, n: int = None) -> List[Dict[str, Any]]:
        """The slice of the pool this season plays.

        A full pool x a full field is dozens of LLM runs, so a round takes a
        rotating window instead — every task comes around, nothing runs the
        whole catalogue at 3am.
        """
        pool = self.tasks()
        if not pool:
            return []
        n = n or int(self.config().get("tasks_per_round", 3))
        n = max(1, min(n, len(pool)))
        start = (int(self._state.get("season", 0)) * n) % len(pool)
        return [pool[(start + i) % len(pool)] for i in range(n)]

    def qualifier_tasks(self, agent: str, n: int = None) -> List[Dict[str, Any]]:
        """The tasks a newcomer is measured on: whatever the field has played.

        Rating a newcomer needs somebody to rate them against, so the qualifier
        goes where the records already are — the tasks the most incumbents have
        a score on — and only falls back to this season's rotation on an empty
        board.
        """
        n = n or int(self.config().get("tasks_per_round", 3))
        counts: Dict[str, int] = {}
        for other, r in self._state.get("ratings", {}).items():
            if other == agent:
                continue
            for key in (r.get("per_task") or {}):
                counts[key] = counts.get(key, 0) + 1
        pool = {t["key"]: t for t in self.tasks()}
        ranked = [pool[k] for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                  if k in pool]
        return ranked[:n] or self.round_tasks(n)

    # ── scoring ────────────────────────────────────────────────────

    def _resolve_spec(self, spec: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
        """Point a file scorer at the match's scratch dir."""
        if "path" in spec and not os.path.isabs(str(spec["path"])):
            return dict(spec, path=str(workdir / str(spec["path"])))
        return spec

    def score(self, trace, task: Dict[str, Any], workdir: Path,
              limit: int = None) -> Dict[str, Any]:
        """Score one trace against a task. No model, no judge with an opinion.

        Correctness is the mean of what the checks reported, and a check reports
        a fraction rather than a verdict when it has one to give: every scorer
        over the trace and the disk is pass/fail and scores 1 or 0, while an
        openarena task is graded case by case, so 7 of 10 cases is 0.7 and not
        a zero. A near-miss should not rank with a blank page.
        """
        workdir = Path(workdir)
        checks = [run_scorer(self._resolve_spec(s, workdir), trace)
                  for s in task.get("scorers", [])]
        correct = (sum(float(c.get("score", float(bool(c.get("passed")))))
                       for c in checks) / len(checks)) if checks else 0.0

        clean = run_scorer({"type": "no_errors"}, trace)
        done = run_scorer({"type": "finished"}, trace)
        reliable = (float(bool(clean["passed"])) + float(bool(done["passed"]))) / 2

        limit = limit or task.get("steps") or int(self.config().get("steps", 8))
        used = len(steps_of(trace))
        # unspent budget, but only if it actually finished — stopping early by
        # falling over is not efficiency
        efficient = max(0.0, 1 - used / max(1, limit)) if done["passed"] else 0.0

        total = W_CORRECT * correct + W_RELIABLE * reliable + W_EFFICIENT * efficient
        # a check that could not run at all — openarena's judge unreachable —
        # is the same kind of nothing as a provider outage: whatever the agent
        # did, this match did not measure it
        unscoreable = next((str(c.get("reason"))[:200] for c in checks
                            if c.get("void")), None)
        return {
            "score": round(total, 4),
            # the loop emits a bare `error` step only when the model call itself
            # failed — a rate-limited free endpoint is not an agent that can't
            # code, so the match is void rather than lost. A tool that errored
            # is a real step and still counts against reliability.
            "void_reason": next((str(s.get("error"))[:200] for s in steps_of(trace)
                                 if s.get("tool") == "error"), None) or unscoreable,
            "correct": round(correct, 4),
            "reliable": round(reliable, 4),
            "efficient": round(efficient, 4),
            "passed": bool(checks) and all(c.get("passed") for c in checks),
            "checks": checks + [clean, done],
            "steps": used,
        }

    # ── matches ────────────────────────────────────────────────────

    @property
    def _matches_path(self) -> Path:
        return self.root / "matches.jsonl"

    def _append_match(self, match: Dict[str, Any]) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self._matches_path, "a") as f:
                f.write(json.dumps(match, default=str) + "\n")
            self._prune_matches()
        except Exception:
            pass

    def _prune_matches(self) -> None:
        try:
            if not self._matches_path.exists():
                return
            lines = self._matches_path.read_text().splitlines()
            if len(lines) > MAX_MATCH_LINES * 1.2:
                self._matches_path.write_text("\n".join(lines[-MAX_MATCH_LINES:]) + "\n")
        except Exception:
            pass

    def matches(self, limit: int = 50, agent: str = None,
                task: str = None) -> List[Dict[str, Any]]:
        """Recent matches, newest first."""
        out = []
        try:
            if self._matches_path.exists():
                for line in self._matches_path.read_text().splitlines():
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if agent and rec.get("agent") != agent:
                        continue
                    if task and rec.get("task") != task and rec.get("suite") != task:
                        continue
                    out.append(rec)
        except Exception:
            pass
        out.reverse()
        return out[:max(1, int(limit))]

    def _workdir(self, match_id: str, task: Dict[str, Any]) -> Path:
        """A scratch dir per match, seeded with the task's fixture files."""
        d = self.work / match_id
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            for rel, body in (task.get("setup", {}).get("files") or {}).items():
                p = d / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body)
        except Exception:
            pass
        return d

    def run_match(self, agent: str, task: str, model: str = None, steps: int = None,
                  free: bool = None, reason: str = "manual", provider: str = None,
                  rate: bool = True) -> Dict[str, Any]:
        """Run one agent on one task and score it. Records the match.

        A match the provider voided (rate limit, endpoint down, no key) is
        replayed up to `retries` times. If it is still void, it is recorded as
        void: kept on the log so the failure is visible, kept out of the rating
        so a free endpoint's bad afternoon doesn't read as an agent regression.

        `rate=False` keeps a real match off the agent's record. A gauntlet is
        one agent playing the same task on six models: those scores say
        something about the models and nothing about the agent, and folding
        them in would move its averages — and, worse, overwrite the per-task
        score a newcomer's qualifier is rated against — on the strength of
        whichever model happened to go last.
        """
        spec = self.task(task) if isinstance(task, str) else task
        cfg = self.config()
        attempts = max(1, int(cfg.get("retries", 1)) + 1)
        for attempt in range(attempts):
            match = self._play(agent, spec, model=model, steps=steps, free=free,
                               reason=reason, attempt=attempt, provider=provider)
            # a closed door is not a flaky one — replaying a rate limit only
            # spends more of the quota it just ran out of
            if not match["void"] or attempt + 1 >= attempts \
                    or self._rate_limited(match.get("void_reason")):
                break
            time.sleep(RETRY_PAUSE)
        self._append_match(match)
        if not match["void"]:
            if rate:
                self._record(agent, match)
            else:
                self._mark_seen(agent)
        else:
            self._mark_seen(agent)   # it showed up, even if it couldn't play
            self._rating(agent)["voids"] = self._rating(agent).get("voids", 0) + 1
            self._save_state()
        return match

    def _play(self, agent: str, spec: Dict[str, Any], model: str = None,
              steps: int = None, free: bool = None, reason: str = "manual",
              attempt: int = 0, provider: str = None) -> Dict[str, Any]:
        """One attempt at a match: fresh scratch dir, run, score, clean up."""
        cfg = self.config()
        model = model if model is not None else cfg.get("model")
        free = cfg.get("free", True) if free is None else free
        provider = provider or cfg.get("provider")
        limit = int(steps or spec.get("steps") or cfg.get("steps", 8))

        mid = uuid.uuid4().hex[:12]
        workdir = self._workdir(mid, spec)
        self._running = {"match": mid, "agent": agent, "task": spec["key"],
                         "started_at": _now(), "reason": reason}

        trace, usage, error = [], {}, None
        t0 = _now()
        try:
            if self._runner is None:
                raise RuntimeError("no runner configured — the arena cannot dispatch runs")
            # {workdir} is the scratch dir, spelled out absolutely: tools resolve
            # relative paths against the API's own cwd, so a task that says
            # "write notes.txt" would land somewhere nobody scores
            prompt = spec["prompt"].replace("{workdir}", str(workdir))
            # a model id only means something on the catalog it came from, so a
            # gauntlet names the provider too. Passed only when there is one:
            # the runner's signature predates it
            extra = {"provider": provider} if provider else {}
            trace, usage = self._runner(prompt=prompt, agent=agent, model=model,
                                        steps=limit, free=free, path=str(workdir),
                                        **extra)
        except Exception as e:
            error = str(e)
        seconds = round(_now() - t0, 2)

        scored = self.score(trace, spec, workdir, limit=limit)
        match = {
            "id": mid,
            "ts": _now(),
            "season": self._state.get("season", 0),
            "reason": reason,
            "agent": agent,
            "task": spec["key"],
            "suite": spec["suite"],
            "title": spec["title"],
            "fp": self.fingerprint(spec),
            # what actually ran, not what was asked for: FREE MODE resolves its
            # own model, and the board is only worth reading if the id on the
            # row is the id that answered
            "model": (usage or {}).get("model") or model,
            "provider": (usage or {}).get("provider") or provider,
            "seconds": seconds,
            "cost": round(float((usage or {}).get("cost", 0.0) or 0.0), 6),
            # what the match actually burned. On free models cost is 0, so
            # tokens are the only honest measure of what the board costs to run
            "tokens": _tokens_of(usage),
            "error": error,
            "budget": limit,
            "attempt": attempt,
            **{k: scored[k] for k in ("score", "correct", "reliable", "efficient",
                                      "passed", "steps")},
            # the check log carries a fraction only when the check had one, and
            # the case list only when a judge produced one — a match record is
            # read by humans and kept on one line
            "checks": [{k: v for k, v in
                        (("type", c.get("type")), ("passed", c.get("passed")),
                         ("reason", c.get("reason")),
                         ("score", round(float(c["score"]), 4)
                          if c.get("score") not in (None, 1.0, 0.0) else None),
                         ("cases", c.get("cases")))
                        if v is not None}
                       for c in scored["checks"]],
        }
        # a run that never reached the model competed in name only
        match["void_reason"] = error or scored["void_reason"]
        match["void"] = bool(match["void_reason"])
        if match["void"]:
            match["score"] = 0.0
        self._running = None
        # the scratch dir has done its job once the scorers have read it
        shutil.rmtree(workdir, ignore_errors=True)
        return match

    def _record(self, agent: str, match: Dict[str, Any]) -> None:
        r = self._rating(agent)
        r["matches"] += 1
        r["score_sum"] = round(r.get("score_sum", 0.0) + match["score"], 4)
        r["seconds_sum"] = round(r.get("seconds_sum", 0.0) + match["seconds"], 2)
        r["cost_sum"] = round(r.get("cost_sum", 0.0) + match.get("cost", 0.0), 6)
        r["tokens_sum"] = int(r.get("tokens_sum", 0)) + int(match.get("tokens", 0))
        r["last"] = match["ts"]
        per = r["per_task"].setdefault(match["task"], {"n": 0, "best": 0.0, "last": 0.0})
        per["n"] += 1
        per["last"] = match["score"]
        per["best"] = max(per.get("best", 0.0), match["score"])
        per["ts"] = match["ts"]
        # the version of the task this score was earned on — an edited task
        # invalidates the record, an unchanged one lets it stand next round
        if match.get("fp"):
            per["fp"] = match["fp"]
        self._mark_seen(agent)
        self._save_state()

    # ── rating ─────────────────────────────────────────────────────

    def _rate(self, task: str, entries: List[tuple]) -> None:
        """Pairwise Elo over everyone's score on one task.

        entries: [(agent, score), ...]. K is split across the field so a round
        with eight agents moves ratings by the same order as a single duel.
        """
        entries = [(a, s) for a, s in entries if a]
        if len(entries) < 2:
            return
        k = ELO_K / (len(entries) - 1)
        deltas = {a: 0.0 for a, _ in entries}
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, sa = entries[i]
                b, sb = entries[j]
                ra, rb = self._rating(a)["elo"], self._rating(b)["elo"]
                expected = 1 / (1 + 10 ** ((rb - ra) / 400))
                if sa > sb + DRAW_MARGIN:
                    outcome = 1.0
                elif sb > sa + DRAW_MARGIN:
                    outcome = 0.0
                else:
                    outcome = 0.5
                d = k * (outcome - expected)
                deltas[a] += d
                deltas[b] -= d
                self._tally(a, outcome)
                self._tally(b, 1 - outcome)
        for a, d in deltas.items():
            self._rating(a)["elo"] = round(self._rating(a)["elo"] + d, 1)
        self._save_state()

    def _tally(self, agent: str, outcome: float) -> None:
        r = self._rating(agent)
        if outcome > 0.5:
            r["wins"] += 1
        elif outcome < 0.5:
            r["losses"] += 1
        else:
            r["draws"] += 1

    # ── rounds ─────────────────────────────────────────────────────

    def run_round(self, agents: List[str] = None, tasks: List[str] = None,
                  reason: str = "manual", force: bool = False) -> Dict[str, Any]:
        """Everyone is rated on every task in the round — but only the matches
        that can say something new actually run.

        An agent with a recorded score on a task that has not changed since
        keeps that score: replaying it burns quota to learn what the board
        already knows. What does run is anything the record can't answer —
        a new agent, an edited task (the fingerprint moved), a pair whose last
        attempt was voided — and it is rated against the standing scores of
        everyone who sat out, the same way a qualifier is. A round where
        nothing changed plays zero matches and costs nothing.

        `force=True` (or the `replay` config knob) plays the whole field
        regardless — the old behaviour, and the right one after a scorer bug.

        Serialized: one round at a time, and matches inside it run one after
        the other. The point is a fair comparison, and eight agents hammering
        one provider key in parallel is not one.
        """
        if not self._lock.acquire(blocking=False):
            return {"error": "a round is already running", "running": self._running}
        try:
            field = agents or self.subjects()
            pool = ([self.task(t) for t in tasks] if tasks else self.round_tasks())
            cfg = self.config()
            force = bool(force) or bool(cfg.get("replay"))
            budget = int(cfg.get("max_matches", 40))
            token_cap = int(cfg.get("max_tokens", 0) or 0)
            fps = self._state.setdefault("task_fps", {})
            played, results, tokens, capped_by, skipped = [], {}, 0, None, 0
            standing: Dict[str, List[tuple]] = {}
            for spec in pool:
                if capped_by:
                    break
                key = spec["key"]
                fp = self.fingerprint(spec)
                # a fingerprint on file that no longer matches means the task
                # was edited — every record on it is about a different task now
                changed = key in fps and fps[key] != fp
                # an eval can name the subjects it applies to
                allowed = self._suite_agents(spec)
                for agent in field:
                    if allowed and agent not in allowed:
                        continue
                    prev = ((self._state.get("ratings", {}).get(agent) or {})
                            .get("per_task") or {}).get(key)
                    # a record from before fingerprints (no fp) counts as
                    # current — assuming unchanged beats replaying everything
                    # once to find out
                    if not force and not changed and prev \
                            and prev.get("fp") in (None, fp):
                        skipped += 1
                        standing.setdefault(key, []).append(
                            (agent, float(prev.get("last", 0.0))))
                        continue
                    if len(played) >= budget:
                        capped_by = "max_matches"
                        break
                    # checked before the match, not after: the cap is there to
                    # stop the spend, and a match already run is already paid for
                    if token_cap and tokens >= token_cap:
                        capped_by = "max_tokens"
                        break
                    match = self.run_match(agent, spec, reason=reason)
                    played.append(match)
                    tokens += int(match.get("tokens", 0))
                    if not match["void"]:
                        results.setdefault(key, []).append((agent, match["score"]))
                    elif self._rate_limited(match.get("void_reason")):
                        # the provider has closed the door for the day: every
                        # further match is the same void, and each one burns
                        # the quota the console's own free runs share
                        self._cooldown(match.get("void_reason"))
                        capped_by = "rate_limited"
                        break
                # stamped only when every agent got their turn — a round cut
                # off mid-task must not certify the new fingerprint, or the
                # agents it cut off would be skipped against a task they
                # never played
                if not capped_by:
                    fps[key] = fp
            for key, entries in results.items():
                # whoever sat the task out is rated on the score that let them
                # sit it out — a fresh match still meets the whole field
                self._rate(key, entries + standing.get(key, []))

            self._state["season"] = int(self._state.get("season", 0)) + 1
            self._state["last_round"] = _now()
            summary = {
                "reason": reason,
                "ts": self._state["last_round"],
                "season": self._state["season"],
                "agents": field,
                "tasks": [t["key"] for t in pool],
                "matches": len(played),
                # pairs whose standing score was kept instead of replayed — a
                # zero-match round with a big skip count is the system working,
                # not the system broken
                "skipped": skipped,
                "forced": force,
                "tokens": tokens,
                "capped": len(played) >= budget,
                # which ceiling ended the round early, if either did — a short
                # round should say why rather than read as "everyone played"
                "capped_by": capped_by,
            }
            rounds = self._state.setdefault("rounds", [])
            rounds.append(summary)
            self._state["rounds"] = rounds[-50:]
            self._save_state()
            return {**summary, "results": played}
        except Exception as e:
            # a round that fell over still ran: left unstamped, the scheduler
            # saw it as due again on the next tick and replayed its first
            # matches every minute — seven hundred a day, on the free tier
            self._state["last_round"] = _now()
            rounds = self._state.setdefault("rounds", [])
            rounds.append({"reason": reason, "ts": self._state["last_round"],
                           "season": self._state.get("season", 0),
                           "matches": 0, "error": str(e)[:200]})
            self._state["rounds"] = rounds[-50:]
            self._save_state()
            raise
        finally:
            self._lock.release()

    def run_gauntlet(self, models: List[Any], agent: str = None,
                     tasks: List[str] = None, steps: int = None,
                     free: bool = False, reason: str = "gauntlet") -> Dict[str, Any]:
        """One agent, one set of tasks, every model in turn.

        This is the round the model board needs. A daily round plays the whole
        field on a single model, which produces no comparison between models at
        all; here the agent, the task, the fixture and the step budget are all
        held still and the model is the only thing that moves — so the scores
        can be rated against each other (see models.py).

        `models` takes ids, or {model, provider} for a catalog that isn't the
        module's default. FREE MODE is off by default and cannot be on for a
        named model: it resolves its own zero-cost pick and would run six
        entries of the same thing.
        """
        if not self._lock.acquire(blocking=False):
            return {"error": "a round is already running", "running": self._running}
        try:
            entries = []
            for spec in models or []:
                if isinstance(spec, str):
                    entries.append({"model": spec, "provider": None})
                elif isinstance(spec, dict) and spec.get("model"):
                    entries.append({"model": str(spec["model"]),
                                    "provider": spec.get("provider") or None})
            if len(entries) < 2:
                return {"error": "a gauntlet needs at least two models — one "
                                 "model playing alone is a round"}
            field = self.subjects()
            agent = agent or (field[0] if field else None)
            if not agent:
                return {"error": "no agent to play the gauntlet"}
            pool = [self.task(t) for t in tasks] if tasks else self.round_tasks()
            if not pool:
                return {"error": "no tasks to play"}

            cfg = self.config()
            budget = int(cfg.get("max_matches", 40))
            token_cap = int(cfg.get("max_tokens", 0) or 0)
            played, tokens, capped_by = [], 0, None
            season = int(self._state.get("season", 0))
            for spec in pool:
                if capped_by:
                    break
                for entry in entries:
                    if len(played) >= budget:
                        capped_by = "max_matches"
                        break
                    if token_cap and tokens >= token_cap:
                        capped_by = "max_tokens"
                        break
                    match = self.run_match(agent, spec, model=entry["model"],
                                           provider=entry["provider"], steps=steps,
                                           free=free, reason=reason,
                                           # the agent is the constant here, not
                                           # the subject — see run_match(rate=)
                                           rate=False)
                    played.append(match)
                    tokens += int(match.get("tokens", 0))
            summary = {
                "reason": reason,
                "ts": _now(),
                "season": season,
                "agent": agent,
                "models": [e["model"] for e in entries],
                "tasks": [t["key"] for t in pool],
                "matches": len(played),
                "tokens": tokens,
                "cost": round(sum(float(m.get("cost") or 0.0) for m in played), 6),
                "capped_by": capped_by,
                # a gauntlet doesn't advance the season: the models it just ran
                # are compared inside this one, and bumping it would put every
                # future match in a bucket of its own
                "results": played,
            }
            return summary
        finally:
            self._lock.release()

    def qualify(self, agent: str, reason: str = None) -> Dict[str, Any]:
        """Score a newcomer against the incumbents without re-running them.

        The new agent plays the round's tasks; each incumbent's last recorded
        score on that same task stands in as their side of the match. It is a
        real comparison — same prompt, same budget, same scorers — and it means
        an agent that comes online at noon is on the board by 12:01 instead of
        waiting for the nightly round.
        """
        if not self._lock.acquire(blocking=False):
            return {"error": "a round is already running", "running": self._running}
        try:
            pool = self.qualifier_tasks(agent)
            token_cap = int(self.config().get("max_tokens", 0) or 0)
            played, tokens = [], 0
            for spec in pool:
                allowed = self._suite_agents(spec)
                if allowed and agent not in allowed:
                    continue
                # one newcomer can arrive at any hour, so the qualifier answers
                # to the same ceiling a scheduled round does
                if token_cap and tokens >= token_cap:
                    break
                match = self.run_match(agent, spec, reason=reason or f"qualifier:{agent}")
                played.append(match)
                tokens += int(match.get("tokens", 0))
                if match["void"]:
                    continue
                entries = [(agent, match["score"])]
                for other, r in self._state["ratings"].items():
                    if other == agent:
                        continue
                    prev = (r.get("per_task") or {}).get(spec["key"])
                    if prev:
                        entries.append((other, prev.get("last", 0.0)))
                self._rate(spec["key"], entries)
            self._mark_seen(agent)
            return {"agent": agent, "qualified": True, "matches": len(played),
                    "tokens": tokens, "tasks": [t["key"] for t in pool],
                    "results": played, "elo": self._rating(agent)["elo"]}
        finally:
            self._lock.release()

    # ── board ──────────────────────────────────────────────────────

    def leaderboard(self) -> List[Dict[str, Any]]:
        """Every rated agent, ranked by Elo."""
        active = set(self.subjects())
        rows = []
        for agent, r in self._state.get("ratings", {}).items():
            n = max(1, r.get("matches", 0))
            games = r.get("wins", 0) + r.get("losses", 0) + r.get("draws", 0)
            rows.append({
                "agent": agent,
                "icon": self.icon(agent),
                "active": agent in active,
                "elo": round(r.get("elo", ELO_START), 1),
                "matches": r.get("matches", 0),
                "wins": r.get("wins", 0),
                "losses": r.get("losses", 0),
                "draws": r.get("draws", 0),
                "win_rate": round((r.get("wins", 0) + 0.5 * r.get("draws", 0)) / games, 3) if games else 0.0,
                "avg_score": round(r.get("score_sum", 0.0) / n, 4),
                "avg_seconds": round(r.get("seconds_sum", 0.0) / n, 2),
                "cost": round(r.get("cost_sum", 0.0), 6),
                # on free models cost stays 0 — tokens are what a rank cost
                "tokens": int(r.get("tokens_sum", 0)),
                "avg_tokens": int(r.get("tokens_sum", 0) / n),
                "tasks": len(r.get("per_task", {})),
                # matches the provider voided — not losses, but worth seeing:
                # a field full of them means the free endpoint is the problem
                "voids": r.get("voids", 0),
                "last": r.get("last", 0),
            })
        rows.sort(key=lambda x: (-x["elo"], -x["avg_score"], x["agent"]))
        for i, row in enumerate(rows, 1):
            row["rank"] = i
        return rows

    # ── the same matches, read by model ────────────────────────────
    #
    # Nothing below runs anything or keeps any state of its own: the match log
    # already carries the model, the wall clock, the tokens and the bill, so
    # these are views over it (models.py). That also means they are honest for
    # free — a model's rank is recomputed from the matches that exist right
    # now, and a match that was pruned off the log stops counting.

    def all_matches(self) -> List[Dict[str, Any]]:
        """Every match still on the log, newest first."""
        return self.matches(limit=MAX_MATCH_LINES * 2)

    def model_board(self, min_matches: int = 1) -> List[Dict[str, Any]]:
        return mb.board(self.all_matches(), min_matches=min_matches)

    def model_card(self, model: str) -> Dict[str, Any]:
        titles = {t["key"]: t["title"] for t in self.tasks()}
        return mb.card(self.all_matches(), model, titles=titles)

    def task_leaders(self) -> Dict[str, Dict[str, Any]]:
        """Per task, every agent's standing record on it — best last score
        first, so [0] is the task's leader.

        Read off the rating table rather than the match log: per_task is only
        written by rated matches, so a gauntlet's model runs never crown an
        agent, and a record outlives the log being pruned.
        """
        per: Dict[str, List[Dict[str, Any]]] = {}
        for agent, r in self._state.get("ratings", {}).items():
            for key, rec in (r.get("per_task") or {}).items():
                per.setdefault(key, []).append({
                    "agent": agent,
                    "icon": self.icon(agent),
                    "n": int(rec.get("n", 0)),
                    "best": round(float(rec.get("best", 0.0)), 4),
                    "last": round(float(rec.get("last", 0.0)), 4),
                    "ts": rec.get("ts", 0),
                })
        out = {}
        for key, rows in per.items():
            rows.sort(key=lambda x: (-x["last"], -x["best"], x["agent"]))
            out[key] = {
                "agents": rows,
                "leader": rows[0]["agent"],
                "leader_icon": rows[0]["icon"],
                "leader_score": rows[0]["last"],
                # best agent minus worst — the agent-side twin of `spread`
                "agent_spread": round(rows[0]["last"] - rows[-1]["last"], 4)
                if len(rows) > 1 else 0.0,
            }
        return out

    def task_board(self) -> List[Dict[str, Any]]:
        """Every task, with the agents that lead it and the models that played
        it. Three sources merged by key: the model ranking off the match log,
        the agent ranking off the rating table, and the pool itself — so the
        board answers "what are the tasks" and not only "what has run"."""
        pool = self.tasks()
        titles = {t["key"]: t["title"] for t in pool}
        rows = mb.task_board(self.all_matches(), titles=titles)
        leaders = self.task_leaders()
        none = {"agents": [], "leader": None, "leader_icon": None,
                "leader_score": 0.0, "agent_spread": 0.0}
        seen = set()
        for row in rows:
            seen.add(row["task"])
            row.update(leaders.get(row["task"], none))
        blank = {"matches": 0, "voids": 0, "avg_score": 0.0, "pass_rate": 0.0,
                 "avg_seconds": 0.0, "spread": 0.0, "models": [], "best": None,
                 "last": 0}
        # tasks with a rated record whose matches have been pruned off the log
        for key, extra in leaders.items():
            if key not in seen:
                seen.add(key)
                rows.append({"task": key, "title": titles.get(key, key),
                             "suite": str(key).split("#", 1)[0], **blank, **extra})
        # pool tasks nobody has played yet, listed so they are known to exist
        for t in pool:
            if t["key"] not in seen:
                rows.append({"task": t["key"], "title": t["title"],
                             "suite": t["suite"], **blank, **none,
                             "unplayed": True})
        return rows

    def models_status(self) -> Dict[str, Any]:
        """The model board plus what it took to build: how many matches are
        controlled head-to-heads, and which model the daily round is on."""
        rows = self.model_board()
        cfg = self.config()
        return {
            "models": rows,
            "rated": sum(1 for r in rows if r["rated"]),
            "matches": sum(r["matches"] for r in rows),
            # what a scheduled round will play on — None means each agent's own
            # model, and `free` overrides both with a zero-cost pick
            "round_model": cfg.get("model"),
            "free": bool(cfg.get("free", True)),
            "agents": self.subjects(),
            "tasks": [{"key": t["key"], "title": t["title"], "suite": t["suite"]}
                      for t in self.tasks()],
            "running": self._running,
        }

    def card(self, agent: str) -> Dict[str, Any]:
        """One agent's record: rating, per-task scores, recent matches."""
        r = self._rating(agent)
        board = {row["agent"]: row for row in self.leaderboard()}
        titles = {t["key"]: t["title"] for t in self.tasks()}
        per_task = [
            {"task": k, "title": titles.get(k, k), **v}
            for k, v in sorted(r.get("per_task", {}).items())
        ]
        return {
            "agent": agent,
            "icon": self.icon(agent),
            **board.get(agent, {"elo": r["elo"], "rank": None}),
            "per_task": per_task,
            "matches_log": self.matches(limit=20, agent=agent),
        }

    def _suite_agents(self, spec: Dict[str, Any]) -> Optional[List[str]]:
        """The subjects a task's eval suite restricts itself to, or None.

        Only a shipped eval suite can name its agents. A hand-written task,
        an openarena problem or an arena drill has no `evals/<suite>/mod.py`
        behind it, and looking one up raised `eval not found: custom` out of
        the middle of a round — which then never stamped last_round and was
        started again on every tick.
        """
        try:
            return self.evals.get(spec["suite"]).get("agents")
        except Exception:
            return None

    @staticmethod
    def _rate_limited(reason: Optional[str]) -> bool:
        """Does a void reason say the provider shut the door, not that the
        agent tripped? A 429, or the agent loop's own plain-words rewrite."""
        low = (reason or "").lower()
        return ("429" in low or "rate limit" in low or "rate-limit" in low
                or "quota" in low)

    def _cooldown(self, reason: Optional[str] = None) -> float:
        """Sit out until the provider reopens: the reset time if the error
        names one, RATE_LIMIT_COOLDOWN from now if it doesn't."""
        until = _now() + RATE_LIMIT_COOLDOWN
        found = re.search(r"resets (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)", reason or "")
        if found:
            try:
                until = max(until, calendar.timegm(
                    time.strptime(found.group(1), "%Y-%m-%dT%H:%M:%SZ")))
            except Exception:
                pass
        self._state["cooldown_until"] = until
        self._state["cooldown_reason"] = str(reason or "")[:200]
        self._save_state()
        return until

    def cooling(self) -> bool:
        return _now() < float(self._state.get("cooldown_until", 0) or 0)

    def due(self) -> bool:
        """Has a day (or whatever the config says) passed since the last round?
        Never while the board is cooling down after a rate limit."""
        if self.cooling():
            return False
        period = float(self.config().get("period_hours", 24)) * 3600
        return (_now() - float(self._state.get("last_round", 0))) >= period

    def next_run(self) -> float:
        period = float(self.config().get("period_hours", 24)) * 3600
        return max(float(self._state.get("last_round", 0)) + period,
                   float(self._state.get("cooldown_until", 0) or 0))

    def status(self) -> Dict[str, Any]:
        cfg = self.config()
        pool = self.tasks()
        suites: Dict[str, int] = {}
        for t in pool:
            suites[t["suite"]] = suites.get(t["suite"], 0) + 1
        return {
            "enabled": bool(cfg.get("enabled")),
            "config": cfg,
            # how the pool breaks down, so the board can say where a task came
            # from without a second call. openarena's own health is a separate
            # action — see openarena_status()
            "suites_count": suites,
            "season": self._state.get("season", 0),
            "last_round": self._state.get("last_round", 0),
            "next_round": self.next_run(),
            "due": self.due(),
            # a board sitting out a rate limit says so, and until when
            "cooldown_until": float(self._state.get("cooldown_until", 0) or 0),
            "cooldown_reason": self._state.get("cooldown_reason") or None,
            "running": self._running,
            "scheduler": self.scheduler.status() if self.scheduler else {"alive": False},
            "subjects": self.subjects(),
            "newcomers": self.newcomers(),
            "tasks": len(pool),
            "round_tasks": [t["key"] for t in self.round_tasks()],
            "rounds": (self._state.get("rounds") or [])[-5:],
            "matches": sum(r.get("matches", 0)
                           for r in self._state.get("ratings", {}).values()),
            "tokens": sum(int(r.get("tokens_sum", 0))
                          for r in self._state.get("ratings", {}).values()),
        }

    # ── mod protocol ───────────────────────────────────────────────

    def forward(self, action: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                  -> board + status
        forward('tasks')                           -> the task pool
        forward('matches', limit=, agent=, task=)  -> recent matches
        forward('card', agent=)                    -> one agent's record
        forward('run', agent=, task=)              -> one match, or a full round

        The same matches, read by model rather than by agent:
        forward('models')                          -> the model board
        forward('model', model=)                   -> one model's record
        forward('task_board')                      -> per task, model by model
        forward('gauntlet', models=[], agent=)     -> play them against each other

        forward('qualify', agent=)                 -> score a newcomer
        forward('config', enabled=, free=, ...)    -> update the knobs
        forward('task_add', spec=, owner=, slug=)  -> store a hand-written task
        forward('task_rm', slug=)                  -> drop one

        The arena module's drills — one generated question, graded over there:
        forward('arena')                           -> is it up, which drills
        forward('drill', game=, round=, seed=)     -> the view, unplayed

        The openarena schema — statement plus graded cases, judged next door:
        forward('openarena')                       -> is it up, what it holds
        forward('oa_task_add', spec=, author=)     -> upload one there
        forward('oa_task_rm', slug=)               -> delete one there
        forward('oa_import', source=, limit=)      -> a published benchmark in
        forward('oa_preview', source=, limit=)     -> ...converted, kept nowhere
        forward('oa_enter', agent=)                -> our agent on their board

        Ownership is not checked here — the caller (mod.forward) resolves the
        address and decides. This class only knows about tasks and scores.
        """
        if action in (None, "board", "leaderboard"):
            return {"leaderboard": self.leaderboard(), "status": self.status()}
        if action == "tasks":
            return {"tasks": self.tasks(), "round": [t["key"] for t in self.round_tasks()],
                    "scorers": sorted(SCORERS), "custom": self.custom()}
        if action == "matches":
            return {"matches": self.matches(int(kwargs.get("limit", 50)),
                                            kwargs.get("agent"), kwargs.get("task"))}
        if action == "card":
            return self.card(kwargs.get("agent", ""))
        if action == "status":
            return self.status()
        if action == "run":
            agent, task = kwargs.get("agent"), kwargs.get("task")
            if agent and task:
                return self.run_match(agent, task, model=kwargs.get("model"),
                                      steps=kwargs.get("steps"), free=kwargs.get("free"),
                                      reason=kwargs.get("reason", "manual"))
            return self.run_round(agents=[agent] if agent else kwargs.get("agents"),
                                  tasks=[task] if task else kwargs.get("tasks"),
                                  reason=kwargs.get("reason", "manual"),
                                  force=bool(kwargs.get("force", False)))
        if action == "qualify":
            return self.qualify(kwargs.get("agent", ""))
        if action in ("models", "model_board"):
            return self.models_status()
        if action == "model":
            return self.model_card(kwargs.get("model", ""))
        if action in ("task_board", "tasks_board"):
            return {"tasks": self.task_board()}
        if action == "gauntlet":
            return self.run_gauntlet(kwargs.get("models") or [],
                                     agent=kwargs.get("agent"),
                                     tasks=kwargs.get("tasks"),
                                     steps=kwargs.get("steps"),
                                     free=bool(kwargs.get("free", False)),
                                     reason=kwargs.get("reason", "gauntlet"))
        if action == "config":
            return self.set_config(**{k: v for k, v in kwargs.items() if k in DEFAULTS})
        if action == "task_add":
            return self.add_task(kwargs.get("spec") or {}, owner=kwargs.get("owner"),
                                 slug=kwargs.get("slug"))
        if action == "task_rm":
            return self.remove_task(kwargs.get("slug", ""))
        # ── the arena module's drills ─────────────────────────────
        if action in ("arena", "drills"):
            return self.arena_status()
        if action in ("drill", "arena_sheet"):
            # the view a seat is shown, without playing anything — what the
            # console puts on the card, and what a task's prompt is built from
            return {"game": kwargs.get("game", ""),
                    "round": int(kwargs.get("round", 0) or 0),
                    "seed": int(kwargs.get("seed", dr.DEFAULT_SEED) or 0),
                    "view": dr.sheet(kwargs.get("game", ""),
                                     seed=int(kwargs.get("seed", dr.DEFAULT_SEED) or 0),
                                     round=int(kwargs.get("round", 0) or 0))}
        # ── the openarena schema ──────────────────────────────────
        if action in ("openarena", "oa"):
            return self.openarena_status()
        if action in ("oa_task_add", "openarena_task_add"):
            return oa.create_task(kwargs.get("spec") or {},
                                  author=kwargs.get("author"))
        if action in ("oa_task_rm", "openarena_task_rm"):
            return oa.delete_task(kwargs.get("slug", ""))
        if action in ("oa_sources", "openarena_sources"):
            return oa.bench_sources()
        if action in ("oa_preview", "openarena_preview"):
            return oa.bench_preview(kwargs.get("source", ""),
                                    **_bench_opts(kwargs))
        if action in ("oa_import", "openarena_import"):
            return oa.bench_import(kwargs.get("source", ""),
                                   **_bench_opts(kwargs))
        if action in ("oa_enter", "openarena_enter"):
            return oa.enter(kwargs.get("agent", ""), name=kwargs.get("name"),
                            model=kwargs.get("model"),
                            steps=kwargs.get("steps"),
                            free=kwargs.get("free"))
        raise KeyError(f"unknown arena action: {action}")


class Scheduler:
    """The background process that keeps the board current.

    One daemon thread, one tick every `poll_seconds`:
        1. any agent that came online since the last tick is qualified now
        2. if the period has elapsed, a full round runs

    Both are serialized behind the arena's own lock, so a tick that lands
    mid-round does nothing rather than double-booking an agent.
    """

    def __init__(self, arena: Arena):
        self.arena = arena
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started_at = 0.0
        self.ticks = 0
        self.last_tick = 0.0
        self.last_error: Optional[str] = None
        self.last_action: Optional[str] = None
        arena.scheduler = self

    def start(self, delay: float = 15.0) -> Dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        self.started_at = _now()
        self._thread = threading.Thread(target=self._loop, args=(delay,),
                                        name="arena-scheduler", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        return self.status()

    def _loop(self, delay: float) -> None:
        # let the API finish booting before the first tick — a round kicked off
        # during startup would race the provider key coming out of the vault
        if self._stop.wait(delay):
            return
        while True:
            try:
                self.tick()
            except Exception as e:
                self.last_error = str(e)
            interval = max(10.0, float(self.arena.config().get("poll_seconds", 60)))
            if self._stop.wait(interval):
                return

    def tick(self) -> Dict[str, Any]:
        """One pass: qualify newcomers, then run the round if it's due."""
        self.ticks += 1
        self.last_tick = _now()
        if not self.arena.config().get("enabled"):
            self.last_action = "disabled"
            return {"skipped": "disabled"}
        if self.arena.cooling():
            # qualifiers too: a newcomer played into a closed door is a void
            # and a slice of the quota, same as a round
            until = float(self.arena._state.get("cooldown_until", 0) or 0)
            self.last_action = "cooling down until " + time.strftime(
                "%Y-%m-%d %H:%M UTC", time.gmtime(until))
            return {"skipped": "rate_limited", "until": until}

        done = []
        # on a board with no history, the first round already plays the whole
        # field — qualifying each agent against nobody first would run every
        # match twice for no comparison
        seeding = self.arena.due() and not self.arena._state.get("ratings")
        for agent in ([] if seeding else self.arena.newcomers()):
            self.last_action = f"qualifying {agent}"
            out = self.arena.qualify(agent)
            done.append(out)
            if out.get("error"):       # a round holds the lock — try next tick
                break

        if self.arena.due():
            self.last_action = "daily round"
            done.append(self.arena.run_round(reason="daily"))

        if not done:
            self.last_action = "idle"
        return {"actions": len(done), "results": done}

    def status(self) -> Dict[str, Any]:
        return {
            "alive": bool(self._thread and self._thread.is_alive()),
            "started_at": self.started_at,
            "ticks": self.ticks,
            "last_tick": self.last_tick,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "poll_seconds": self.arena.config().get("poll_seconds", 60),
        }
