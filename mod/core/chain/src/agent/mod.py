"""chain.agent — the chain console's Claude Code harness.

The chain console keeps a builder's projects (contracts/*.sol next to
test/*.test.js) in ~/.mod/chain/build/projects.json and runs their tests on an
in-process EVM. This module hands one of those projects to the Claude Code
CLI: it lays the project out as a real Hardhat workspace, runs the CLI there
with its file tools and `npx hardhat test|compile` and nothing else, and when
the run ends writes whatever changed back into the project — so the console's
BUILD and TEST tabs show the agent's work the moment it finishes.

It is a *harness* in the orbit/agent sense, the same contract the build
module's job server answers (harness "buildmod"):

    harness()                                                -> card
    run(query, path=, goal=, model=, timeout=, on_step=, key=,
        project=, address=, network=)                        -> [step, ...]

orbit/agent's RUNNERS maps "chainmod" here, and its shipped `chain-mod` agent
is a persona over it, so a run reaches this code through the agent module —
its owner gate, task ledger and console — never straight from a browser. The
chain API's POST /agent/run is a bridge to that module, not a runner.

Why spawn the CLI here rather than submit to the build job server: build runs
`--dangerously-skip-permissions` in whatever directory it is pointed at, which
is what an orbit module edit needs. A contract workspace needs the opposite —
edits accepted only inside the project directory and a shell that runs
hardhat and nothing else — and the CLI's own permission rules express that
exactly (`--permission-mode acceptEdits`, `--allowedTools "Bash(npx hardhat:*)"`).

The CLI runs on this host's own Claude account, so orbit/agent keeps harness
runs owner-only; this module additionally caps concurrency.

Usage:
    a = m.mod('chain.agent')()
    a.harness()                                   # {name, available, ...}
    a.run('add a test for transfer()', project='token', address='0x…',
          on_step=print)
    a.runs('0x…')                                 # past runs, newest first
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import mod as m
except ImportError:      # tests import this file straight off the disk
    m = None

HERE = Path(__file__).resolve().parent
MODULE_DIR = HERE.parent.parent                       # core/chain
TEMPLATE = MODULE_DIR / "src" / "build" / "hardhat.template.js"
NODE_MODULES = MODULE_DIR / "node_modules"

BUILD_DIR = Path(os.environ.get("CHAIN_BUILD_DIR") or Path.home() / ".mod" / "chain" / "build")
PROJECTS = "projects.json"
AGENT_DIR = "agent"                                   # BUILD_DIR/agent/{run,runs.json}

HARNESS_TIMEOUT = 1800
DEFAULT_MODEL = os.environ.get("CHAIN_AGENT_MODEL", "sonnet")
MAX_CONCURRENT = int(os.environ.get("CHAIN_AGENT_CONCURRENCY", "2"))
MAX_RESULT = 4000
MAX_FILE = 512 * 1024
DEFAULT_PROJECT = "agent"

# the CLI's tool names, as the fleet's step traces spell them
TOOLS = {
    "Read": "read", "Edit": "edit", "MultiEdit": "edit", "Write": "write",
    "Bash": "bash", "Glob": "glob", "Grep": "grep", "LS": "ls",
    "TodoWrite": "todo", "Task": "agent", "WebFetch": "fetch", "WebSearch": "search",
}
# the params worth carrying on a step; the rest is bulk
PARAM_KEYS = ("file_path", "command", "pattern", "path", "description", "query",
              "old_string", "new_string", "content", "timeout")

_lock = threading.Lock()
_running: Dict[str, dict] = {}


def clip(text: Any, limit: int = MAX_RESULT) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit} more chars]"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def safe_rel(path: str) -> Optional[str]:
    """A project-relative path with no way out of the project; None if it has one."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def layout(files: dict) -> dict:
    """The project on Hardhat's layout — the same rule the API's test runner
    applies, so what the agent sees is what TEST runs."""
    out = {}
    for raw, content in (files or {}).items():
        path = safe_rel(raw)
        if not path:
            continue
        head = path.split("/")[0]
        if path.endswith(".sol") and head != "contracts":
            path = f"contracts/{path}"
        elif path.endswith((".js", ".ts", ".cjs", ".mjs")) and head != "test":
            path = f"test/{path}"
        out[path] = content or ""
    return out


# ── the CLI's stream, as steps ──────────────────────────────────────────────

