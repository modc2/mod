"""
agent_base.py — off-chain "agent contract" template.

Every agent module is a CONTRACT: a class with declared methods, persistent
state, an owner address, and a code hash. The class IS the source of truth
for that agent's execution logic — same shape as a Solidity contract, but
running off-chain against a CLI backend instead of the EVM.

How it maps:

    Solidity                 AgentContract
    --------------------     -----------------------------
    bytecode hash            code_hash (sha3 of class source)
    constructor              __init__
    public/external methods  @view / @tx decorated methods
    storage                  state dict (persisted to ~/.mod/<name>/state.json)
    events                   emit(event_name, **fields)
    msg.sender               key argument on @tx methods
    onlyOwner                @owner_only or self.require_owner()
    immutable                class constants (NAME, BINARY, …)
    ABI                      abi() (auto-derived from decorators)

To add a new agent: drop a 20-line subclass declaring NAME, BINARY,
DEFAULT_MODEL, ENV_KEY, override build_args() if your CLI takes different
flags. The dev console will route to it automatically.
"""

import hashlib
import inspect
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import mod as m


# ── Per-user × per-tool workspaces ────────────────────────────────────
# Layout: ~/.mod/workspaces/<addr-lowercase>/<tool>/
#   workspace/      job cwds + file-tree root for that user
#   jobs.db         per-user-per-tool job log
#   manifest.json   {root_cid, registered_at, prev_cid} linked-list
# Shared by claude/codex/cursor/any future coding-tool module so paths
# are sandboxed and one user never sees another's files. Override the
# root via MOD_WORKSPACES_ROOT for container bind-mounts.

USERSPACE_ROOT = Path(os.path.expanduser(
    os.environ.get("MOD_WORKSPACES_ROOT", "~/.mod/workspaces")
))


def _norm_addr(addr: str) -> str:
    a = (addr or "").strip().lower()
    if not (a.startswith("0x") and len(a) == 42
            and all(c in "0123456789abcdef" for c in a[2:])):
        raise ValueError(f"invalid address: {addr!r}")
    return a


def user_root(addr: str, tool: str) -> Path:
    """Per-user, per-tool root dir."""
    return USERSPACE_ROOT / _norm_addr(addr) / tool


def user_workspace(addr: str, tool: str) -> Path:
    """Sandboxed filesystem area; everything path-bound resolves under here."""
    return user_root(addr, tool) / "workspace"


def ensure_user_dirs(addr: str, tool: str) -> Path:
    """Idempotent mkdir -p; returns the workspace dir."""
    ws = user_workspace(addr, tool)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def resolve_user_path(addr: str, tool: str, rel: str) -> Path:
    """Join + canonicalize + assert containment. Raises PermissionError on
    `..` escape or symlink-traversal out of the workspace."""
    ws = ensure_user_dirs(addr, tool).resolve()
    rel_clean = (rel or "").lstrip("/")
    candidate = (ws / rel_clean)
    # Canonicalize; if leaf doesn't exist yet (writes), canonicalize parent.
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        parent = candidate.parent
        parent_canon = parent.resolve()
        resolved = parent_canon / candidate.name
    try:
        resolved.relative_to(ws)
    except ValueError:
        raise PermissionError(
            f"path escapes workspace: {resolved} not under {ws}"
        )
    return resolved


# ── Decorators — mark methods as part of the agent's "ABI" ───────────

def view(fn: Callable) -> Callable:
    """Marks a read-only method. Like Solidity `view`. No state mutation."""
    fn._abi_kind = "view"
    return fn


def tx(fn: Callable) -> Callable:
    """Marks a state-mutating method. Like Solidity external/payable.
    The first kwarg `key` is the caller's identity (msg.sender)."""
    fn._abi_kind = "tx"
    return fn


def owner_only(fn: Callable) -> Callable:
    """Mark a tx method as owner-restricted. Enforced at call time via
    self.require_owner(). Use on top of @tx."""
    original = fn

    def wrapper(self, *args, **kwargs):
        self.require_owner(kwargs.get("key"))
        return original(self, *args, **kwargs)

    wrapper._abi_kind = getattr(fn, "_abi_kind", "tx")
    wrapper._owner_only = True
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ── The contract base class ─────────────────────────────────────────

