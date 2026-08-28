"""
codexcli - the OpenAI Codex CLI, as a module.

The twin of orbit/claudecode: same harness contract, different CLI. Codex
brings its own tools, sandbox and models; this module spawns it in
`exec --json` mode and translates its item stream into the step dicts the rest
of the fleet speaks ({'tool', 'params', 'result'|'error'}, ending in finish).

    harness()                      -> {name, label, available, install, ...}
    run(query, path=, goal=, ...)  -> [step, ...]

(Not to be confused with orbit/codex, which is a whole developer console. This
module is only the CLI driver.)

The CLI runs with its approval prompts off — there is no human at the other end
of a server-side run — so it does whatever the prompt says on this host. Gating
who may call this belongs to the caller (see orbit/agent).

Usage:
    import mod as m
    cx = m.mod('codexcli')()
    cx.harness()                                   # is the binary here?
    cx.run('fix the failing test', path='/repo', on_step=print)
"""
import json
import os
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import mod as m
    print = m.print
except ImportError:
    m = None

# a tool result is a trace row, not a file — keep it renderable
MAX_RESULT = 4000
# wall-clock cap on one run; the CLI is killed when it expires
DEFAULT_TIMEOUT = 1800


def clip(text: Any, limit: int = MAX_RESULT) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + f"\n… [{len(s) - limit} more chars]"


class Session:
    """One run's translation state: CLI items in, step dicts out."""

    def __init__(self):
        self._held: Optional[str] = None   # narration waiting to be flushed
        self._done = False                 # a terminal step was already emitted

    # ── narration buffer ─────────────────────────────────────────────
    # Prose is only a narration step once something follows it: the LAST
    # message of a run is the answer, and that belongs in finish, not the trace.

    def hold(self, text: str) -> List[dict]:
        out = self.flush()
        self._held = (text or "").strip() or None
        return out

    def flush(self) -> List[dict]:
        text, self._held = self._held, None
        return [{"tool": "response", "params": {}, "result": text}] if text else []

    # ── event translation ────────────────────────────────────────────

    def steps(self, event: dict) -> List[dict]:
        kind = event.get("type")
        if kind == "item.completed":
            return self._item(event.get("item") or {})
        if kind == "turn.completed":
            self._done = True
            text, self._held = self._held, None
            return [{"tool": "finish", "params": {"summary": text or ""}}]
        if kind == "turn.failed":
            self._done = True
            msg = (event.get("error") or {}).get("message") or "codex turn failed"
            return [{"tool": "error", "params": {}, "error": clip(msg, 800)}]
        return []

    def _item(self, item: dict) -> List[dict]:
        kind = item.get("type")
        if kind == "agent_message":
            return self.hold(item.get("text") or "")
        if kind == "reasoning":
            text = (item.get("text") or "").strip()
            return self.flush() + ([{"tool": "think", "params": {"thought": clip(text, 1200)}}]
                                   if text else [])
        if kind == "command_execution":
            step = {"tool": "bash", "params": {"command": item.get("command", "")}}
            out = item.get("aggregated_output") or ""
            if item.get("exit_code"):
                step["error"] = clip(out) or f"exit {item.get('exit_code')}"
            else:
                step["result"] = clip(out)
            return self.flush() + [step]
        if kind == "file_change":
            changes = [c for c in (item.get("changes") or []) if isinstance(c, dict)]
            return self.flush() + [{
                "tool": "edit",
                "params": {"files": [c.get("path") for c in changes]},
                "result": f"{len(changes)} file(s) changed",
            }]
        if kind == "mcp_tool_call":
            return self.flush() + [{
                "tool": str(item.get("tool") or "mcp").lower(),
                "params": {"server": item.get("server"), "arguments": item.get("arguments")},
                "result": clip(item.get("result") or ""),
            }]
        if kind == "web_search":
            return self.flush() + [{"tool": "search",
                                    "params": {"query": item.get("query", "")}}]
        if kind == "error":
            # codex reports recoverable trouble (transport fallbacks, retries) as
            # an error item and keeps going — only turn.failed ends a run, so
            # this stays a plain trace row instead of failing the whole task
            return self.flush() + [{"tool": "harness", "params": {},
                                    "result": clip(item.get("message") or "", 800)}]
        return []

    def close(self, code: int, errors: List[str]) -> List[dict]:
        """Terminal step for a CLI that stopped without emitting one itself."""
        if self._done:
            return []
        self._done = True
        text, self._held = self._held, None
        if text:
            return [{"tool": "finish", "params": {"summary": text}}]
        tail = "\n".join(errors[-6:]).strip()
        return [{"tool": "error", "params": {},
                 "error": f"Codex exited with no result (code {code})"
                          + (f":\n{clip(tail, 800)}" if tail else "")}]


