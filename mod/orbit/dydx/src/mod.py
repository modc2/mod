"""
dydx — copy-trading on dYdX, scaffolded from the tradekit
standard (tradekit/1; reference framework: orbit/hyperliquid).

High-level manager:
    - builds the Rust API (cargo build --release)
    - serves the Rust API on `api_port`
    - serves the Next.js app on `app_port` (when src/app exists)
    - exposes a `forward(fn=..., **kwargs)` dispatch for mod-protocol callers

Layout:
    src/
      mod.py           # this file
      api/             # Rust API (axum) — the venue adapter lives here
      strats/          # canonical Strat classes (base.py = shared schema)
      app/             # Next.js app (optional)

Check conformance any time with `m tradekit/check mod=dydx`.
"""

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import requests

import mod as m

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")
APP_DIR = os.path.join(SRC_DIR, "app")


class Dydx(m.Mod):
    """dYdX copy-trading orchestrator (tradekit/1)."""

    name = "dydx"
    description = "dYdX copy-trading — top traders, strats, backtests, live engine, MCP"
    fns = [
        # lifecycle
        "forward", "serve", "api", "kill", "status", "build", "logs", "test",
        # identity
        "whoami",
        # market + traders (dYdX has no leaderboard: top_traders ranks a watchlist)
        "markets", "top_traders", "analyze_trader", "watchlist", "watch", "unwatch",
        # strats
        "strat", "list_strats", "run_strat", "backtest_strats",
        # live copy-trade engine
        "live_start", "live_stop", "live_status",
        # MCP tool server
        "mcp", "mcp_tools", "mcp_call", "mcp_config",
        # build provenance
        "build_cid", "publish_build", "build_onchain",
    ]

    api_port = 51442
    app_port = 51443

    def __init__(self, api_url: Optional[str] = None, token: Optional[str] = None, **kwargs):
        self.api_url = api_url or os.environ.get("DYDX_API_URL", f"http://localhost:{self.api_port}")
        self.token = token or os.environ.get("DYDX_TOKEN", "")

    # ── lifecycle ────────────────────────────────────────────────────

    def build(self, release: bool = True) -> Dict[str, Any]:
        """Build the Rust API binary."""
        if not shutil.which("cargo"):
            return {"ok": False, "error": "cargo not on PATH"}
        cmd = ["cargo", "build"] + (["--release"] if release else [])
        proc = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True)
        return {"ok": proc.returncode == 0, "release": release,
                "stderr_tail": proc.stderr[-2000:] if proc.returncode else ""}

    def _api_binary(self) -> str:
        for kind in ("release", "debug"):
            p = os.path.join(API_DIR, "target", kind, "dydx-api")
            if os.path.exists(p):
                return p
        return ""

    def api(self, port: Optional[int] = None, build: bool = True) -> Dict[str, Any]:
        """Start the Rust API under pm2."""
        port = port or self.api_port
        if build:
            r = self.build(release=True)
            if not r["ok"]:
                return {"ok": False, "stage": "build", **r}
        bin_path = self._api_binary()
        if not bin_path:
            return {"ok": False, "error": "binary not found after build"}
        script = os.path.join(SRC_DIR, "_api.sh")
        with open(script, "w") as f:
            f.write(f"#!/bin/bash\nexport PORT={port}\nexport MALLOC_ARENA_MAX=2\nexec {bin_path}\n")
        os.chmod(script, 0o755)
        try:
            pm2 = m.mod("pm.pm2")()
            name = "dydx-api"
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=SRC_DIR, interpreter="bash")
            return {"ok": True, "manager": "pm2", "name": name, "port": port}
        except Exception as e:
            proc = subprocess.Popen(["bash", script], cwd=SRC_DIR)
            return {"ok": True, "manager": "subprocess", "pid": proc.pid, "port": port,
                    "warn": f"pm2 unavailable ({e}); using subprocess"}

    def serve(self, api_port: Optional[int] = None, build: bool = True, **kwargs) -> Dict[str, Any]:
        """Start the module (API; the app too once src/app exists)."""
        a = self.api(port=api_port, build=build)
        self._register_gateway(api_port or self.api_port, self.app_port)
        return {"ok": bool(a.get("ok")), "api": a}

    def _register_gateway(self, api_port: int, app_port: int):
        try:
            m.mod("server.namespace")().reg_app(
                "dydx", f"http://localhost:{app_port}", api_url=f"http://localhost:{api_port}")
        except Exception as e:
            print(f"[dydx] gateway registration failed: {e}")

    def kill(self, target: str = "all") -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            pm2 = m.mod("pm.pm2")()
            for t in ("api", "app"):
                if target in (t, "all") and pm2.exists(f"dydx-{t}"):
                    pm2.kill(f"dydx-{t}")
                    out[t] = "stopped"
        except Exception as e:
            out["error"] = str(e)
        return out

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"module": "dydx", "api_url": self.api_url}
        try:
            r = requests.get(f"{self.api_url}/status", timeout=2)
            out["api_status"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["api_status"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 100) -> str:
        try:
            return m.mod("pm.pm2")().logs(f"dydx-{target}", lines=lines)
        except Exception as e:
            return f"<no pm2 logs: {e}>"

    # ── http helpers ─────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @staticmethod
    def _check(r: "requests.Response") -> Any:
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text[:500]}
        if not r.ok:
            msg = body.get("error") if isinstance(body, dict) else None
            raise RuntimeError(f"{r.status_code}: {msg or body}")
        return body

    def _get(self, path: str, _timeout: float = 30, **params) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        return self._check(requests.get(f"{self.api_url}{path}", params=params,
                                        headers=self._headers(), timeout=_timeout))

    def _post(self, path: str, body: Any) -> Any:
        return self._check(requests.post(f"{self.api_url}{path}", json=body,
                                         headers=self._headers(), timeout=60))

    def whoami(self) -> Any:
        return self._get("/auth/me")

    # ── market + traders ─────────────────────────────────────────────

    def markets(self, limit: int = 50) -> Any:
        """Tradable markets on dYdX."""
        return self._get("/markets", limit=limit)

    def top_traders(self, days: int = 7, limit: int = 50, sort: str = "pnl") -> Any:
        """Leaderboard over `days`."""
        return self._get("/traders", days=days, limit=limit, sort=sort)

    def analyze_trader(self, address: str, days: int = 7) -> Any:
        """One trader's stats over `days`."""
        return self._get(f"/traders/{address}", days=days)

    # ── watchlist — the roster top_traders ranks ─────────────────────

    _WATCH = os.path.expanduser("~/.mod/dydx/watchlist.json")

    def watchlist(self) -> List[str]:
        """Addresses top_traders ranks (plus $DYDX_WATCHLIST on the API)."""
        try:
            with open(self._WATCH) as f:
                return list(json.load(f))
        except Exception:
            return []

    def _save_watch(self, addrs: List[str]) -> List[str]:
        os.makedirs(os.path.dirname(self._WATCH), exist_ok=True)
        addrs = sorted(set(addrs))
        tmp = self._WATCH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(addrs, f, indent=1)
        os.replace(tmp, self._WATCH)
        return addrs

    def watch(self, address: str) -> List[str]:
        """Add a dydx1… address to the watchlist."""
        address = address.strip()
        if not address.startswith("dydx1"):
            raise ValueError("address must be a dydx1… bech32 address")
        return self._save_watch(self.watchlist() + [address])

    def unwatch(self, address: str) -> List[str]:
        return self._save_watch([a for a in self.watchlist() if a != address.strip()])

    # ── strats ───────────────────────────────────────────────────────

    @staticmethod
    def _strats():
        """src/strats as a package, loaded by path — src/ is not reliably on
        sys.path when the framework imports this file."""
        import importlib.util, sys
        name = "dydx_strats"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, os.path.join(SRC_DIR, "strats", "__init__.py"),
                submodule_search_locations=[os.path.join(SRC_DIR, "strats")])
            pkg = importlib.util.module_from_spec(spec)
            sys.modules[name] = pkg
            spec.loader.exec_module(pkg)
        return sys.modules[name]

    def strat(self, name: str, **kwargs: Any):
        return self._strats().make(name, **kwargs)

    def list_strats(self) -> Any:
        return self._strats().list_strats()

    def run_strat(self, name: str, eoa: str, **kwargs: Any) -> Any:
        return self.strat(name, **kwargs).start(self, eoa)

    def backtest_strats(self, days: str = "1,7,30", capital: float = 1000.0) -> Any:
        return self._get("/strats/backtest", days=days, capital=capital, _timeout=300)

    # ── live engine — DRY RUN unless auto_execute=True ──────────────

    def live_start(self, eoa: str, traders: Optional[List[Dict[str, Any]]] = None,
                   capital: Optional[float] = None, auto_execute: bool = False,
                   **params: Any) -> Any:
        """Start a copy session. Dry run by default: every mirror is computed
        and none is placed until a caller passes auto_execute=True."""
        body = {"eoa": eoa, "traders": traders or [], "capital": capital,
                "auto_execute": auto_execute is True or str(auto_execute).lower() == "true",
                **params}
        return self._post("/live/start", body)

    def live_stop(self, eoa: str) -> Any:
        """Stop the session. Never gated."""
        return self._post("/live/stop", {"eoa": eoa})

    def live_status(self, eoa: str) -> Any:
        return self._get("/live/status", eoa=eoa)

    # ── MCP (Rust serves POST /mcp + GET /mcp/schema) ───────────────

    def mcp(self) -> Any:
        return self._get("/mcp/schema")

    def mcp_tools(self) -> List[Dict[str, Any]]:
        return [{"name": t["name"], "fn": t["fn"],
                 "bound": t["fn"] in self.fns and hasattr(self, t["fn"])}
                for t in (self.mcp().get("tools") or [])]

    def mcp_call(self, tool: str, arguments: Optional[Dict[str, Any]] = None, **kwargs) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments or {}, **kwargs)}}
        msg = self._check(requests.post(f"{self.api_url}/mcp", json=body,
                                        headers=self._headers(), timeout=300))
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        result = msg.get("result", {})
        if "structuredContent" in result:
            return result["structuredContent"]
        text = (result.get("content") or [{}])[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def mcp_config(self) -> Dict[str, Any]:
        return {"http": {"type": "http", "url": f"{self.api_url}/mcp"},
                "gateway": {"type": "http", "url": "/dydx/api/mcp"},
                "add_cmd": f"claude mcp add --transport http dydx {self.api_url}/mcp"}

    # ── build provenance ─────────────────────────────────────────────

    _BUILD_IGNORE = ["node_modules", "target", ".next", "__pycache__", ".git"]

    def build_cid(self, mod: str = "dydx") -> str:
        """Localfs CID over the module's source files (same scheme as hyperliquid)."""
        return m.mod("localfs")().cid(m.content(mod, ignore_folders=self._BUILD_IGNORE))

    def publish_build(self, **kwargs) -> Dict[str, Any]:
        """Record the build CID on-chain. Not wired for dYdX yet."""
        return {"ok": False, "cid": self.build_cid(), "error": "publish_build not wired for this venue yet"}

    def build_onchain(self) -> Dict[str, Any]:
        try:
            with open(os.path.join(ROOT_DIR, "config.json")) as f:
                return json.load(f).get("build_onchain", {})
        except Exception:
            return {}

    # ── test ─────────────────────────────────────────────────────────

    def test(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, path in (("status", "/status"), ("markets", "/markets"),
                           ("traders", "/traders"), ("mcp_schema", "/mcp/schema")):
            try:
                r = requests.get(f"{self.api_url}{path}", timeout=10)
                results[name] = {"ok": r.ok, "status": r.status_code}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}
        passed = sum(1 for v in results.values() if v.get("ok"))
        total = len(results)
        results["summary"] = f"{passed}/{total} passed"
        results["ok"] = passed == total
        return results

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        if fn is None:
            return {"module": self.name, "fns": self.fns, "api": self.api_url}
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


Mod = Dydx
