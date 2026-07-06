"""
evals - agent eval registry

Auto-discovers eval modules in evals/ directory.
Evals can be flat (evals/<name>/mod.py) or nested by category
(evals/<category>/<name>/mod.py and surfaced as "category/name").

Each eval is a folder with mod.py containing an Eval class with:
    name, description, language (optional), agents (optional),
    owner (optional — defaults to runtime owner key),
    tasks: list of {"prompt": str, "checks": list[str]}

Subjects under test include the persona agents discovered in
sibling agents/ directory PLUS the runtime modules `agent` and `claude`.

Usage:
    evals = Evals()
    evals.ls()                          # ['code/python', 'code/rust', ...]
    evals.get("code/python")            # eval config
    evals.for_agent("builder")          # evals targeting a subject
    evals.show()                        # per-agent table with owner key
    evals.run("code/python", "builder") # run an eval on a subject
    evals.forward()                     # mod protocol entry point
"""
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    import mod as m
except ImportError:
    m = None


class Evals:
    description = "Eval registry - discover, inspect, and run agent evals"

    def __init__(self, **kwargs):
        self._dir = Path(__file__).parent
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ── discovery ──────────────────────────────────────────────────

    def _walk(self) -> List[Path]:
        """Return all eval mod.py files (flat or nested one level)."""
        out = []
        for child in self._dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if (child / "mod.py").exists():
                out.append(child)
                continue
            # category dir — descend one level
            for sub in child.iterdir():
                if sub.is_dir() and not sub.name.startswith("_") and (sub / "mod.py").exists():
                    out.append(sub)
        return out

    def _key(self, path: Path) -> str:
        rel = path.relative_to(self._dir)
        return str(rel).replace("\\", "/")

    def ls(self) -> List[str]:
        """List eval keys, e.g. ['code/python', 'code/rust', 'code/typescript']."""
        return sorted(self._key(p) for p in self._walk())

    def get(self, key: str) -> Dict[str, Any]:
        """Load an eval config by key (e.g. 'code/python')."""
        if key in self._cache:
            return self._cache[key]
        mod_path = self._dir / key / "mod.py"
        if not mod_path.exists():
            raise KeyError(f"eval not found: {key}")
        spec = importlib.util.spec_from_file_location(
            f"evals.{key.replace('/', '.')}", str(mod_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Eval", None)
        if cls is None:
            raise AttributeError(f"no Eval class in {key}/mod.py")
        config = {
            "key": key,
            "name": getattr(cls, "name", key),
            "description": getattr(cls, "description", ""),
            "language": getattr(cls, "language", None),
            "agents": getattr(cls, "agents", None),  # None = all subjects
            "owner": getattr(cls, "owner", None),
            "tasks": getattr(cls, "tasks", []),
            "cls": cls,
        }
        self._cache[key] = config
        return config

    # ── owner key ──────────────────────────────────────────────────

    def owner_key(self, subject: str = None, eval_key: str = None) -> str:
        """Resolve the owner key (address) for a subject or eval.

        Order of resolution:
          1. eval-level ``owner`` field, if set
          2. runtime mod key (``m.key().address``)
          3. literal "(unset)" when mod runtime is unavailable
        """
        if eval_key:
            owner = self.get(eval_key).get("owner")
            if owner:
                return owner
        if m is None:
            return "(unset)"
        try:
            return m.key().address
        except Exception:
            return "(unset)"

    # ── agent integration ──────────────────────────────────────────

    # subjects under test = persona agents + runtime modules
    RUNTIMES = ("agent", "claude")

    def _persona_agents(self) -> List[str]:
        """Discover sibling agents/ directory."""
        agents_dir = self._dir.parent / "agents"
        if not agents_dir.exists():
            return []
        return sorted(
            d.name for d in agents_dir.iterdir()
            if d.is_dir() and not d.name.startswith("_") and (d / "mod.py").exists()
        )

    def _agents(self) -> List[str]:
        """All testable subjects: persona agents + runtime modules."""
        return self._persona_agents() + list(self.RUNTIMES)

    def for_agent(self, agent: str) -> List[str]:
        """Return eval keys that target a given subject."""
        out = []
        for key in self.ls():
            cfg = self.get(key)
            targets = cfg.get("agents")
            if targets is None or agent in targets:
                out.append(key)
        return out

    def matrix(self) -> Dict[str, List[str]]:
        """Return {subject: [eval_key, ...]} mapping for all known subjects."""
        return {a: self.for_agent(a) for a in self._agents()}

    def show(self) -> str:
        """Render a per-subject eval table with owner key column."""
        mtx = self.matrix()
        if not mtx:
            return "(no subjects discovered)"

        rows = [
            (subject, self.owner_key(subject), ", ".join(keys) if keys else "(none)")
            for subject, keys in mtx.items()
        ]
        w_subj = max(len("agent"), max(len(r[0]) for r in rows)) + 2
        w_own = max(len("owner key"), max(len(r[1]) for r in rows)) + 2

        header = f"{'agent'.ljust(w_subj)}{'owner key'.ljust(w_own)}evals"
        sep = "-" * len(header)
        lines = ["evals per agent:", "", header, sep]
        for subj, owner, evals in rows:
            lines.append(f"{subj.ljust(w_subj)}{owner.ljust(w_own)}{evals}")
        lines.append("")
        lines.append(f"total evals: {len(self.ls())}  total subjects: {len(rows)}")
        return "\n".join(lines)

    # ── execution ──────────────────────────────────────────────────

    def run(self, key: str, agent: str, **kwargs) -> Dict[str, Any]:
        """Run an eval on an agent. Returns task results.

        Requires the agent runtime (m.mod('agent')) to dispatch prompts.
        Without runtime, returns the dry-run plan.
        """
        cfg = self.get(key)
        plan = {
            "eval": key,
            "agent": agent,
            "language": cfg.get("language"),
            "tasks": cfg.get("tasks", []),
        }

        plan["owner_key"] = self.owner_key(agent, eval_key=key)

        if m is None:
            plan["status"] = "dry-run (mod runtime unavailable)"
            return plan

        # Route to the right runtime: agent runtime, claude runtime,
        # or persona agent dispatched through the agent runtime.
        try:
            if agent == "claude":
                runtime = m.mod("claude")()
                dispatch = lambda p: runtime.ask(p, **kwargs)  # noqa: E731
            elif agent == "agent":
                runtime = m.mod("agent")()
                dispatch = lambda p: runtime.forward(prompt=p, **kwargs)  # noqa: E731
            else:
                runtime = m.mod("agent")(name=agent)
                dispatch = lambda p: runtime.forward(prompt=p, **kwargs)  # noqa: E731
        except Exception as e:
            plan["status"] = f"dry-run (runtime error: {e})"
            return plan

        results = []
        for task in cfg.get("tasks", []):
            prompt = task.get("prompt", "")
            checks = task.get("checks", [])
            try:
                response = dispatch(prompt)
            except Exception as e:
                results.append({"prompt": prompt, "error": str(e), "passed": False})
                continue
            text = response if isinstance(response, str) else str(response)
            passed = all(c in text for c in checks) if checks else True
            results.append({
                "prompt": prompt,
                "checks": checks,
                "passed": passed,
                "response": text,
            })
        plan["results"] = results
        plan["passed"] = sum(1 for r in results if r.get("passed"))
        plan["total"] = len(results)
        plan["status"] = "complete"
        return plan

    def schema(self) -> Dict[str, Dict]:
        """Get configs for all evals (without cls)."""
        out = {}
        for key in self.ls():
            try:
                cfg = self.get(key)
                out[key] = {k: v for k, v in cfg.items() if k != "cls"}
            except Exception as e:
                out[key] = {"error": str(e)}
        return out

    # ── mod protocol ───────────────────────────────────────────────

    def forward(self, key: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                  -> list all evals + matrix
        forward("code/python")                     -> get eval config
        forward(action="show")                     -> per-agent display
        forward(action="for_agent", agent="...")   -> evals for an agent
        forward(action="run", key=..., agent=...)  -> run an eval on an agent
        """
        action = kwargs.get("action")

        if action == "show":
            return self.show()
        if action == "for_agent":
            return self.for_agent(kwargs.get("agent", ""))
        if action == "run":
            return self.run(
                key=kwargs.get("key", key or ""),
                agent=kwargs.get("agent", ""),
                **{k: v for k, v in kwargs.items() if k not in ("action", "key", "agent")},
            )

        if key is None:
            return {
                "evals": self.ls(),
                "agents": self._agents(),
                "matrix": self.matrix(),
                "schemas": self.schema(),
            }
        return {k: v for k, v in self.get(key).items() if k != "cls"}