class Mod:
    description = "Codex CLI as a harness — hand it a run, get back a step trace"

    # what the fleet knows this harness by
    name = "codex"
    label = "Codex"
    bin = "codex"
    install = "npm install -g @openai/codex"

    endpoints = ["forward", "harness", "run", "available", "command", "session"]

    # ── availability ─────────────────────────────────────────────────

    def path(self) -> Optional[str]:
        """Where the CLI lives on this host, or None."""
        return shutil.which(self.bin)

    def available(self) -> bool:
        return self.path() is not None

    def harness(self) -> Dict[str, Any]:
        """Harness card: who this is and whether it can run here."""
        p = self.path()
        return {"name": self.name, "label": self.label, "bin": self.bin,
                "description": "OpenAI's Codex CLI — its own tools, sandbox and models",
                "install": self.install, "available": bool(p), "path": p,
                "module": "codexcli"}

    # ── the run ──────────────────────────────────────────────────────

    def session(self) -> Session:
        """A fresh translator for one run's event stream."""
        return Session()

    def command(self, query: str, goal: str = None, model: str = None,
                path: str = None) -> List[str]:
        """The argv this harness runs."""
        cmd = [self.bin, "exec", "--json", "--skip-git-repo-check",
               # same trade as claude's --dangerously-skip-permissions: nobody
               # is here to approve, and a harness run is caller-gated
               "--dangerously-bypass-approvals-and-sandbox"]
        if path:
            cmd += ["-C", path]
        if model:
            cmd += ["-m", model]
        # codex has no system-prompt flag — the goal leads the prompt instead
        cmd.append(f"{goal}\n\n{query}" if goal else query)
        return cmd

    def run(self, query: str, path: str = None, goal: str = None,
            model: str = None, timeout: int = DEFAULT_TIMEOUT,
            on_step: Callable[[dict], None] = None,
            env: Dict[str, str] = None, **kwargs) -> List[dict]:
        """Run the CLI to completion, returning its steps.

        Args:
            query: the task, handed to the CLI as its prompt
            path: working directory for the run (default: cwd)
            goal: system prompt — prepended to the prompt, codex has no flag
            model: model override passed to the CLI
            timeout: wall-clock cap; the CLI is killed when it expires
            on_step: called with each step as it happens (live progress)
            env: extra environment for the child
        """
        if not self.available():
            raise RuntimeError(
                f"{self.label} is not installed on this host "
                f"(no `{self.bin}` on PATH). Install it with: {self.install}")
        cwd = str(Path(path).expanduser()) if path else os.getcwd()
        if not Path(cwd).is_dir():
            raise NotADirectoryError(f"working directory does not exist: {cwd}")

        session = self.session()
        steps: List[dict] = []

        def emit(step: dict):
            steps.append(step)
            if on_step:
                try:
                    on_step(step)
                except Exception:
                    pass

        proc = subprocess.Popen(
            self.command(query, goal=goal, model=model, path=cwd),
            cwd=cwd,
            stdin=subprocess.DEVNULL,      # the prompt is an argument; never wait on stdin
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, **(env or {})},
        )
        errors: "deque[str]" = deque(maxlen=40)
        threading.Thread(target=self._drain, args=(proc.stderr, errors), daemon=True).start()
        killer = threading.Timer(timeout, proc.kill)
        killer.start()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    continue           # the CLI also logs plain text to stdout
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for step in session.steps(event):
                    emit(step)
        finally:
            killer.cancel()
            code = proc.wait()
        for step in session.close(code, list(errors)):
            emit(step)
        return steps

    @staticmethod
    def _drain(stream, sink: "deque[str]"):
        try:
            for line in stream:
                line = line.rstrip()
                if line:
                    sink.append(line)
        except Exception:
            pass

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, query: str = None, **kwargs) -> Any:
        """forward()            -> the harness card
           forward('fix it')    -> run it here, returning the step trace
        """
        if not query:
            return self.harness()
        return self.run(query,
                        path=kwargs.get("path"),
                        goal=kwargs.get("goal"),
                        model=kwargs.get("model"),
                        timeout=int(kwargs.get("timeout") or DEFAULT_TIMEOUT),
                        on_step=kwargs.get("on_step"))
