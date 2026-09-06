"""
claudecode - the Claude Code CLI, as a module.

Claude Code brings its own tools, sandbox and models. This module is the thin
layer that drives it from the orbit: it spawns the binary in --print mode with
a stream-json event feed, and translates that feed into the step dicts the rest
of the fleet speaks ({'tool', 'params', 'result'|'error'}, ending in finish).

It is a *harness*: any module that wants to hand a whole run to Claude Code
calls two functions here, and never learns a CLI flag.

    harness()                      -> {name, label, available, install, ...}
    run(query, path=, goal=, ...)  -> [step, ...]

The CLI runs with its approval prompts off — there is no human at the other end
of a server-side run — so it does whatever the prompt says on this host. Gating
who may call this belongs to the caller (see orbit/agent).

Usage:
    import mod as m
    cc = m.mod('claudecode')()
    cc.harness()                                   # is the binary here?
    cc.run('fix the failing test', path='/repo', on_step=print)
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
    """One run's translation state: CLI events in, step dicts out.

    Per-run, not per-module — two runs translate their own streams without
    stepping on each other's held narration or pending tool calls.
    """

    # CLI tool names -> the fleet's skill names, so a harness trace reads like
    # a native one (unknown tools just lowercase)
    TOOLS = {
        "Bash": "bash", "Read": "read", "Write": "write", "Edit": "edit",
        "MultiEdit": "edit", "NotebookEdit": "edit", "Glob": "glob",
        "Grep": "grep", "Task": "task", "WebFetch": "fetch",
        "WebSearch": "search", "TodoWrite": "todo",
    }

    def __init__(self):
        self._held: Optional[str] = None      # narration waiting to be flushed
        self._done = False                    # a terminal step was already emitted
        self._pending: Dict[str, dict] = {}   # tool_use id -> step awaiting its result
        self.model: Optional[str] = None      # what the CLI says it is running
        self.usage: Dict[str, Any] = {}       # the run's exact token/cost report

    # ── narration buffer ─────────────────────────────────────────────
    # An agent's prose is only a narration step once something follows it: the
    # LAST message of a run is the answer, and that belongs in finish, not in
    # the trace. So text is held, and flushed only when a step or another
    # message arrives.

    def hold(self, text: str) -> List[dict]:
        out = self.flush()
        self._held = (text or "").strip() or None
        return out

    def flush(self) -> List[dict]:
        text, self._held = self._held, None
        return [{"tool": "response", "params": {}, "result": text}] if text else []

    # ── event translation ────────────────────────────────────────────

    def steps(self, event: dict) -> List[dict]:
        """Translate one CLI event into zero or more steps."""
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.model = event.get("model") or self.model
            return []
        if kind == "assistant":
            out: List[dict] = []
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    out += self.hold(block.get("text") or "")
                elif block.get("type") == "tool_use":
                    # the step itself is emitted when its result lands
                    out += self.flush()
                    self._pending[block.get("id")] = {
                        "tool": self.TOOLS.get(block.get("name"),
                                               str(block.get("name") or "tool").lower()),
                        "params": block.get("input") or {},
                    }
            return out
        if kind == "user":
            out = []
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                step = self._pending.pop(block.get("tool_use_id"), None) \
                    or {"tool": "tool", "params": {}}
                text = self._result_text(block.get("content"))
                if block.get("is_error"):
                    step["error"] = clip(text)
                else:
                    step["result"] = clip(text)
                out.append(step)
            return out
        if kind == "result":
            self._done = True
            self._held = None        # the result IS the last assistant message
            text = str(event.get("result") or "").strip()
            # the result event is the one place the CLI reports what the run
            # actually cost — exact token counts and USD, not an estimate —
            # so it rides on the terminal step for whoever meters the run
            self.usage = self._usage_of(event)
            params: Dict[str, Any] = {"usage": self.usage} if self.usage else {}
            if event.get("is_error") or event.get("subtype") not in (None, "success"):
                return [{"tool": "error", "params": params,
                         "error": text or f"claude: {event.get('subtype') or 'failed'}"}]
            return [{"tool": "finish", "params": dict(params, summary=text)}]
        return []

    def _usage_of(self, event: dict) -> Dict[str, Any]:
        """The run's own bill, in the fleet's field names. Never raises."""
        try:
            u = event.get("usage") or {}
            inp = int(u.get("input_tokens") or 0)
            cc = int(u.get("cache_creation_input_tokens") or 0)
            cr = int(u.get("cache_read_input_tokens") or 0)
            out = int(u.get("output_tokens") or 0)
            if not (inp or cc or cr or out):
                return {}
            return {
                # prompt = everything the API processed, cached or not; the
                # cache split is kept so a meter can price the tiers apart
                "prompt_tokens": inp + cc + cr,
                "completion_tokens": out,
                "input_tokens": inp,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr,
                "cost": round(float(event.get("total_cost_usd") or 0.0), 6),
                "turns": int(event.get("num_turns") or 0),
                "duration_ms": int(event.get("duration_ms") or 0),
                "model": self.model,
                "provider": "claude-code",
            }
        except Exception:
            return {}

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
                 "error": f"Claude Code exited with no result (code {code})"
                          + (f":\n{clip(tail, 800)}" if tail else "")}]

    @staticmethod
    def _result_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(c.get("text", "") for c in content
                             if isinstance(c, dict) and c.get("type") == "text")
        return "" if content is None else str(content)


class Mod:
    description = "Claude Code CLI as a harness — hand it a run, get back a step trace"

    # what the fleet knows this harness by
    name = "claude"
    label = "Claude Code"
    bin = "claude"
    install = "npm install -g @anthropic-ai/claude-code"

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
                "description": "Anthropic's Claude Code CLI — its own tools, sandbox and models",
                "install": self.install, "available": bool(p), "path": p,
                "module": "claudecode"}

    # ── the run ──────────────────────────────────────────────────────

    def session(self) -> Session:
        """A fresh translator for one run's event stream."""
        return Session()

    def command(self, query: str, goal: str = None, model: str = None,
                path: str = None) -> List[str]:
        """The argv this harness runs. `path` is the cwd, not a flag."""
        cmd = [self.bin, "--print", "--verbose",
               "--output-format", "stream-json",
               # no human is watching a server-side run to answer prompts
               "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        if goal:
            cmd += ["--append-system-prompt", goal]
        cmd.append(query)
        return cmd

    def run(self, query: str, path: str = None, goal: str = None,
            model: str = None, timeout: int = DEFAULT_TIMEOUT,
            on_step: Callable[[dict], None] = None,
            env: Dict[str, str] = None, **kwargs) -> List[dict]:
        """Run the CLI to completion, returning its steps.

        Args:
            query: the task, handed to the CLI as its prompt
            path: working directory for the run (default: cwd)
            goal: system prompt, appended to the CLI's own
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
