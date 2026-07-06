"""
localfs — orbit module wrapping the content-addressable local filesystem.

Delegates to the LocalFS implementation under mod/core/store/src/localfs and
exposes the usual orbit lifecycle (info, serve, kill, status).
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import mod as m

# Reuse the canonical LocalFS implementation that lives under mod/core/store.
# We load it via importlib under a unique module name to avoid colliding with
# this package, which is also called `localfs`.
import importlib.util as _ilu

_CORE_LOCALFS_MOD = Path(__file__).resolve().parents[3] / "core" / "store" / "src" / "localfs" / "localfs" / "mod.py"
_spec = _ilu.spec_from_file_location("_localfs_core_mod", _CORE_LOCALFS_MOD)
_core = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_core)
LocalFS = _core.LocalFS


class Mod:
    description = "Content-addressable local filesystem (IPFS-compatible CIDs, no daemon)"
    path = str(Path(__file__).resolve().parents[1])

    PIDFILE = "/tmp/localfs/served.json"
    LOG_DIR = "/tmp/localfs"

    def __init__(self, storage_path: Optional[str] = None):
        self._cfg_path = os.path.join(self.path, "config.json")
        self._cfg = self._load_config()
        self.fs = LocalFS(storage_path=storage_path or self._cfg.get("storage_path"))

    def _load_config(self) -> dict:
        if not os.path.exists(self._cfg_path):
            return {}
        with open(self._cfg_path) as f:
            return json.load(f)

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": self._cfg.get("name", "localfs"),
            "description": self.description,
            "path": self.path,
            "ports": {"api": self._cfg.get("port"), "app": self._cfg.get("app_port")},
            "urls": self._cfg.get("urls", {}),
            "schema": self._cfg.get("schema"),
            "storage_path": str(self.fs.storage_path),
        }

    # ── Storage primitives (delegate to LocalFS) ──────────────────────

    def put(self, data: Any, pin: bool = True) -> str:
        return self.fs.put(data, pin=pin)

    def get(self, cid: str) -> Any:
        return self.fs.get(cid)

    def cid(self, data: Any) -> str:
        return self.fs.cid(data)

    def iscid(self, text: Any) -> bool:
        """True iff `text` looks like a content identifier (IPFS Qm... or CIDv1
        baf...). Used by core/registry to decide whether to resolve a string
        as a CID URL or treat it as a raw module name."""
        if not isinstance(text, str):
            return False
        s = text.strip()
        # Strip any provider prefix (ipfs/Qm..., lighthouse/baf..., etc.)
        if '/' in s and not s.startswith('/'):
            s = s.split('/', 1)[1]
        if s.startswith('Qm') and len(s) == 46:
            return True
        if s.startswith(('baf', 'bafy', 'bafk', 'bafq')) and len(s) >= 50:
            return True
        return False

    def valid_cid(self, cid: str) -> bool:
        """Stronger check — `iscid` + we actually have it locally (or can
        fetch it). Default to shape check; backends that index CIDs locally
        can override to also confirm presence."""
        return self.iscid(cid)

    def rm(self, cid: str) -> Dict[str, Any]:
        return self.fs.rm(cid)

    def pin(self, cid: str) -> Dict[str, Any]:
        return self.fs.pin_add(cid)

    def unpin(self, cid: str) -> Dict[str, Any]:
        return self.fs.pin_rm(cid)

    def pins(self, cid: Optional[str] = None) -> Dict[str, Any]:
        return self.fs.pins(cid)

    def stats(self) -> Dict[str, Any]:
        return self.fs.stats()

    def gc(self, aggressive: bool = False) -> Dict[str, Any]:
        return self.fs.gc(aggressive=aggressive)

    def test(self) -> bool:
        return self.fs.test()

    # ── Serve / Kill ──────────────────────────────────────────────────

    def _log_dir(self) -> Path:
        p = Path(self.LOG_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _read_pidfile(self) -> dict:
        p = Path(self.PIDFILE)
        return json.loads(p.read_text()) if p.exists() else {}

    def _write_pidfile(self, data: dict):
        Path(self.PIDFILE).parent.mkdir(parents=True, exist_ok=True)
        Path(self.PIDFILE).write_text(json.dumps(data, indent=2))

    def _pid_alive(self, pid: Optional[int]) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def serve(self, api_port: Optional[int] = None, app_port: Optional[int] = None) -> dict:
        """Start the FastAPI backend and the static app server."""
        api_port = int(api_port or self._cfg.get("port", 8860))
        app_port = int(app_port or self._cfg.get("app_port", 8861))
        log_dir = self._log_dir()

        # Kill anything already bound to these ports.
        for port in (api_port, app_port):
            subprocess.run(f"lsof -ti:{port} | xargs kill -9", shell=True,
                           capture_output=True)

        api_dir = os.path.join(self.path, "api")
        app_dir = os.path.join(self.path, "app")

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [env.get("PYTHONPATH"), str(Path(self.path).parents[1])])
        )

        api_proc = subprocess.Popen(
            ["python3", "-m", "uvicorn", "api:app",
             "--host", "0.0.0.0", "--port", str(api_port)],
            cwd=api_dir, env=env,
            stdout=open(log_dir / "api.log", "ab"),
            stderr=subprocess.STDOUT,
        )

        app_proc = subprocess.Popen(
            ["python3", "-m", "http.server", str(app_port)],
            cwd=app_dir,
            stdout=open(log_dir / "app.log", "ab"),
            stderr=subprocess.STDOUT,
        )

        state = {
            "api": {"pid": api_proc.pid, "port": api_port,
                    "url": f"http://localhost:{api_port}",
                    "log": str(log_dir / "api.log")},
            "app": {"pid": app_proc.pid, "port": app_port,
                    "url": f"http://localhost:{app_port}",
                    "log": str(log_dir / "app.log")},
            "started_at": int(time.time()),
        }
        self._write_pidfile(state)
        return state

    def kill(self) -> dict:
        state = self._read_pidfile()
        stopped = {}
        for name in ("api", "app"):
            rec = state.get(name) or {}
            pid = rec.get("pid")
            if pid and self._pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    stopped[name] = {"pid": pid, "stopped": True}
                except ProcessLookupError:
                    stopped[name] = {"pid": pid, "stopped": False}
            else:
                stopped[name] = {"pid": pid, "stopped": False, "reason": "not alive"}
        Path(self.PIDFILE).unlink(missing_ok=True)
        return {"stopped": stopped}

    def status(self) -> dict:
        state = self._read_pidfile()
        for name in ("api", "app"):
            rec = state.get(name) or {}
            rec["alive"] = self._pid_alive(rec.get("pid"))
            state[name] = rec
        return state