class Trace:
    """stream-json lines in, the fleet's step dicts out.

    A tool_use opens a step; the tool_result that answers it closes and emits
    it. Prose is held until something follows it — the last thing the agent
    says is the answer, and that belongs on the finish step, not in the trace.
    """

    def __init__(self, root: str = None):
        self._root = (str(root).rstrip("/") + "/") if root else None
        self._open: Dict[str, dict] = {}       # tool_use_id -> step
        self._order: List[str] = []
        self._held: Optional[str] = None
        self.session_id: Optional[str] = None
        self.cost: Optional[float] = None
        self.turns: Optional[int] = None
        self.final: Optional[str] = None
        self.is_error = False
        self.done = False

    @property
    def summary(self) -> str:
        return self.final or self._held or ""

    def line(self, raw: str) -> List[dict]:
        raw = (raw or "").strip()
        if not raw.startswith("{"):
            return []
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return []
        kind = ev.get("type")
        if kind == "system":
            self.session_id = ev.get("session_id") or self.session_id
            return []
        if kind == "assistant":
            return self._assistant(ev)
        if kind == "user":
            return self._user(ev)
        if kind == "result":
            return self._result(ev)
        return []

    def _assistant(self, ev: dict) -> List[dict]:
        out: List[dict] = []
        for block in (ev.get("message") or {}).get("content") or []:
            btype = block.get("type")
            if btype == "text" and (block.get("text") or "").strip():
                out += self._hold(block["text"])
            elif btype == "tool_use":
                out += self._flush()
                name = block.get("name") or "tool"
                params = {k: clip(v, 1200) if isinstance(v, str) else v
                          for k, v in (block.get("input") or {}).items() if k in PARAM_KEYS}
                # paths as the project knows them, not as the host does
                for k in ("file_path", "path"):
                    if self._root and isinstance(params.get(k), str) and params[k].startswith(self._root):
                        params[k] = params[k][len(self._root):]
                if not params and block.get("input"):
                    params = {"detail": clip(block["input"], 600)}
                step = {"tool": TOOLS.get(name, name.lower()), "params": params}
                uid = block.get("id") or f"anon-{len(self._order)}"
                self._open[uid] = step
                self._order.append(uid)
        return out

    def _user(self, ev: dict) -> List[dict]:
        out: List[dict] = []
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") != "tool_result":
                continue
            uid = block.get("tool_use_id")
            step = self._open.pop(uid, None)
            if step is None:
                continue
            self._order.remove(uid)
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content
                                    if isinstance(c, dict) and c.get("type") == "text")
            text = clip(content if content is not None else "")
            if block.get("is_error"):
                step["error"] = text or "tool failed"
            else:
                step["result"] = text
            out.append(step)
        return out

    def _result(self, ev: dict) -> List[dict]:
        self.done = True
        self.session_id = ev.get("session_id") or self.session_id
        self.cost = ev.get("total_cost_usd")
        self.turns = ev.get("num_turns")
        self.is_error = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
        text = ev.get("result")
        if isinstance(text, str) and text.strip():
            self.final = text.strip()
        return []

    def _hold(self, text: str) -> List[dict]:
        out = self._flush()
        self._held = text.strip()
        return out

    def _flush(self) -> List[dict]:
        text, self._held = self._held, None
        return [{"tool": "response", "params": {}, "result": clip(text)}] if text else []

    def close(self, status: str, error: str = None, synced: dict = None) -> List[dict]:
        """Terminal step. Unanswered tool calls are reported, not lost."""
        out: List[dict] = []
        for uid in list(self._order):
            step = self._open.pop(uid)
            step["error"] = error or "run ended before the tool answered"
            out.append(step)
        self._order.clear()
        # narration the agent left before its answer stays in the trace; the
        # answer itself rides on the finish step
        if self._held and self._held != (self.final or ""):
            out += self._flush()
        self._held = None
        if status == "completed" and not self.is_error:
            params = {"summary": self.final or "", "changed": (synced or {}).get("changed", [])}
            if self.cost is not None:
                params["cost_usd"] = self.cost
            if self.turns is not None:
                params["turns"] = self.turns
            return out + [{"tool": "finish", "params": params}]
        return out + [{"tool": "error", "params": {},
                       "error": error or self.final or f"run {status}"}]


# ── the runner ──────────────────────────────────────────────────────────────

