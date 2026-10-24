"""
An open-source agent from GitHub, as a module.

This file is the same in every module orbit/agent's hub generates. Nothing in
it names an agent: what to install, which binary to run and how to hand it a
task all live in config.json under "agent", so the module describes itself and
keeps working if orbit/agent is gone.

    config.json["agent"] = {
        "repo":    "Aider-AI/aider",
        "install": {"kind": "pip", "packages": ["aider-chat"]},
                   # pip | npm | script | git — see setup()
        "bin":     "aider",
        "run":     ["{bin}", "--message", "{query}", "--yes-always"],
        "model":   ["--model", "{model}"],     # dropped when no model is given
        "env":     ["OPENAI_API_KEY", "OPENAI_API_BASE"],  # the keys it reads
    }

Two contracts, both the fleet's own:

    harness()  run(query, path=, goal=, model=, timeout=, on_step=)
        what orbit/agent's Harness calls — the same pair claudecode/codexcli
        answer, so a persona with harness=<name> hands its run here
    agents()   run_stream(query, ...)
        GET /agents + POST /run/stream (api.py) — what orbit/build probes
        before it mounts a module as an agent backend

Everything the agent installs and every secret it is given stays off-tree in
~/.mod/<name>/ (venv, npm prefix, binaries, env.json 0600, workspace), so the
module directory is safe to publish.

Usage:
    import mod as m
    a = m.mod('<name>')()
    a.harness()                     # installed here?
    a.setup()                       # install it into ~/.mod/<name>/
    a.run('add a --verbose flag', path='/repo', on_step=print)
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

HERE = Path(__file__).resolve().parent

# one trace row is something a console renders, not a log file
MAX_RESULT = 4000
# wall-clock cap on one run; the process group is killed when it expires
DEFAULT_TIMEOUT = 1800
# output is flushed as one step after this much quiet, so a chatty CLI reads
# as paragraphs rather than one row per line
FLUSH_SECONDS = 1.5
# lines of the tail that become the run's answer when the CLI has no
# structured "final message" of its own
SUMMARY_LINES = 40
# ANSI escapes and carriage-return redraws — CLIs that think they own a
# terminal still emit some even with colour off
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")


def clip(text: Any, limit: int = MAX_RESULT) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + f"\n... [{len(s) - limit} more chars]"


def load_config() -> Dict[str, Any]:
    return json.loads((HERE / "config.json").read_text())


class Mod:
    description = "An open-source GitHub agent, run as a module on this host"

    def __init__(self, **kwargs):
        self.config = load_config()
        self.name = self.config["name"]
        self.spec: Dict[str, Any] = self.config.get("agent") or {}
        self.label = self.spec.get("label") or self.name
        self.bin = self.spec.get("bin") or ""
        self.state = Path(os.path.expanduser(f"~/.mod/{self.name}"))
        self._setup_lock = threading.Lock()
        self._setup: Dict[str, Any] = {"state": "idle", "log": []}

    # ── where the agent lives ────────────────────────────────────────

    def bin_dirs(self) -> List[Path]:
        """This module's own install prefixes, searched before PATH."""
        return [self.state / "venv" / "bin",
                self.state / "npm" / "node_modules" / ".bin",
                self.state / "bin"]

    def path(self) -> Optional[str]:
        """The agent's binary, or None when it isn't installed here."""
        if not self.bin:
            return None
        for d in self.bin_dirs():
            p = d / self.bin
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        return shutil.which(self.bin)

    def available(self) -> bool:
        return bool(self.path()) and bool(self.spec.get("run"))

    def install_hint(self) -> str:
        inst = self.spec.get("install") or {}
        kind = inst.get("kind")
        pkgs = " ".join(inst.get("packages") or [])
        if kind == "pip":
            return f"pip install {pkgs}"
        if kind == "npm":
            return f"npm install -g {pkgs}"
        if kind == "script":
            return inst.get("command", "")
        return f"git clone https://github.com/{self.spec.get('repo', '')}"

    # ── secrets: env.json, off-tree, 0600, names only ever leave ─────

    def _env_file(self) -> Path:
        return self.state / "env.json"

    def env(self) -> Dict[str, str]:
        try:
            got = json.loads(self._env_file().read_text())
            return {str(k): str(v) for k, v in got.items() if v not in (None, "")}
        except (OSError, ValueError):
            return {}

    def env_keys(self) -> Dict[str, Any]:
        """Which keys the agent reads, and which are set — never the values."""
        have = self.env()
        wanted = list(dict.fromkeys(list(self.spec.get("env") or []) + list(have)))
        return {"keys": [{"name": k, "set": k in have or bool(os.environ.get(k)),
                          "source": "module" if k in have else
                                    ("host" if os.environ.get(k) else None)}
                         for k in wanted]}

    def set_env(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Merge keys into env.json. An empty value removes the key."""
        cur = self.env()
        for k, v in (values or {}).items():
            k = str(k).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                raise ValueError(f"not an environment variable name: {k!r}")
            if v in (None, ""):
                cur.pop(k, None)
            else:
                cur[k] = str(v)
        self.state.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._env_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cur, f, indent=1)
        return self.env_keys()

    def child_env(self, extra: Dict[str, str] = None) -> Dict[str, str]:
        env = dict(os.environ)
        # never hand a child agent this host's own agent-session plumbing
        for k in list(env):
            if k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE":
                env.pop(k)
        env.update(self.env())
        env.update(extra or {})
        env["PATH"] = os.pathsep.join([str(d) for d in self.bin_dirs()] + [env.get("PATH", "")])
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", "dumb")
        env.setdefault("CI", "1")
        return env

    # ── install ──────────────────────────────────────────────────────

    def setup_commands(self) -> List[List[str]]:
        """The argv list setup() would run, without running it."""
        inst = self.spec.get("install") or {}
        kind = inst.get("kind")
        pkgs = list(inst.get("packages") or [])
        s = str(self.state)
        if kind == "pip":
            return [["python3", "-m", "venv", f"{s}/venv"],
                    [f"{s}/venv/bin/pip", "install", "--upgrade", "pip"],
                    [f"{s}/venv/bin/pip", "install", *pkgs]]
        if kind == "npm":
            return [["npm", "install", "--no-fund", "--no-audit", "--prefix", f"{s}/npm", *pkgs]]
        if kind == "script":
            return [["bash", "-c", inst["command"]]]
        repo = self.spec.get("repo")
        if not repo:
            return []
        cmds = [["git", "clone", "--depth", "1", f"https://github.com/{repo}", f"{s}/src"]]
        if inst.get("command"):
            cmds.append(["bash", "-c", f"cd {s}/src && {inst['command']}"])
        return cmds

    def setup(self, wait: bool = True) -> Dict[str, Any]:
        """Install the agent into ~/.mod/<name>/ — never system-wide.

        pip  -> a venv of its own; npm -> its own prefix; script -> the
        project's installer with BIN_DIR/GOOSE_BIN_DIR pointed at our bin/;
        git  -> a shallow clone (and its own build command, if any).
        """
        if not self._setup_lock.acquire(blocking=False):
            return self.setup_status()
        self._setup = {"state": "running", "log": [], "started": time.time()}

        def work():
            try:
                (self.state / "bin").mkdir(parents=True, exist_ok=True)
                env = self.child_env({"BIN_DIR": str(self.state / "bin"),
                                      "GOOSE_BIN_DIR": str(self.state / "bin"),
                                      "CONFIGURE": "false"})
                cmds = self.setup_commands()
                if not cmds:
                    raise RuntimeError("no install recipe and no repo to clone")
                for cmd in cmds:
                    if cmd[:2] == ["git", "clone"] and Path(cmd[-1]).exists():
                        self._log(f"$ (already cloned: {cmd[-1]})")
                        continue
                    self._log("$ " + " ".join(cmd))
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            env=env, cwd=str(self.state))
                    for line in proc.stdout:
                        self._log(line.rstrip())
                    if proc.wait() != 0:
                        raise RuntimeError(f"exit {proc.returncode}: {' '.join(cmd)}")
                ok = self.available() or not self.spec.get("run")
                self._setup.update(state="done" if ok else "failed",
                                   error=None if ok else f"installed, but `{self.bin}` is not on any path")
            except Exception as e:
                self._log(f"! {e}")
                self._setup.update(state="failed", error=str(e))
            finally:
                self._setup["ended"] = time.time()
                self._setup_lock.release()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        if wait:
            t.join()
        return self.setup_status()

    def _log(self, line: str):
        log = self._setup.setdefault("log", [])
        log.append(ANSI.sub("", line)[:500])
        del log[:-400]

    def setup_status(self) -> Dict[str, Any]:
        return {**{k: v for k, v in self._setup.items() if k != "log"},
                "log": self._setup.get("log", [])[-120:],
                "available": self.available(), "path": self.path(),
                "commands": [" ".join(c) for c in self.setup_commands()]}

    # ── the run ──────────────────────────────────────────────────────

    def command(self, query: str, goal: str = None, model: str = None) -> List[str]:
        """argv for one headless run. {bin} {query} {model} {goal} are filled;
        the model pair is dropped when there is no model, and a goal the CLI
        has no flag for is folded into the prompt."""
        run = list(self.spec.get("run") or [])
        if not run:
            raise RuntimeError(
                f"{self.label} has no headless run command yet — set agent.run in "
                f"{HERE / 'config.json'} (e.g. [\"{{bin}}\", \"--message\", \"{{query}}\"])")
        goal_flag = list(self.spec.get("goal") or [])
        if goal and not goal_flag:
            query = f"{goal.strip()}\n\n{query}"
        fill = {"bin": self.path() or self.bin, "query": query,
                "model": model or "", "goal": goal or ""}
        argv = [a.format(**fill) for a in run]
        if model and self.spec.get("model"):
            argv += [a.format(**fill) for a in self.spec["model"]]
        if goal and goal_flag:
            argv += [a.format(**fill) for a in goal_flag]
        return argv

    def run(self, query: str, path: str = None, goal: str = None,
            model: str = None, timeout: int = DEFAULT_TIMEOUT,
            on_step: Callable[[dict], None] = None, **kwargs) -> List[dict]:
        """Run the agent to completion and return its steps.

        Output is plain text from a CLI, so steps are paragraphs: a `response`
        step whenever the stream goes quiet for FLUSH_SECONDS, then `finish`
        with the tail as its summary. A non-zero exit is an error step, never
        a silent success.
        """
        if not self.path():
            raise RuntimeError(f"{self.label} is not installed on this host. "
                               f"Install it from this module's page, or: {self.install_hint()}")
        cwd = Path(path).expanduser() if path else self.state / "workspace"
        if not path:
            cwd.mkdir(parents=True, exist_ok=True)
        if not cwd.is_dir():
            raise NotADirectoryError(f"working directory does not exist: {cwd}")
        argv = self.command(query, goal=goal, model=model)

        steps: List[dict] = []
        tail: deque = deque(maxlen=SUMMARY_LINES)
        buf: List[str] = []
        lock = threading.Lock()

        def emit(step: dict):
            steps.append(step)
            if on_step:
                try:
                    on_step(step)
                except Exception:
                    pass

        def flush():
            with lock:
                text = "\n".join(buf).strip()
                buf.clear()
            if text:
                emit({"tool": "response", "params": {}, "result": clip(text)})

        emit({"tool": "bash", "params": {"command": " ".join(argv[:1] + ["..."]),
                                         "cwd": str(cwd)},
              "result": f"{self.label} started"})
        started = time.time()
        proc = subprocess.Popen(argv, cwd=str(cwd), env=self.child_env(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                start_new_session=True)
        last = [time.time()]
        done = threading.Event()

        def ticker():
            while not done.wait(0.5):
                if buf and time.time() - last[0] > FLUSH_SECONDS:
                    flush()

        threading.Thread(target=ticker, daemon=True).start()
        killer = threading.Timer(timeout, lambda: self._kill(proc))
        killer.start()
        try:
            for raw in proc.stdout:
                line = ANSI.sub("", raw.rstrip("\n"))
                with lock:
                    buf.append(line)
                tail.append(line)
                last[0] = time.time()
            code = proc.wait()
        finally:
            killer.cancel()
            done.set()
        flush()
        elapsed = round(time.time() - started, 1)
        summary = "\n".join(tail).strip()
        if code != 0:
            why = "timed out" if elapsed >= timeout else f"exited {code}"
            emit({"tool": "finish", "params": {"summary": summary},
                  "error": f"{self.label} {why} after {elapsed}s"})
        else:
            emit({"tool": "finish", "params": {"summary": summary, "elapsed": elapsed}})
        return steps

    @staticmethod
    def _kill(proc: subprocess.Popen):
        try:
            os.killpg(proc.pid, 9)
        except (OSError, ProcessLookupError):
            pass

    def run_stream(self, query: str, **kwargs) -> Iterator[Dict[str, Any]]:
        """The same run as events — model_start, step, done|error — which is
        what POST /run/stream drains and orbit/build already renders."""
        events: "queue.Queue" = queue.Queue()
        task_id = uuid.uuid4().hex[:12]
        started = time.time()

        def worker():
            try:
                events.put({"type": "model_start", "step": 0,
                            "model": kwargs.get("model") or self.name})
                steps = self.run(query, on_step=lambda s: events.put({"type": "step", "step": s}),
                                 **kwargs)
                last = steps[-1] if steps else {}
                result = (last.get("params") or {}).get("summary", "")
                if last.get("error"):
                    events.put({"type": "error", "error": last["error"], "result": result,
                                "task_id": task_id})
                else:
                    events.put({"type": "done", "result": result, "task_id": task_id,
                                "free": True, "duration": round(time.time() - started, 2)})
            except Exception as e:
                events.put({"type": "error", "error": str(e), "task_id": task_id})
            finally:
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            ev = events.get()
            if ev is None:
                return
            yield ev

    # ── what the fleet reads ─────────────────────────────────────────

    def harness(self) -> Dict[str, Any]:
        """orbit/agent's Harness card."""
        return {"name": (self.config.get("harness") or {}).get("name", self.name),
                "label": self.label, "bin": self.bin, "module": self.name,
                "description": self.config.get("description", ""),
                "install": self.install_hint(), "available": self.available(),
                "path": self.path(), "repo": self.spec.get("repo")}

    def agents(self) -> Dict[str, Any]:
        """GET /agents — the probe orbit/build mounts a module by."""
        return {"agents": [{"id": self.name, "name": self.label,
                            "description": self.config.get("description", ""),
                            "icon": self.config.get("icon", ">_"),
                            "repo": self.spec.get("repo"), "provider": self.name,
                            "local": True, "available": self.available()}],
                "default": self.name}

    def readme(self, fresh: bool = False) -> Dict[str, Any]:
        """The upstream README, cached a day off-tree."""
        repo = self.spec.get("repo")
        cache = self.state / "readme.md"
        if cache.exists() and not fresh and time.time() - cache.stat().st_mtime < 86400:
            return {"repo": repo, "markdown": cache.read_text(), "cached": True}
        local = self.state / "src" / "README.md"
        if local.exists():
            return {"repo": repo, "markdown": local.read_text()[:200_000], "cached": True}
        if not repo:
            return {"repo": None, "markdown": self.config.get("description", "")}
        for branch in ("HEAD", "main", "master"):
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/README.md"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mod-agent-module"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    text = r.read().decode("utf-8", "replace")[:200_000]
                self.state.mkdir(parents=True, exist_ok=True)
                cache.write_text(text)
                return {"repo": repo, "markdown": text, "cached": False}
            except Exception:
                continue
        return {"repo": repo, "markdown": self.config.get("description", ""), "error": "README unreachable"}

    def info(self) -> Dict[str, Any]:
        spec = {k: v for k, v in self.spec.items() if k != "env"}
        return {"name": self.name, "label": self.label,
                "description": self.config.get("description", ""),
                "icon": self.config.get("icon", ">_"), "color": self.config.get("color"),
                "port": self.config.get("port"), "owner": self.config.get("owner"),
                "agent": spec, "harness": self.harness(), "env": self.env_keys(),
                "setup": {k: v for k, v in self.setup_status().items() if k != "log"},
                "workspace": str(self.state / "workspace")}

    def set_run(self, run: List[str], model: List[str] = None) -> Dict[str, Any]:
        """Owner: teach a repo-only agent its headless command. Written to
        config.json because it is the module's description, not a secret."""
        if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
            raise ValueError('run must be a non-empty list of strings, e.g. ["{bin}", "-p", "{query}"]')
        if not any("{query}" in a for a in run):
            raise ValueError("run must contain {query} somewhere — that is where the task goes")
        cfg = load_config()
        cfg.setdefault("agent", {})["run"] = run
        if model is not None:
            cfg["agent"]["model"] = list(model)
        if not cfg["agent"].get("bin"):
            cfg["agent"]["bin"] = run[0] if run[0] != "{bin}" else ""
        (HERE / "config.json").write_text(json.dumps(cfg, indent=4) + "\n")
        self.__init__()
        return self.info()

    # ── process ──────────────────────────────────────────────────────

    def serve(self, port: int = None) -> Dict[str, Any]:
        """Start api.py (interface + API on one port) under pm2."""
        port = int(port or self.config.get("port"))
        proc = f"{self.name}-api"
        subprocess.run(["pm2", "delete", proc], capture_output=True)
        out = subprocess.run(["pm2", "start", str(HERE / "_serve.sh"), "--name", proc,
                              "--cwd", str(HERE)], capture_output=True, text=True,
                             env={**os.environ, "PORT": str(port)})
        return {"name": proc, "port": port, "ok": out.returncode == 0,
                "url": f"http://127.0.0.1:{port}/",
                **({"error": out.stderr[-500:]} if out.returncode else {})}

    def kill(self) -> Dict[str, Any]:
        out = subprocess.run(["pm2", "delete", f"{self.name}-api"], capture_output=True, text=True)
        return {"name": f"{self.name}-api", "stopped": out.returncode == 0}

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "module": self.name, "available": self.available(),
                "installed": bool(self.path()), "runnable": bool(self.spec.get("run"))}

    def forward(self, query: str = None, **kwargs) -> Any:
        """m <name> 'task'  -> run it;  m <name>  -> its card."""
        if query:
            return self.run(query, **kwargs)
        return self.info()

    def test(self) -> Dict[str, Any]:
        assert self.config["name"] == self.name
        argv = self.command("hello", model="m1") if self.spec.get("run") else []
        assert not argv or "hello" in " ".join(argv)
        assert self.agents()["agents"][0]["id"] == self.name
        return {"ok": True, "argv": argv, "available": self.available()}
