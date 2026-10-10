"""
cron — run an agent every N minutes

A job is: which agent, what to ask it, how often (minutes), and whose standing
it runs on. A daemon thread checks every CHECK seconds and starts whatever is
due. Every run is recorded on the job (status, answer, cost, the compute it ran
on) and, when the API is up, lands in the console's TASKS list like any run.

Who may schedule:

    the module owner        any agent, any job; manages everyone's jobs
    a granted address       the owner granted it 'cron' (or '*') via /grant;
                            manages its own jobs only; runs sandboxed to its
                            portal and billed to its credits, like /run
    anyone else             can see that jobs exist (counts), nothing more

Standing is re-checked at EVERY run, not just at creation: revoke a grant and
that address's jobs pause themselves on their next tick with the reason.

Guards, because a timer that spends is how a fleet loses its quota:

    MIN_EVERY        1 minute; every is clamped to [MIN_EVERY, MAX_EVERY]
    daily_cap        runs per job per UTC day (default 96)
    max_fails        consecutive failures before a job pauses itself (3)
    MAX_JOBS         per address (the owner gets MAX_JOBS_OWNER)
    MAX_PARALLEL     runs in flight at once, across all jobs
    no overlap       a job that is still running is not started again

Like src/grow, the engine never imports the module — it talks to a host, and
ModHost below is the adapter onto Mod. Tests drive it with a fake.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import compute

MIN_EVERY = 1             # minutes
MAX_EVERY = 7 * 24 * 60   # a week
CHECK = 15.0              # seconds between due-checks
MAX_PARALLEL = 2
MAX_JOBS = 20
MAX_JOBS_OWNER = 200
HISTORY = 20              # runs kept per job
LOG_KEEP = 200
PERMISSION = "cron"       # the ACL action an owner grants

JOB_FIELDS = ("agent", "prompt", "every", "enabled", "model", "provider",
              "free", "steps", "daily_cap", "max_fails", "label")
JOB_DEFAULTS: Dict[str, Any] = {
    "enabled": True, "model": None, "provider": None, "free": False,
    "steps": 10, "daily_cap": 96, "max_fails": 3, "label": None,
}


def _now() -> float:
    return time.time()


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class Standing:
    """An address the scheduler runs on behalf of.

    Built here, never parsed off the wire: the address was verified when the
    job was created, and the right to run is re-checked against the module's
    ACL before every run. Mod._resolve_address reads `.address` straight off
    it, so owner checks, portal sandboxing and billing all see this address.
    """
    __slots__ = ("address",)

    def __init__(self, address: str):
        self.address = str(address or "").lower()

    def __repr__(self):
        return f"Standing({self.address})"


class Cron:
    """Jobs, their state, and one run. Knows nothing about Mod — see ModHost."""

    def __init__(self, dir: str = None):
        self.dir = Path(dir) if dir else Path.home() / ".mod" / "agent" / "cron"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / "jobs.json"
        self._lock = threading.RLock()
        self._state = self._load()
        self.running: Dict[str, float] = {}     # job id → started at

    # ── state ────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        try:
            st = json.loads(self._path.read_text())
        except Exception:
            st = {}
        st.setdefault("jobs", {})
        st.setdefault("log", [])
        return st

    def _save(self):
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=1, default=str))
            tmp.replace(self._path)

    def jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in self._state["jobs"].values()]

    def get(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            j = self._state["jobs"].get(job_id)
            if not j:
                raise KeyError(f"no cron job {job_id!r}")
            return dict(j)

    def log(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._state["log"][:max(1, int(limit))]

    # ── validation ───────────────────────────────────────────────────

    @staticmethod
    def _clean(changes: Dict[str, Any]) -> Dict[str, Any]:
        bad = [k for k in changes if k not in JOB_FIELDS]
        if bad:
            raise ValueError(f"unknown cron field(s): {', '.join(bad)} "
                             f"(have: {', '.join(JOB_FIELDS)})")
        out: Dict[str, Any] = {}
        for k, v in changes.items():
            if k == "every":
                try:
                    v = int(float(v))
                except Exception:
                    raise ValueError("every must be a number of minutes")
                v = max(MIN_EVERY, min(MAX_EVERY, v))
            elif k in ("steps",):
                v = max(1, min(50, int(v)))
            elif k in ("daily_cap", "max_fails"):
                v = max(0, int(v))
            elif k in ("enabled", "free"):
                v = bool(v)
            elif k == "prompt":
                v = str(v or "").strip()[:4000]
                if not v:
                    raise ValueError("prompt is empty — say what the agent should do each run")
            elif k == "agent":
                v = str(v or "").strip()
                if not v:
                    raise ValueError("pick an agent")
            else:
                v = (str(v).strip()[:120] or None) if v is not None else None
            out[k] = v
        return out

    # ── writes ───────────────────────────────────────────────────────

    def add(self, owner: str, is_host: bool, **fields) -> Dict[str, Any]:
        f = self._clean(fields)
        for k in ("agent", "prompt", "every"):
            if k not in f:
                raise ValueError(f"{k} is required")
        owner = owner.lower()
        mine = [j for j in self.jobs() if j["owner"] == owner]
        cap = MAX_JOBS_OWNER if is_host else MAX_JOBS
        if len(mine) >= cap:
            raise ValueError(f"job limit reached ({cap}) — delete one first")
        now = _now()
        job = {**JOB_DEFAULTS, **f, "id": uuid.uuid4().hex[:10], "owner": owner,
               "created_at": now, "next_at": now + 60 * f["every"],
               "fails": 0, "runs_total": 0, "day": {"date": _day(), "runs": 0},
               "last": None, "history": [], "paused_reason": None}
        with self._lock:
            self._state["jobs"][job["id"]] = job
            self._save()
        return dict(job)

    def update(self, job_id: str, **fields) -> Dict[str, Any]:
        f = self._clean(fields)
        with self._lock:
            job = self._state["jobs"].get(job_id)
            if not job:
                raise KeyError(f"no cron job {job_id!r}")
            if "every" in f and f["every"] != job.get("every"):
                job["next_at"] = _now() + 60 * f["every"]
            if f.get("enabled") is True and not job.get("enabled"):
                # resuming forgives the failures that paused it
                job.update(fails=0, paused_reason=None,
                           next_at=_now() + 60 * f.get("every", job["every"]))
            job.update(f)
            self._save()
            return dict(job)

    def remove(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._state["jobs"].pop(job_id, None)
            if not job:
                raise KeyError(f"no cron job {job_id!r}")
            self._save()
        return {"removed": job_id, "agent": job.get("agent")}

    def _pause(self, job: Dict, why: str):
        job["enabled"] = False
        job["paused_reason"] = why

    # ── scheduling ───────────────────────────────────────────────────

    def due(self, now: float = None) -> List[Dict[str, Any]]:
        now = now or _now()
        with self._lock:
            return [dict(j) for j in self._state["jobs"].values()
                    if j.get("enabled") and float(j.get("next_at") or 0) <= now
                    and j["id"] not in self.running]

    def run(self, host, job_id: str, manual: bool = False) -> Dict[str, Any]:
        """Run one job now. The scheduler calls this for due jobs; the owner
        (or the job's own author) can call it by hand. Never two at once."""
        with self._lock:
            job = self._state["jobs"].get(job_id)
            if not job:
                raise KeyError(f"no cron job {job_id!r}")
            if job_id in self.running:
                return {"skipped": "already running", "id": job_id}
            if job["day"].get("date") != _day():
                job["day"] = {"date": _day(), "runs": 0}
            started = _now()
            # the next slot is booked from this start, so a slow run doesn't
            # drift the cadence (and a manual run resets the clock)
            job["next_at"] = started + 60 * int(job["every"])
            cap = int(job.get("daily_cap") or 0)
            if cap and job["day"]["runs"] >= cap and not manual:
                self._save()
                return self._record(job, {"t": started, "status": "skipped",
                                          "summary": f"daily cap reached ({cap})"})
            why = host.standing(job["owner"])
            if why:
                self._pause(job, why)
                self._save()
                return self._record(job, {"t": started, "status": "paused", "summary": why})
            if job["agent"] not in host.agents():
                self._pause(job, f"agent {job['agent']!r} no longer exists")
                self._save()
                return self._record(job, {"t": started, "status": "paused",
                                          "summary": job["paused_reason"]})
            self.running[job_id] = started
            job["day"]["runs"] += 1
            job["runs_total"] = int(job.get("runs_total") or 0) + 1
            snap = dict(job)
            self._save()
        res: Dict[str, Any] = {"t": started, "manual": manual}
        try:
            out = host.run(snap) or {}
            res.update(status=out.get("status") or "done",
                       summary=str(out.get("summary") or "")[:600],
                       cost=out.get("cost"), charged=out.get("charged"),
                       task=out.get("task"), compute=out.get("compute"))
        except Exception as e:
            res.update(status="error", summary=str(e)[:600])
        finally:
            res["elapsed"] = round(_now() - started, 2)
            with self._lock:
                self.running.pop(job_id, None)
                job = self._state["jobs"].get(job_id)
                if job is not None:
                    if res["status"] == "error":
                        job["fails"] = int(job.get("fails") or 0) + 1
                        limit = int(job.get("max_fails") or 0)
                        if limit and job["fails"] >= limit:
                            self._pause(job, f"paused after {job['fails']} failures in a row: "
                                             f"{res['summary'][:160]}")
                    else:
                        job["fails"] = 0
                    self._record(job, res)
                    self._save()
        return res

    def _record(self, job: Dict, res: Dict) -> Dict:
        res = {"job": job["id"], "agent": job["agent"], **res}
        job["last"] = res
        job["history"] = [res, *(job.get("history") or [])][:HISTORY]
        self._state["log"] = [res, *self._state["log"]][:LOG_KEEP]
        return res

    # ── reporting ────────────────────────────────────────────────────

    def view(self, job: Dict, full: bool = False) -> Dict[str, Any]:
        out = {k: v for k, v in job.items() if k != "history" or full}
        out["running"] = job["id"] in self.running
        out["running_since"] = self.running.get(job["id"])
        return out


class Scheduler:
    """One daemon thread that starts due jobs on worker threads."""

    def __init__(self, cron: Cron, host):
        self.cron, self.host = cron, host
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._slots = threading.BoundedSemaphore(MAX_PARALLEL)
        self.started_at = 0.0
        self.last_check = 0.0
        self.last_error: Optional[str] = None

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, delay: float = 20.0) -> Dict[str, Any]:
        if self.running():
            return self.status()
        self._stop.clear()
        self.started_at = _now()
        self._thread = threading.Thread(target=self._loop, args=(delay,),
                                        name="cron-scheduler", daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        return self.status()

    def check(self) -> int:
        """Start every due job that has a free slot. Returns how many started."""
        self.last_check = _now()
        started = 0
        for job in self.cron.due():
            if not self._slots.acquire(blocking=False):
                break      # the rest wait for the next check
            threading.Thread(target=self._work, args=(job["id"],),
                             name=f"cron-{job['id']}", daemon=True).start()
            started += 1
        return started

    def _work(self, job_id: str):
        try:
            self.cron.run(self.host, job_id)
        except Exception as e:
            self.last_error = str(e)[:300]
        finally:
            self._slots.release()

    def _loop(self, delay: float):
        if self._stop.wait(delay):
            return
        while True:
            try:
                self.check()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)[:300]
            if self._stop.wait(CHECK):
                return

    def status(self) -> Dict[str, Any]:
        return {"running": self.running(), "started_at": self.started_at or None,
                "last_check": self.last_check or None, "check_every": CHECK,
                "parallel": MAX_PARALLEL, "last_error": self.last_error}


