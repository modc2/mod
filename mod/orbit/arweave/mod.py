"""
arweave — store and retrieve data on Arweave via a public gateway, with optional wallet uploads.

Usage (Python):
    import mod as m
    ar = m.mod('arweave')()
    ar.put({'hello': 'arweave'})            # → {txid, size, backend}
    ar.get('abc123...')                      # → parsed content
    ar.serve()                                # bring up FastAPI + web app at :50153
    ar.status()                               # backend connectivity

Usage (CLI):
    m arweave/serve
    m arweave/put data='{"hello":"world"}'
    m arweave/get txid=abc123...
    m arweave/status
    m arweave/price num_bytes=1024
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 50153


class Mod:
    description = (
        "Arweave storage and retrieval — public gateway reads + optional wallet uploads, "
        "with FastAPI server and minimal web app."
    )

    def __init__(self, port: Optional[int] = None, **kwargs):
        self.module_dir = DIR
        self.config = self._load_config()
        self.port = int(port or self.config.get("port") or DEFAULT_PORT)
        self._client = None

    # ─── helpers ────────────────────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        cfg_path = self.module_dir / "config.json"
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    @property
    def client(self):
        if self._client is None:
            import sys
            sys.path.insert(0, str(self.module_dir))
            from arlocal import ArweaveClient
            self._client = ArweaveClient()
        return self._client

    def forward(self, **_: Any) -> Dict[str, Any]:
        return self.status()

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def serve(self, port: Optional[int] = None, dev: bool = False) -> Dict[str, Any]:
        """Start the FastAPI server (API + static app) on `port`."""
        port = int(port or self.port)
        log_dir = Path("/tmp/arweave")
        log_dir.mkdir(parents=True, exist_ok=True)

        self.kill()

        api_dir = self.module_dir / "api"
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{self.module_dir}:{env.get('PYTHONPATH', '')}"
        env["PORT"] = str(port)

        cmd = [
            "python3", "-m", "uvicorn", "api:app",
            "--host", "0.0.0.0", "--port", str(port),
            "--app-dir", str(api_dir),
        ]
        if dev:
            cmd.append("--reload")

        log = open(log_dir / "server.log", "w")
        proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)

        url = f"http://localhost:{port}"
        self._save_urls(url)
        return {
            "pid": proc.pid,
            "url": url,
            "api": f"{url}/api",
            "docs": f"{url}/docs",
            "log": str(log_dir / "server.log"),
        }

    def kill(self) -> Dict[str, Any]:
        killed = []
        pattern = f"uvicorn.*api:app.*{self.port}"
        try:
            r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            for pid in (r.stdout or "").strip().splitlines():
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        killed.append(int(pid))
                    except ProcessLookupError:
                        pass
        except Exception:
            pass
        return {"killed": killed}

    def _save_urls(self, url: str) -> None:
        cfg_path = self.module_dir / "config.json"
        if not cfg_path.exists():
            return
        with open(cfg_path) as f:
            data = json.load(f)
        data["urls"] = {"api": f"{url}/api", "app": url}
        with open(cfg_path, "w") as f:
            json.dump(data, f, indent=2)

    # ─── client proxies ─────────────────────────────────────────────────────

    def put(self, data: Any, name: Optional[str] = None) -> Dict[str, Any]:
        return self.client.put(data, name=name)

    def put_file(self, path: str) -> Dict[str, Any]:
        return self.client.put_file(path)

    def get(self, txid: str) -> Any:
        return self.client.get(txid)

    def get_file(self, txid: str, dest: str) -> Dict[str, Any]:
        return self.client.get_file(txid, dest)

    def list(self, limit: int = 100):
        return self.client.list(limit=limit)

    def rm(self, txid: str) -> Dict[str, Any]:
        return self.client.rm(txid)

    def tx(self, txid: str) -> Dict[str, Any]:
        return self.client.tx(txid)

    def price(self, num_bytes: int) -> Dict[str, Any]:
        return self.client.price(num_bytes)

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.config.get("name", "arweave"),
            "version": self.config.get("version"),
            "description": self.description,
            "port": self.port,
            "urls": self.config.get("urls", {}),
            "fns": self.config.get("fns", []),
            "endpoints": list((self.config.get("endpoints") or {}).keys()),
        }

    def status(self) -> Dict[str, Any]:
        s = self.client.status()
        s["port"] = self.port
        s["urls"] = self.config.get("urls", {})
        s["running"] = self._is_running()
        return s

    def _is_running(self) -> bool:
        try:
            import requests
            r = requests.get(f"http://localhost:{self.port}/api/health", timeout=2)
            return r.ok
        except Exception:
            return False

    def call(self, fn: str = "health", params: Optional[dict] = None, timeout: int = 10):
        """Call any /api/<fn> endpoint on the running server."""
        import requests
        url = f"http://localhost:{self.port}/api/{fn.lstrip('/')}"
        method = "POST" if params else "GET"
        try:
            if method == "GET":
                r = requests.get(url, timeout=timeout)
            else:
                r = requests.post(url, json=params, timeout=timeout)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return r.text
        except requests.RequestException as e:
            return {"error": str(e), "url": url}

    c = call
