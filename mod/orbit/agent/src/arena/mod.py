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

Tasks come from the eval registry next door — an eval task is an arena task,
with `checks` (substrings) or `scorers` (scorer specs) saying what passing
means, and an optional `setup.files` map seeded into the scratch dir first.

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
    arena.run_match("builder", "code/python#0")
    arena.run_round(reason="manual")    # everyone x the round's tasks
    arena.qualify("mynewagent")         # newcomer vs the incumbents
    arena.leaderboard()                 # ranked board
    arena.forward()                     # mod protocol entry point
"""
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from src.evals.mod import Evals
    from src.evals.scorers import run_scorer, steps_of
    from src.agents.mod import Agents
except ImportError:  # running the arena standalone
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.evals.mod import Evals
    from src.evals.scorers import run_scorer, steps_of
    from src.agents.mod import Agents


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
    "model": None,           # None = the agent's own model, then provider default
    "steps": 8,
    "period_hours": 24,
    "poll_seconds": 60,
    "tasks_per_round": 3,    # rotated through the pool, season by season
    "max_matches": 40,       # hard ceiling on one round
    "harnesses": False,      # CLI-backed agents run the host's own shell — opt in
    "agents": None,          # None = every eligible agent
    "suites": None,          # None = every eval suite
    "retries": 1,            # a voided match is replayed this many times
}

# how long to wait before replaying a match the provider voided — a free model
# that just said "rate limited" will say it again immediately
RETRY_PAUSE = 20.0

MAX_MATCH_LINES = 5000


def _now() -> float:
    return time.time()


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
                 "first_seen": _now(), "last": 0, "per_task": {}}
            self._state["ratings"][agent] = r
        r.setdefault("per_task", {})
        return r

    # ── subjects ───────────────────────────────────────────────────

    def subjects(self) -> List[str]:
        """The agents eligible to compete, in registry order.

        Harness agents (Claude Code, Codex) hand the run to a CLI on the host
        with its approval prompts off, so they stay out unless the host opts in.
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
        """The whole task pool, flattened across eval suites."""
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
        return out

    def task(self, key: str) -> Dict[str, Any]:
        for t in self.tasks():
            if t["key"] == key:
                return t
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
        """Score one trace against a task. Pure — no model, no network."""
        workdir = Path(workdir)
        checks = [run_scorer(self._resolve_spec(s, workdir), trace)
                  for s in task.get("scorers", [])]
        correct = (sum(1 for c in checks if c.get("passed")) / len(checks)) if checks else 0.0

        clean = run_scorer({"type": "no_errors"}, trace)
        done = run_scorer({"type": "finished"}, trace)
        reliable = (float(bool(clean["passed"])) + float(bool(done["passed"]))) / 2

        limit = limit or task.get("steps") or int(self.config().get("steps", 8))
        used = len(steps_of(trace))
        # unspent budget, but only if it actually finished — stopping early by
        # falling over is not efficiency
        efficient = max(0.0, 1 - used / max(1, limit)) if done["passed"] else 0.0

        total = W_CORRECT * correct + W_RELIABLE * reliable + W_EFFICIENT * efficient
        return {
            "score": round(total, 4),
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
                  free: bool = None, reason: str = "manual") -> Dict[str, Any]:
        """Run one agent on one task and score it. Records the match."""
        spec = self.task(task) if isinstance(task, str) else task
        cfg = self.config()
        model = model if model is not None else cfg.get("model")
        free = cfg.get("free", True) if free is None else free
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
            trace, usage = self._runner(prompt=prompt, agent=agent, model=model,
                                        steps=limit, free=free, path=str(workdir))
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
            "model": (usage or {}).get("model") or model,
            "seconds": seconds,
            "cost": round(float((usage or {}).get("cost", 0.0) or 0.0), 6),
            "error": error,
            "budget": limit,
            **{k: scored[k] for k in ("score", "correct", "reliable", "efficient",
                                      "passed", "steps")},
            "checks": [{"type": c.get("type"), "passed": c.get("passed"),
                        "reason": c.get("reason")} for c in scored["checks"]],
        }
        # a run that never reached the model scored 0 on everything — that is a
        # forfeit, not a loss, so say so rather than pretending it competed
        if error:
            match["score"] = 0.0
        self._running = None
        self._append_match(match)
        self._record(agent, match)
        # the scratch dir has done its job once the scorers have read it
        shutil.rmtree(workdir, ignore_errors=True)
        return match

    def _record(self, agent: str, match: Dict[str, Any]) -> None:
        r = self._rating(agent)
        r["matches"] += 1
        r["score_sum"] = round(r.get("score_sum", 0.0) + match["score"], 4)
        r["seconds_sum"] = round(r.get("seconds_sum", 0.0) + match["seconds"], 2)
        r["cost_sum"] = round(r.get("cost_sum", 0.0) + match.get("cost", 0.0), 6)
        r["last"] = match["ts"]
        per = r["per_task"].setdefault(match["task"], {"n": 0, "best": 0.0, "last": 0.0})
        per["n"] += 1
        per["last"] = match["score"]
        per["best"] = max(per.get("best", 0.0), match["score"])
        per["ts"] = match["ts"]
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
                  reason: str = "manual") -> Dict[str, Any]:
        """Every agent plays every task in the round, then everyone is rated.

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
            budget = int(cfg.get("max_matches", 40))
            played, results = [], {}
            for spec in pool:
                # an eval can name the subjects it applies to
                allowed = self.evals.get(spec["suite"]).get("agents")
                for agent in field:
                    if allowed and agent not in allowed:
                        continue
                    if len(played) >= budget:
                        break
                    match = self.run_match(agent, spec, reason=reason)
                    played.append(match)
                    results.setdefault(spec["key"], []).append((agent, match["score"]))
            for key, entries in results.items():
                self._rate(key, entries)

            self._state["season"] = int(self._state.get("season", 0)) + 1
            self._state["last_round"] = _now()
            summary = {
                "reason": reason,
                "ts": self._state["last_round"],
                "season": self._state["season"],
                "agents": field,
                "tasks": [t["key"] for t in pool],
                "matches": len(played),
                "capped": len(played) >= budget,
            }
            rounds = self._state.setdefault("rounds", [])
            rounds.append(summary)
            self._state["rounds"] = rounds[-50:]
            self._save_state()
            return {**summary, "results": played}
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
            played = []
            for spec in pool:
                allowed = self.evals.get(spec["suite"]).get("agents")
                if allowed and agent not in allowed:
                    continue
                match = self.run_match(agent, spec, reason=reason or f"qualifier:{agent}")
                played.append(match)
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
                    "tasks": [t["key"] for t in pool], "results": played,
                    "elo": self._rating(agent)["elo"]}
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
                "tasks": len(r.get("per_task", {})),
                "last": r.get("last", 0),
            })
        rows.sort(key=lambda x: (-x["elo"], -x["avg_score"], x["agent"]))
        for i, row in enumerate(rows, 1):
            row["rank"] = i
        return rows

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

    def due(self) -> bool:
        """Has a day (or whatever the config says) passed since the last round?"""
        period = float(self.config().get("period_hours", 24)) * 3600
        return (_now() - float(self._state.get("last_round", 0))) >= period

    def next_run(self) -> float:
        period = float(self.config().get("period_hours", 24)) * 3600
        return float(self._state.get("last_round", 0)) + period

    def status(self) -> Dict[str, Any]:
        cfg = self.config()
        return {
            "enabled": bool(cfg.get("enabled")),
            "config": cfg,
            "season": self._state.get("season", 0),
            "last_round": self._state.get("last_round", 0),
            "next_round": self.next_run(),
            "due": self.due(),
            "running": self._running,
            "scheduler": self.scheduler.status() if self.scheduler else {"alive": False},
            "subjects": self.subjects(),
            "newcomers": self.newcomers(),
            "tasks": len(self.tasks()),
            "round_tasks": [t["key"] for t in self.round_tasks()],
            "rounds": (self._state.get("rounds") or [])[-5:],
            "matches": sum(r.get("matches", 0)
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
        forward('qualify', agent=)                 -> score a newcomer
        forward('config', enabled=, free=, ...)    -> update the knobs
        """
        if action in (None, "board", "leaderboard"):
            return {"leaderboard": self.leaderboard(), "status": self.status()}
        if action == "tasks":
            return {"tasks": self.tasks(), "round": [t["key"] for t in self.round_tasks()]}
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
                                  reason=kwargs.get("reason", "manual"))
        if action == "qualify":
            return self.qualify(kwargs.get("agent", ""))
        if action == "config":
            return self.set_config(**{k: v for k, v in kwargs.items() if k in DEFAULTS})
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

        done = []
        for agent in self.arena.newcomers():
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
