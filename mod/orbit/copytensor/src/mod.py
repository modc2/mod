"""
copytensor — Bittensor dTAO copy trading (FastAPI + Next.js)

Layout:
    src/
      mod.py       # this file (orchestrator + mod-protocol surface)
      api/         # FastAPI app
      chain/       # SubtensorClient with round-robin RPC failover
      engine/      # leaderboard, pnl, copier, safety
      app/         # Next.js frontend
"""

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import requests
import mod as m

log = logging.getLogger("copytensor.mod")

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")
APP_DIR = os.path.join(SRC_DIR, "app")


def _has_docker() -> bool:
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


class Copytensor(m.Mod):
    """Bittensor dTAO copy trading — mirror subnet allocations of top performers.

    All read paths (leaderboard, subnets, account, trader, PnL) work without
    any wallet against a rotating pool of public Bittensor RPC endpoints.
    Only stake/unstake operations require a wallet (set via `set_wallet`).
    """

    name = "copytensor"
    description = (
        "Bittensor dTAO copy trading — mirror subnet allocations of top "
        "performers (round-robin public RPCs, no third-party APIs)"
    )
    fns = [
        # lifecycle
        "serve", "kill", "status", "logs", "test", "gateway",
        # public-read passthroughs (no wallet needed)
        "subnets", "leaderboard", "account", "account_pnl",
        "trader", "trades", "rpc_pool",
        # watchlist
        "watch", "unwatch", "watches",
        # copy management (needs wallet)
        "create_copy", "list_copies", "pause_copy", "resume_copy",
        "delete_copy", "sync_copy",
        # wallet
        "set_wallet", "wallet_balance",
        # default
        "forward",
    ]

    api_port = 50150
    app_port = 3150

    def __init__(self, **kwargs):
        self.api_url = os.environ.get(
            "COPYTENSOR_API_URL", f"http://localhost:{self.api_port}",
        )
        self._mode: Optional[str] = None

    # ── docker lifecycle ──────────────────────────────────────────

    def _docker_serve(self, build: bool = True) -> Dict[str, Any]:
        cmd = ["docker", "compose", "up", "-d"]
        if build:
            cmd.append("--build")
        proc = subprocess.run(
            cmd, cwd=ROOT_DIR, capture_output=True, text=True,
        )
        ok = proc.returncode == 0
        self._mode = "docker" if ok else None
        return {
            "ok": ok,
            "mode": "docker",
            "api": self.api_url,
            "app": f"http://localhost:{self.app_port}/copytensor",
            "stderr_tail": proc.stderr[-2000:] if not ok else "",
        }

    def _docker_kill(self) -> Dict[str, Any]:
        proc = subprocess.run(
            ["docker", "compose", "down"],
            cwd=ROOT_DIR, capture_output=True, text=True,
        )
        return {"ok": proc.returncode == 0, "mode": "docker", "action": "stopped"}

    def _docker_status(self) -> Dict[str, Any]:
        proc = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=ROOT_DIR, capture_output=True, text=True,
        )
        container = "stopped"
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                info = json.loads(proc.stdout.strip())
                if isinstance(info, list):
                    info = info[0] if info else {}
                container = info.get("State", info.get("status", "unknown"))
            except json.JSONDecodeError:
                container = "running"
        out = {"module": self.name, "mode": "docker", "container": container}
        try:
            r = requests.get(f"{self.api_url}/health", timeout=2)
            out["health"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def _docker_logs(self, lines: int = 50) -> str:
        proc = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(lines)],
            cwd=ROOT_DIR, capture_output=True, text=True,
        )
        return proc.stdout or proc.stderr or "<no output>"

    # ── local lifecycle ───────────────────────────────────────────
    # We avoid pm2 here: pm2.start() auto-generates a python serve script
    # that imports `mod`, which collides with copytensor/src/mod.py when the
    # cwd is inside the package. Plain Popen + a PID file is simpler and
    # actually runs the uvicorn / npm commands we want.

    LOG_DIR = "/tmp/copytensor"

    def _pid_file(self, role: str) -> str:
        os.makedirs(self.LOG_DIR, exist_ok=True)
        return os.path.join(self.LOG_DIR, f"{role}.pid")

    def _log_file(self, role: str) -> str:
        os.makedirs(self.LOG_DIR, exist_ok=True)
        return os.path.join(self.LOG_DIR, f"{role}.log")

    def _is_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _read_pid(self, role: str) -> Optional[int]:
        path = self._pid_file(role)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                pid = int(f.read().strip())
            return pid if self._is_running(pid) else None
        except Exception:
            return None

    def _kill_role(self, role: str, port: Optional[int] = None):
        pid = self._read_pid(role)
        if pid:
            try:
                os.kill(pid, 15)
                time.sleep(0.3)
                if self._is_running(pid):
                    os.kill(pid, 9)
            except Exception:
                pass
        try:
            os.remove(self._pid_file(role))
        except Exception:
            pass
        if port is not None:
            try:
                subprocess.run(
                    ["bash", "-c", f"lsof -ti:{port} | xargs -r kill -9"],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

    def _spawn(self, role: str, cmd: List[str], cwd: str,
               env: Dict[str, str]) -> Dict[str, Any]:
        log_path = self._log_file(role)
        log_f = open(log_path, "ab")
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            env={**os.environ, **env},
            stdout=log_f, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with open(self._pid_file(role), "w") as f:
            f.write(str(proc.pid))
        return {"ok": True, "pid": proc.pid, "log": log_path}

    def _local_api(self, port: Optional[int] = None) -> Dict[str, Any]:
        port = port or self.api_port
        self._kill_role("api", port=port)
        # Prefer the prebuilt Rust binary if it exists — it's the canonical
        # backend (axum + subxt). Fall back to uvicorn + the python copy if
        # the binary isn't built.
        rust_bin = os.path.join(
            SRC_DIR, "api", "target", "release", "copytensor-api",
        )
        if os.path.exists(rust_bin) and os.access(rust_bin, os.X_OK):
            cmd = [rust_bin]
            env = {"PORT": str(port), "RUST_LOG": "info"}
            out = self._spawn("api", cmd, cwd=ROOT_DIR, env=env)
            out["backend"] = "rust"
            out["port"] = port
            return out

        # api/app.py uses `from ..chain.client` so it must be loaded as
        # `src.api.app`. We run uvicorn from ROOT_DIR (one above src/) and
        # set PYTHONPATH there too so the `src` package is importable.
        cmd = [
            "uvicorn", "src.api.app:app",
            "--host", "0.0.0.0", "--port", str(port),
        ]
        env = {"PORT": str(port), "PYTHONPATH": ROOT_DIR}
        out = self._spawn("api", cmd, cwd=ROOT_DIR, env=env)
        out["backend"] = "python"
        out["port"] = port
        return out

    def _local_app(self, port: Optional[int] = None,
                   api_port: Optional[int] = None) -> Dict[str, Any]:
        port = port or self.app_port
        api_port = api_port or self.api_port
        if not os.path.isdir(os.path.join(APP_DIR, "node_modules")):
            subprocess.run(
                ["npm", "install", "--no-audit", "--no-fund"],
                cwd=APP_DIR, capture_output=True,
            )
        self._kill_role("app", port=port)
        env = {
            "PORT": str(port),
            "NEXT_PUBLIC_API_URL": f"http://localhost:{api_port}",
        }
        out = self._spawn(
            "app",
            ["npm", "run", "dev", "--", "-p", str(port)],
            cwd=APP_DIR, env=env,
        )
        out["port"] = port
        return out

    # ── unified serve / kill / status / logs ──────────────────────

    def serve(self, mode: Optional[str] = None, api_port: Optional[int] = None,
              app_port: Optional[int] = None, build: bool = True, **kw) -> Dict[str, Any]:
        """Start copytensor. mode='docker' (default if available) or 'local'."""
        if mode is None:
            mode = "docker" if _has_docker() else "local"

        if mode == "docker":
            result = self._docker_serve(build=build)
            if result["ok"]:
                self._register_gateway(self.api_port, self.app_port)
            return result

        self._mode = "local"
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port

        a = self._local_api(port=api_port)
        if not a.get("ok"):
            return {"ok": False, "mode": "local", "api": a}
        time.sleep(1)
        b = self._local_app(port=app_port, api_port=api_port)

        self._register_gateway(api_port, app_port)
        return {"ok": a.get("ok") and b.get("ok"), "mode": "local",
                "api": a, "app": b}

    def _register_gateway(self, api_port: int, app_port: int):
        """Wire copytensor into the mod-protocol gateway.

        The gateway is the core Next.js app on :3001 (whose middleware reads
        app_namespace from the Flask backend on :8000 and rewrites
        /api/{mod}/* → api_url and /{mod}/* → app url). Caddy on :3000 is the
        public-facing edge. Registering with `server.namespace.reg_app`
        publishes copytensor into the namespace so both work:

            http://localhost:3001/copytensor          → app  (mod gateway)
            http://localhost:3001/api/copytensor/*    → API  (mod gateway)
            http://localhost:3000/copytensor          → app  (caddy edge)
            http://localhost:3000/api/copytensor/*    → API  (caddy edge)
        """
        gateway_port = self._detect_gateway_port()
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg["urls"] = {
                "api": f"http://localhost:{api_port}",
                "app": f"http://localhost:{app_port}/copytensor",
                "gateway_api": f"http://localhost:{gateway_port}/api/copytensor",
                "gateway_app": f"http://localhost:{gateway_port}/copytensor",
            }
            cfg["port"] = api_port
            cfg["app_port"] = app_port
            cfg["gateway_port"] = gateway_port
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
                f.write("\n")
        except Exception:
            pass

        # Register with the mod-protocol app namespace so the core gateway
        # middleware can resolve /copytensor and /api/copytensor.
        import threading
        def _sync():
            try:
                ns = m.mod("server.namespace")()
                ns.reg_app(
                    name="copytensor",
                    address=f"http://localhost:{app_port}",
                    api_url=f"http://localhost:{api_port}",
                )
            except Exception as e:
                log.warning("namespace reg_app failed: %s", e)
        threading.Thread(target=_sync, daemon=True).start()

    def _detect_gateway_port(self) -> int:
        """Pick the active mod-protocol gateway port.

        Prefer the core Next.js app on :3001 (canonical mod-protocol
        gateway). Fall back to caddy edge on :3000 if the core app isn't
        reachable.
        """
        for p in (3001, 3000, 3002):
            try:
                r = requests.get(f"http://localhost:{p}/", timeout=1)
                if r.status_code < 500:
                    return p
            except Exception:
                continue
        return 3001

    def gateway(self) -> Dict[str, Any]:
        """Show the public gateway URLs for this module (mod-protocol pattern)."""
        p = self._detect_gateway_port()
        return {
            "gateway_port": p,
            "app": f"http://localhost:{p}/copytensor",
            "api": f"http://localhost:{p}/api/copytensor",
            "examples": {
                "leaderboard": f"http://localhost:{p}/api/copytensor/leaderboard?days=7&top=20",
                "subnets": f"http://localhost:{p}/api/copytensor/subnets",
                "health": f"http://localhost:{p}/api/copytensor/health",
            },
        }

    def kill(self, target: str = "all", mode: Optional[str] = None) -> Dict[str, Any]:
        """Stop services. mode='docker' (default) or 'local'."""
        if mode is None:
            mode = self._mode or ("docker" if _has_docker() else "local")

        if mode == "docker":
            return self._docker_kill()

        out: Dict[str, Any] = {"mode": "local"}
        if target in ("api", "all"):
            self._kill_role("api", port=self.api_port)
            out["api"] = "stopped"
        if target in ("app", "all"):
            self._kill_role("app", port=self.app_port)
            out["app"] = "stopped"
        return out

    def status(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """Service status. mode='docker' (default) or 'local'."""
        if mode is None:
            mode = self._mode or ("docker" if _has_docker() else "local")

        if mode == "docker":
            return self._docker_status()

        out = {"module": self.name, "mode": "local", "api_url": self.api_url}
        out["api"] = "running" if self._read_pid("api") else "stopped"
        out["app"] = "running" if self._read_pid("app") else "stopped"
        try:
            r = requests.get(f"{self.api_url}/health", timeout=2)
            out["health"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 50,
             mode: Optional[str] = None) -> str:
        """Fetch logs. mode='docker' (default) or 'local'."""
        if mode is None:
            mode = self._mode or ("docker" if _has_docker() else "local")

        if mode == "docker":
            return self._docker_logs(lines=lines)

        path = self._log_file(target)
        if not os.path.exists(path):
            return f"<no logs: {path}>"
        try:
            with open(path, "rb") as f:
                data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-lines:])
        except Exception as e:
            return f"<read failed: {e}>"

    # ── data passthroughs (call live API) ─────────────────────────

    def _get(self, path: str, **params) -> Any:
        r = requests.get(f"{self.api_url}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Optional[Dict] = None) -> Any:
        r = requests.post(f"{self.api_url}{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> Any:
        r = requests.delete(f"{self.api_url}{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    # public reads (no wallet needed)
    def subnets(self) -> Any:
        return self._get("/subnets")

    def leaderboard(self, days: int = 7, top: int = 50) -> Any:
        return self._get("/leaderboard", days=str(days), top=str(top))

    def account(self, ss58: str, days: int = 7) -> Any:
        return self._get(f"/account/{ss58}", days=str(days))

    def account_pnl(self, ss58: str, days: int = 7) -> Any:
        return self._get(f"/account/{ss58}/pnl", days=str(days))

    def trader(self, ss58: str) -> Any:
        return self._get(f"/trader/{ss58}")

    def trades(self, limit: int = 50, copy_id: Optional[str] = None) -> Any:
        params: Dict[str, str] = {"limit": str(limit)}
        if copy_id:
            params["copy_id"] = copy_id
        return self._get("/trades", **params)

    def rpc_pool(self) -> Any:
        """Show the active Bittensor RPC pool + which endpoint is primary."""
        return self._get("/health")

    # watchlist
    def watch(self, ss58: str, label: Optional[str] = None) -> Any:
        return self._post("/watch", {"ss58": ss58, "label": label})

    def unwatch(self, ss58: str) -> Any:
        return self._delete(f"/watch/{ss58}")

    def watches(self) -> Any:
        return self._get("/watches")

    # copies
    def create_copy(self, target_ss58: str, our_hotkey: str,
                    label: Optional[str] = None,
                    max_tao_per_tx: Optional[float] = None,
                    daily_limit_tao: Optional[float] = None,
                    rebalance_threshold_pct: Optional[float] = None) -> Any:
        body = {
            "target_ss58": target_ss58,
            "our_hotkey": our_hotkey,
            "label": label,
            "max_tao_per_tx": max_tao_per_tx,
            "daily_limit_tao": daily_limit_tao,
            "rebalance_threshold_pct": rebalance_threshold_pct,
        }
        return self._post("/copy", {k: v for k, v in body.items() if v is not None})

    def list_copies(self) -> Any:
        return self._get("/copies")

    def pause_copy(self, copy_id: str) -> Any:
        return self._post(f"/copy/{copy_id}/pause")

    def resume_copy(self, copy_id: str) -> Any:
        return self._post(f"/copy/{copy_id}/resume")

    def delete_copy(self, copy_id: str) -> Any:
        return self._delete(f"/copy/{copy_id}")

    def sync_copy(self, copy_id: str) -> Any:
        return self._post(f"/copy/{copy_id}/sync")

    # wallet
    def set_wallet(self, mnemonic: Optional[str] = None,
                   seed_hex: Optional[str] = None) -> Any:
        body = {k: v for k, v in {"mnemonic": mnemonic, "seed_hex": seed_hex}.items() if v}
        return self._post("/wallet/set", body)

    def wallet_balance(self) -> Any:
        return self._get("/wallet/balance")

    # ── test ──────────────────────────────────────────────────────

    def test(self) -> Dict[str, Any]:
        """Hit every public read endpoint + show the RPC pool."""
        results: Dict[str, Any] = {}
        for name, fn in [
            ("health", lambda: self._get("/health")),
            ("subnets", lambda: self._get("/subnets")),
            ("leaderboard", lambda: self._get("/leaderboard", days="7", top="10")),
            ("status", lambda: self._get("/status")),
        ]:
            try:
                data = fn()
                results[name] = {"ok": True, "preview": str(data)[:200]}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}
        passed = sum(1 for v in results.values() if v.get("ok"))
        total = len(results)
        results["summary"] = f"{passed}/{total} passed"
        results["ok"] = passed == total
        return results

    # ── forward ──────────────────────────────────────────────────

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        if fn is None:
            return {
                "module": self.name,
                "description": self.description,
                "fns": self.fns,
                "api": self.api_url,
                "app": f"http://localhost:{self.app_port}/copytensor",
            }
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


Mod = Copytensor