# ── the adapter onto orbit/agent's Mod ───────────────────────────────

class ModHost:
    """What cron needs from the module, and nothing more."""

    def __init__(self, mod):
        self.mod = mod
        # set by the API so scheduled runs show up in its TASKS registry:
        # start(job) -> task, step(task, step), finish(task, status, summary, usage)
        self.observer = None

    def agents(self) -> set:
        return set(self.mod.agents.ls())

    def is_host(self, address: str) -> bool:
        return bool(address) and self.mod.is_owner(Standing(address))

    def may(self, address: str) -> bool:
        """Owner, or the owner granted this address 'cron' (or '*')."""
        if not address:
            return False
        if self.is_host(address):
            return True
        grant = (getattr(self.mod, "_acl", {}) or {}).get(address.lower()) or {}
        acts = grant.get("actions") or []
        return "*" in acts or PERMISSION in acts

    def standing(self, address: str) -> Optional[str]:
        """None if this address may still run its jobs, else why not."""
        return None if self.may(address) else \
            f"{address[:10]}… no longer has '{PERMISSION}' access — ask the owner to grant it"

    def agent_compute(self, name: str, provider: str = None, model: str = None) -> Dict:
        """Where this agent runs: the host for the loop + tools, and the model plane."""
        cfg = {}
        try:
            if name and name in self.agents():
                cfg = self.mod.agents.get(name) or {}
        except Exception:
            cfg = {}
        harness = cfg.get("harness")
        prov = provider or cfg.get("provider")
        if not prov and not harness:
            try:
                prov = self.mod._provider_short(self.mod.default_provider())
            except Exception:
                prov = None
        mdl = model or cfg.get("model")
        if prov and not harness:
            # the model the run will really use: a saved model from another
            # provider is swapped for this provider's default (Mod._model_for)
            try:
                mdl = self.mod._model_for(prov, mdl)
            except Exception:
                mdl = mdl or self.mod.DEFAULT_MODELS.get(prov)
        return compute.where(prov, mdl, harness)

    def run(self, job: Dict) -> Dict[str, Any]:
        mod, owner = self.mod, job["owner"]
        who = Standing(owner)
        host = self.is_host(owner)
        free = bool(job.get("free"))
        provider = job.get("provider") or None
        budget = None
        if not host and not free and not mod.is_free_provider(provider):
            bal = float(mod.credits.balance(owner) or 0)
            if bal <= 0:
                raise RuntimeError("no credits — top up or switch the job to free models")
            fee = 1 + max(0.0, float(getattr(mod.credits, "fee_rate", 0) or 0))
            budget = lambda cost: cost * fee < bal
        obs = self.observer
        task = None
        if obs:
            try:
                task = obs.start(job)
            except Exception:
                task = None
        trace: List[Dict] = []

        def on_step(st):
            trace.append(st)
            if obs and task is not None:
                try:
                    obs.step(task, st)
                except Exception:
                    pass

        where = self.agent_compute(job["agent"], provider, job.get("model"))
        status, summary, usage, charged = "error", "", {}, None
        try:
            last = mod._run(query=job["prompt"], agent_type=job["agent"],
                            model=job.get("model") or None, provider=provider,
                            steps=int(job.get("steps") or 10), free=free, key=who,
                            budget=budget, on_step=on_step)
            steps = trace or (last if isinstance(last, list) else [])
            summary = mod.graph_answer(steps)
            failed = not steps or all(isinstance(s, dict) and s.get("tool") == "error"
                                      for s in steps)
            status = "error" if failed else "done"
            if failed and not summary:
                summary = "the run produced nothing"
        except Exception as e:
            summary = str(e)
        finally:
            # read the meter whether or not anyone is billed: an unread tally
            # would be handed to the next run on this thread
            try:
                usage = mod.meter.take() or {}
            except Exception:
                usage = {}
        if not host and not free and usage.get("calls") and not mod.is_free_provider(provider):
            try:
                c = mod.charge_run(owner, dict(usage, steps=len(trace)),
                                   note=f"cron {job['id']}: {job['prompt'][:60]}")
                charged = c.get("charged")
            except Exception:
                pass
        if usage.get("model"):
            where["inference"]["model"] = usage["model"]
        if obs and task is not None:
            try:
                obs.finish(task, status, summary, usage)
            except Exception:
                pass
        return {"status": status, "summary": summary, "charged": charged,
                "cost": round(float(usage.get("cost") or 0), 8) if usage.get("priced") else None,
                "task": (task or {}).get("id") if isinstance(task, dict) else None,
                "compute": {"hostname": where["host"].get("hostname"),
                            "inference": where["inference"]}}
