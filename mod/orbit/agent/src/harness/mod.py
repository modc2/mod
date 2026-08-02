"""
harness - hand a whole run to another module's coding agent.

Most agents here are a persona over our own loop: a goal, a skill set, a model.
A harness agent is different — the run goes to a module that already owns an
agent of its own, with its own tools, sandbox and models:

    claude     -> orbit/claudecode   the Claude Code CLI on this host
    codex      -> orbit/codexcli     the Codex CLI on this host
    claudemod  -> orbit/claude       that module's job server: the same CLI,
                                     plus per-caller sandboxing, snapshot CIDs
                                     and a public task ledger

Every runner module answers the same two calls:

    harness()                                  -> {name, label, available, ...}
    run(query, path=, goal=, model=, timeout=, on_step=)  -> [step, ...]

and returns the step dicts this module's loop emits ({'tool', 'params',
'result'|'error'}, ending in finish), so a harness run renders in the console
exactly like a native one. That's the whole contract: no CLI-specific code
lives here, and adding a harness is adding a module plus one line in RUNNERS.

The CLIs run with their approval prompts off — there is no human at the other
end of a server-side run — so a harness run is host-owner only. The gate lives
in Mod._run, not here.

Usage:
    h = Harness()
    h.ls()                                       # runners + is each one usable
    h.run('claude', 'fix the bug', path='/repo', on_step=print)
"""
from typing import Any, Callable, Dict, List

try:
    import mod as m
except ImportError:
    m = None

# wall-clock cap on one harness run
DEFAULT_TIMEOUT = 1800

# harness name -> the module that implements it
RUNNERS = {
    "claude": "claudecode",
    "codex": "codexcli",
    "claudemod": "claude",
}


class Harness:
    description = "Run another module's coding agent (Claude Code, Codex, the claude console)"

    RUNNERS = RUNNERS

    def __init__(self):
        self._loaded: Dict[str, Any] = {}

    # ── the runner modules ───────────────────────────────────────────

    def names(self) -> List[str]:
        return list(self.RUNNERS)

    def exists(self, name: str) -> bool:
        return name in self.RUNNERS

    def get(self, name: str):
        """The module behind a harness name, instantiated once and kept."""
        if name not in self.RUNNERS:
            raise KeyError(f"unknown harness: {name} (have: {', '.join(self.RUNNERS)})")
        if name not in self._loaded:
            module = self.RUNNERS[name]
            if m is None:
                raise RuntimeError(
                    f"the {name} harness lives in the {module} module, which needs "
                    f"the mod protocol to load — it isn't importable here")
            self._loaded[name] = m.mod(module)()
        return self._loaded[name]

    def info(self, name: str) -> Dict[str, Any]:
        """One runner's card. A module that won't load is reported, not raised:
        the console lists every harness, installed or not."""
        module = self.RUNNERS.get(name, name)
        try:
            card = dict(self.get(name).harness())
        except Exception as e:
            return {"name": name, "label": name, "module": module,
                    "description": f"provided by the {module} module",
                    "available": False, "error": str(e),
                    "install": f"the {module} module is missing or won't load"}
        card.setdefault("name", name)
        card.setdefault("module", module)
        return card

    def ls(self) -> List[Dict[str, Any]]:
        """Every runner and whether it can run on this host."""
        return [self.info(name) for name in self.RUNNERS]

    # ── the run ──────────────────────────────────────────────────────

    def run(self, name: str, query: str, path: str = None, goal: str = None,
            model: str = None, timeout: int = DEFAULT_TIMEOUT,
            on_step: Callable[[dict], None] = None,
            key=None, **kwargs) -> List[dict]:
        """Hand the run to a runner module and return its steps.

        Args:
            name: harness name ('claude', 'codex', 'claudemod')
            query: the task, handed on as the prompt
            path: working directory for the run (default: cwd)
            goal: system prompt for the run
            model: model override passed to the runner
            timeout: wall-clock cap; the run is killed when it expires
            on_step: called with each step as it happens (live progress)
            key: caller identity, for runners that scope a run by it
        """
        return self.get(name).run(query, path=path, goal=goal, model=model,
                                  timeout=timeout, on_step=on_step, key=key,
                                  **kwargs)

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                      -> list runners + availability
        forward('claude')              -> one runner's card
        forward(action='run', name=..., query=...) -> run it
        """
        if kwargs.get("action") == "run":
            return self.run(kwargs.get("name", name or ""),
                            query=kwargs.get("query", ""),
                            path=kwargs.get("path"),
                            goal=kwargs.get("goal"),
                            model=kwargs.get("model"),
                            timeout=kwargs.get("timeout", DEFAULT_TIMEOUT))
        if name:
            return self.info(name)
        runners = self.ls()
        return {"harnesses": runners,
                "available": [r["name"] for r in runners if r["available"]]}