class Mod:
    description = "Claude Code over a chain-console project: contracts and tests, in a sandbox"

    def __init__(self, **kwargs):
        self._auth = None

    # ── harness contract ─────────────────────────────────────────────

    def harness(self) -> dict:
        claude = self._find_claude()
        return {
            "name": "chainmod",
            "label": "Chain Console",
            "module": "chain",
            "description": "Claude Code over a chain-console project: it reads and edits the "
                           "contracts and tests, runs `npx hardhat test` in a sandbox, and "
                           "its edits land back in the project",
            "available": bool(claude),
            "cli": claude,
            "version": self.cli_version() if claude else None,
            "install": "npm i -g @anthropic-ai/claude-code && claude  # log in once",
            "model": DEFAULT_MODEL,
            "running": len(_running),
            "concurrency": MAX_CONCURRENT,
        }

    def available(self) -> bool:
        return bool(self._find_claude())

    def run(self, query: str, path: str = None, goal: str = None, model: str = None,
            timeout: int = HARNESS_TIMEOUT, on_step: Callable[[dict], None] = None,
            key=None, project: str = None, address: str = None,
            network: str = "testnet", **kwargs) -> List[dict]:
        """Hand a project to Claude Code and return the run's steps.

        Args:
            query: the task, handed to the CLI as its prompt
            path: ignored — the workspace is derived from the project
            goal: system prompt for the run (appended to the CLI's own)
            model: CLI model alias (sonnet | opus | haiku); default DEFAULT_MODEL
            timeout: wall-clock cap; the CLI is killed when it expires
            on_step: called with each step as it happens
            key: caller identity (protocol-auth token) — whose projects
            project: project name under that address; DEFAULT_PROJECT if none
            address: the project owner, when the caller resolved it already
            network: the console's network — context for the agent, nothing more
        """
        claude = self._find_claude()
        if not claude:
            raise RuntimeError("the claude CLI is not installed on this host")
        who = self._who(address or self._resolve_address(key))
        name = (project or "").strip() or DEFAULT_PROJECT
        run_id = uuid.uuid4().hex[:12]
        query = (query or "").strip() or "look over this project and report what you find"

        with _lock:
            if len(_running) >= MAX_CONCURRENT:
                raise RuntimeError(f"{MAX_CONCURRENT} agent runs are already in flight — try again shortly")
            if any(r["who"] == who and r["project"] == name for r in _running.values()):
                raise RuntimeError(f"an agent run is already working on {name}")
            _running[run_id] = {"who": who, "project": name, "started": time.time()}

        steps: List[dict] = []

        def emit(step: dict):
            step.setdefault("params", {})
            step["run"] = run_id
            steps.append(step)
            if on_step:
                try:
                    on_step(step)
                except Exception:
                    pass

        record = {"id": run_id, "who": who, "project": name, "network": network,
                  "query": query, "model": model or DEFAULT_MODEL, "status": "running",
                  "started": time.time()}
        self._record(record)
        trace = Trace()
        status, error, synced = "failed", None, {}
        try:
            before = self.project_files(who, name)
            root = self.workspace(who, name, before)
            trace = Trace(root)
            emit({"tool": "workspace", "params": {"project": name, "files": sorted(layout(before))},
                  "result": str(root)})
            cmd = self.command(query, model=model, goal=goal,
                              note=self.note(name, network, before))
            proc = self._spawn(claude, cmd, root)
            deadline = time.time() + max(30, int(timeout or HARNESS_TIMEOUT))
            killed = self._follow(proc, trace, emit, deadline)
            if killed:
                error = f"exceeded {int(timeout)}s — the run was stopped"
            elif proc.returncode not in (0, None) and not trace.done:
                error = self._stderr_tail(proc) or f"claude exited {proc.returncode}"
            # whatever the run did to the workspace is the project's now — even
            # a timed-out run's edits, which are on disk and would otherwise vanish
            after = self.collect(root)
            synced = self.sync_back(who, name, before, after)
            if synced["changed"]:
                emit({"tool": "project", "params": {"project": name, **synced},
                      "result": f"{len(synced['changed'])} file(s) written back to {name}"})
            if not error:
                status = "completed"
        except Exception as e:
            error = str(e)
        finally:
            with _lock:
                _running.pop(run_id, None)
        for step in trace.close(status, error, synced):
            emit(step)
        record.update({"status": status if not error else "error", "error": error,
                       "ended": time.time(), "steps": len(steps),
                       "summary": clip(trace.summary, 600), "cost_usd": trace.cost,
                       "changed": synced.get("changed", []), "session_id": trace.session_id})
        self._record(record)
        return steps

    # ── the CLI ──────────────────────────────────────────────────────

    def command(self, query: str, model: str = None, goal: str = None, note: str = None) -> List[str]:
        """The argv, minus the binary. Edits are accepted inside the workspace
        only; the shell runs hardhat and nothing else; no web, no subagents."""
        system = "\n\n".join(s for s in (goal, note) if s)
        cmd = ["--print", "--verbose",
               "--model", (model or DEFAULT_MODEL),
               "--output-format", "stream-json",
               "--permission-mode", "acceptEdits",
               "--tools", "Read,Edit,MultiEdit,Write,Glob,Grep,LS,Bash,TodoWrite",
               "--allowedTools", "Bash(npx hardhat:*)", "Bash(npx hardhat test:*)",
               "Bash(npx hardhat compile:*)", "Bash(npx hardhat clean:*)",
               "--disallowedTools", "WebFetch", "WebSearch", "Task", "NotebookEdit",
               "--strict-mcp-config"]
        if system:
            cmd += ["--append-system-prompt", system]
        cmd += ["-p", query]
        return cmd

    def note(self, project: str, network: str, files: dict) -> str:
        names = sorted(layout(files)) or ["(empty — create contracts/<Name>.sol and test/<Name>.test.js)"]
        return "\n".join([
            "You are working in a chain-console project — a Hardhat workspace with the "
            "builder's Solidity contracts under contracts/ and their Mocha/Chai tests under test/.",
            f"Project: {project}. Console network: {network}. Files:",
            *[f"  - {n}" for n in names],
            "",
            "Rules:",
            "- Read before you write. Keep changes minimal and inside contracts/ and test/.",
            "- `npx hardhat test` runs the suite on an in-process EVM (solc 0.8.26, "
            "@openzeppelin/contracts available); `npx hardhat compile` just compiles. "
            "Those are the only shell commands you have — one plain command at a time: "
            "a pipe, `&&` or a redirect gets the whole command refused.",
            "- Never deploy or send transactions: deploys are signed by the builder's own "
            "wallet in the console. Do not touch hardhat.config.js, package.json or node_modules.",
            "- When you finish, say in a few plain sentences what you changed and what the "
            "tests say — that final message is all the builder sees.",
        ])

    def _spawn(self, claude: str, cmd: List[str], root: Path) -> subprocess.Popen:
        env = {**os.environ, "HOME": os.path.expanduser("~"),
               "HARDHAT_DISABLE_TELEMETRY_PROMPT": "true",
               "PATH": f"{os.environ.get('PATH', '')}:/usr/local/bin:{NODE_MODULES / '.bin'}"}
        # the host's subscription, not a stale inherited key
        if (Path(env["HOME"]) / ".claude" / ".credentials.json").exists():
            env.pop("ANTHROPIC_API_KEY", None)
        base = ["node", claude] if claude.endswith(".js") else [claude]
        return subprocess.Popen(base + cmd, cwd=str(root), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1,
                                start_new_session=True)

    def _follow(self, proc: subprocess.Popen, trace: Trace, emit, deadline: float) -> bool:
        """Read the stream until it ends; kill the process group at the deadline."""
        killed = False
        lines: "list" = []
        done = threading.Event()

        def reader():
            try:
                for line in proc.stdout:
                    lines.append(line)
            finally:
                done.set()

        threading.Thread(target=reader, daemon=True).start()
        seen = 0
        while True:
            while seen < len(lines):
                for step in trace.line(lines[seen]):
                    emit(step)
                seen += 1
            if done.is_set() and seen >= len(lines):
                break
            if time.time() >= deadline:
                killed = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                break
            time.sleep(0.05)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        return killed

    @staticmethod
    def _stderr_tail(proc: subprocess.Popen) -> str:
        try:
            err = proc.stderr.read() if proc.stderr else ""
        except Exception:
            err = ""
        return clip((err or "").strip()[-1500:], 1500)

    def _find_claude(self) -> Optional[str]:
        for cand in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"),
                     "/usr/local/bin/claude", os.path.expanduser("~/.claude/local/claude")):
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return None

    def cli_version(self) -> Optional[str]:
        claude = self._find_claude()
        if not claude:
            return None
        try:
            out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=15)
            return (out.stdout or out.stderr).strip().split("\n")[0] or None
        except Exception:
            return None

    # ── the project ↔ workspace ──────────────────────────────────────

    def _store_path(self) -> Path:
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        return BUILD_DIR / PROJECTS

    def _projects(self) -> dict:
        try:
            return json.loads(self._store_path().read_text())
        except Exception:
            return {}

    def project_files(self, who: str, name: str) -> dict:
        return dict((self._projects().get(who, {}).get(name) or {}).get("files") or {})

    def workspace(self, who: str, name: str, files: dict) -> Path:
        """A Hardhat project the CLI can work in: the project's files on the
        test runner's layout, the module's config and node_modules, and a
        CLAUDE.md so the agent knows the ground rules before its first tool call."""
        if not TEMPLATE.is_file():
            raise RuntimeError("hardhat template missing — the chain module is not built")
        root = BUILD_DIR / AGENT_DIR / "run" / slug(who) / slug(name)
        root.mkdir(parents=True, exist_ok=True)
        # a fresh copy of the project, nothing from the last run
        for entry in root.iterdir():
            if entry.name in ("node_modules", "cache", "artifacts"):
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        modules = root / "node_modules"
        if not modules.exists():
            modules.symlink_to(NODE_MODULES)
        (root / "package.json").write_text('{"name": "chain-agent-workspace", "private": true}\n')
        shutil.copyfile(TEMPLATE, root / "hardhat.config.js")
        (root / ".gitignore").write_text("node_modules\ncache\nartifacts\nmocha.json\n")
        for path, content in layout(files).items():
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        (root / "contracts").mkdir(exist_ok=True)
        (root / "test").mkdir(exist_ok=True)
        (root / "CLAUDE.md").write_text(self.note(name, "-", files) + "\n")
        return root

    def collect(self, root: Path) -> dict:
        """The project's files as they are on disk after the run."""
        out = {}
        for sub in ("contracts", "test"):
            base = root / sub
            if not base.is_dir():
                continue
            for f in sorted(base.rglob("*")):
                if not f.is_file() or f.is_symlink():
                    continue
                if f.stat().st_size > MAX_FILE:
                    continue
                try:
                    out[str(f.relative_to(root))] = f.read_text()
                except UnicodeDecodeError:
                    continue
        return out

    def sync_back(self, who: str, name: str, before: dict, after: dict) -> dict:
        """Write the workspace's files back into the project. Returns the diff."""
        old = layout(before)
        added = sorted(p for p in after if p not in old)
        removed = sorted(p for p in old if p not in after)
        edited = sorted(p for p in after if p in old and old[p] != after[p])
        changed = sorted(set(added) | set(removed) | set(edited))
        if changed or (not old and after):
            store = self._projects()
            store.setdefault(who, {})[name] = {"files": after, "updated": time.time()}
            self._write_json(self._store_path(), store)
        return {"added": added, "removed": removed, "edited": edited, "changed": changed}

    # ── runs ─────────────────────────────────────────────────────────

    def _runs_path(self) -> Path:
        d = BUILD_DIR / AGENT_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d / "runs.json"

    def _record(self, entry: dict):
        with _lock:
            try:
                rows = json.loads(self._runs_path().read_text())
            except Exception:
                rows = []
            rows = [r for r in rows if r.get("id") != entry["id"]]
            rows.insert(0, entry)
            self._write_json(self._runs_path(), rows[:500])

    def runs(self, address: str = None, limit: int = 20) -> List[dict]:
        who = self._who(address)
        try:
            rows = json.loads(self._runs_path().read_text())
        except Exception:
            rows = []
        rows = [r for r in rows if r.get("who") == who]
        live = {rid for rid in _running}
        for r in rows:
            if r.get("status") == "running" and r.get("id") not in live:
                r["status"] = "lost"      # the process that ran it is gone
        return rows[:max(1, min(int(limit or 20), 200))]

    def running(self) -> List[dict]:
        with _lock:
            return [{"id": k, **v} for k, v in _running.items()]

    @staticmethod
    def _write_json(path: Path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)

    # ── identity ─────────────────────────────────────────────────────

    @staticmethod
    def _who(address: Optional[str]) -> str:
        return (address or "anon").strip().lower() or "anon"

    def _resolve_address(self, key) -> Optional[str]:
        """The address behind a key: a key object, a signed token, or a bare
        address. Same reading orbit/agent gives its callers."""
        if key is None:
            return None
        if hasattr(key, "address"):
            return key.address
        token = str(key)
        if token.startswith("0x") and len(token) == 42:
            return token
        if m is not None:
            try:
                if self._auth is None:
                    self._auth = m.mod("auth")()
                return self._auth.verify(token).get("key")
            except Exception:
                return None
        return None

    # ── mod protocol ─────────────────────────────────────────────────

    def forward(self, action: str = "harness", **kwargs) -> Any:
        if action == "harness":
            return self.harness()
        if action == "runs":
            return self.runs(kwargs.get("address"), kwargs.get("limit", 20))
        if action == "running":
            return self.running()
        if action == "run":
            return self.run(kwargs.pop("query", ""), **kwargs)
        raise ValueError(f"unknown action: {action} (harness | runs | running | run)")