class AgentContract:
    # ── Immutables (declare in subclass; same role as Solidity constants) ─
    NAME: str = ""
    ICON: str = ""
    COLOR: str = "#888888"
    BINARY: str = ""
    DEFAULT_MODEL: str = ""
    ENV_KEY: str = ""
    DESCRIPTION: str = "agent (override DESCRIPTION)"
    DANGEROUS_FLAG: Optional[str] = None

    # ── Constructor — same role as Solidity constructor ────────────
    def __init__(self):
        self._path = self._discover_path()
        self._cfg = self._load_config()
        self._owner = (self._cfg.get("owner") or "").lower()
        self._events: List[dict] = []
        self.state = self._load_state()

    # ── Code hash — sha3 of this class's source (the agent's bytecode hash)
    @classmethod
    def code_hash(cls) -> str:
        # Try the dynamic-import path first: it has __file__ on the module.
        try:
            mod_file = inspect.getfile(cls)
            return "0x" + hashlib.sha3_256(Path(mod_file).read_bytes()).hexdigest()
        except (TypeError, OSError):
            pass
        try:
            src = inspect.getsource(cls)
            return "0x" + hashlib.sha3_256(src.encode()).hexdigest()
        except (OSError, TypeError):
            return "0x" + hashlib.sha3_256(cls.__name__.encode()).hexdigest()

    # ── ABI — auto-derived from decorated methods ──────────────────
    @classmethod
    def abi(cls) -> List[dict]:
        out = []
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            kind = getattr(member, "_abi_kind", None)
            if kind is None:
                continue
            sig = inspect.signature(member)
            inputs = []
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                inputs.append({
                    "name": pname,
                    "type": str(p.annotation) if p.annotation is not inspect._empty else "any",
                    "default": None if p.default is inspect._empty else repr(p.default),
                })
            out.append({
                "name": name,
                "kind": kind,
                "owner_only": getattr(member, "_owner_only", False),
                "inputs": inputs,
                "doc": (member.__doc__ or "").strip().split("\n")[0],
            })
        return sorted(out, key=lambda x: (x["kind"], x["name"]))

    # ── Storage — like contract storage slots, persisted to disk ───
    def _storage_path(self) -> Path:
        d = Path(os.path.expanduser(f"~/.mod/{self.NAME}"))
        d.mkdir(parents=True, exist_ok=True)
        return d / "state.json"

    def _load_state(self) -> dict:
        p = self._storage_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except json.JSONDecodeError:
                pass
        return {"created_at": int(time.time()), "jobs_submitted": 0, "events": 0}

    def _save_state(self):
        self._storage_path().write_text(json.dumps(self.state, indent=2))

    # ── Events — like Solidity emit ────────────────────────────────
    def emit(self, name: str, **fields):
        evt = {"event": name, "ts": int(time.time()), "fields": fields}
        self._events.append(evt)
        self.state.setdefault("events", 0)
        self.state["events"] += 1
        log_path = Path(os.path.expanduser(f"~/.mod/{self.NAME}/events.jsonl"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(evt) + "\n")

    # ── Owner gate — like Solidity onlyOwner ───────────────────────
    def require_owner(self, key=None):
        if not self._owner:
            return
        try:
            if key is None:
                addr = m.key().address.lower()
            elif hasattr(key, "address"):
                addr = key.address.lower()
            elif isinstance(key, str) and key.startswith("0x") and len(key) in (42, 66):
                addr = key.lower()
            else:
                addr = m.key(key).address.lower()
        except Exception as e:
            raise PermissionError(f"caller resolution failed: {e}")
        if addr != self._owner:
            raise PermissionError(f"owner-only — only {self._owner} may call this (got {addr})")

    # ── View methods (no state mutation) ───────────────────────────
    @view
    def info(self) -> dict:
        return {
            "name": self.NAME,
            "icon": self.ICON,
            "color": self.COLOR,
            "binary": self.BINARY,
            "description": self.DESCRIPTION,
            "owner": self._owner,
            "port": self._cfg.get("port"),
            "default_model": self.DEFAULT_MODEL,
            "env_key": self.ENV_KEY,
            "code_hash": self.code_hash(),
            "abi_method_count": len(self.abi()),
        }

    @view
    def forward(self, **kwargs):
        return self.info()

    @view
    def health(self) -> dict:
        port = self._cfg.get("port")
        if not port:
            return {"service": self.NAME, "status": "no port configured"}
        try:
            import urllib.request as r
            with r.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"service": self.NAME, "status": "down", "error": str(e)}

    @view
    def cli_path(self) -> Optional[str]:
        if not self.BINARY:
            return None
        result = subprocess.run(["which", self.BINARY], capture_output=True, text=True)
        return result.stdout.strip() or None

    @view
    def cid(self) -> str:
        try:
            return m.cid(self.NAME)
        except Exception:
            return ""

    @view
    def jobs(self) -> List[dict]:
        port = self._cfg.get("port")
        if not port:
            return []
        import urllib.request as r
        try:
            with r.urlopen(f"http://127.0.0.1:{port}/jobs", timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return []

    @view
    def events(self, limit: int = 50) -> List[dict]:
        p = Path(os.path.expanduser(f"~/.mod/{self.NAME}/events.jsonl"))
        if not p.exists():
            return []
        with p.open() as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l) for l in lines]

    # ── Subclass hook: build the argv for one prompt ───────────────
    # NOT decorated — it's an internal helper, not part of the public ABI.
    def build_args(self, prompt: str, model: str, work_dir: str) -> List[str]:
        args = []
        if self.DEFAULT_MODEL:
            args += ["--model", model or self.DEFAULT_MODEL]
        if self.DANGEROUS_FLAG:
            args.append(self.DANGEROUS_FLAG)
        args.append(prompt)
        return args

    # ── TX methods (mutate state, may emit events) ─────────────────
    @tx
    def submit(self, prompt: str, model: Optional[str] = None,
               work_dir: Optional[str] = None, key=None) -> dict:
        """Queue a job. Spawns BINARY via the Rust job server."""
        port = self._cfg.get("port")
        if not port:
            raise RuntimeError(f"{self.NAME}: no port in config.json")
        import urllib.request as r
        body = json.dumps({
            "prompt": prompt,
            "model": model or self.DEFAULT_MODEL,
            "work_dir": work_dir or os.getcwd(),
        }).encode()
        req = r.Request(f"http://127.0.0.1:{port}/jobs", method="POST", data=body,
                        headers={"Content-Type": "application/json"})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with r.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            self.emit("submit_failed", prompt=prompt[:50], error=str(e))
            self._save_state()
            return {"error": str(e)}
        self.state["jobs_submitted"] = self.state.get("jobs_submitted", 0) + 1
        self.emit("job_submitted", job_id=result.get("id"), prompt=prompt[:50],
                  model=model or self.DEFAULT_MODEL)
        self._save_state()
        return result

    @tx
    @owner_only
    def register(self, fund_eth: float = 0.0005, key=None) -> dict:
        """Register this agent on the mod protocol registry under its own
        deterministic key. Gas paid by the funded chain relayer."""
        dev = m.mod("dev")()
        result = dev.register_per_key(modules=[self.NAME], fund_eth=fund_eth, key=key)
        self.emit("registered", **result.get(self.NAME, {}))
        self._save_state()
        return result

    # ── Path discovery — like Solidity address resolution ──────────
    def _discover_path(self) -> str:
        here = Path(__file__).resolve().parent
        for d in (here, *here.parents):
            p = d.parent / self.NAME
            if (p / "config.json").exists():
                return str(p)
        return str(here.parent.parent / self.NAME)

    def _load_config(self) -> dict:
        cp = Path(self._path) / "config.json"
        return json.loads(cp.read_text()) if cp.exists() else {}


# Backwards-compat alias — earlier draft used AgentBase.
AgentBase = AgentContract
