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
import shutil
import subprocess
import sys
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
    any wallet. They are served by the `bt` module's local index — the same
    indexer behind the bt explorer — which also keeps the history of every
    tracked trader. When bt is down, reads fall back to a rotating pool of
    public Bittensor RPC endpoints. Only stake/unstake operations require a
    wallet (set via `set_wallet`).

    `ask` talks to the strat agent — a Claude wired to those same reads that
    picks a weighted basket of traders and says why each one is in it. It is
    read-only too: a proposal is a basket you activate yourself.
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
        "subnets", "leaderboard", "account", "account_pnl", "account_curve",
        "trader", "trades", "rpc_pool", "source",
        # tracked traders (indexed by the bt module)
        "traders", "trader_history", "flows",
        # watchlist + the trader pool the leaderboard ranks
        "watch", "unwatch", "watches", "discover", "universe", "set_pool",
        # the strat agent — talk to it, get a basket back
        "ask", "agent_status",
        # copy management (needs wallet)
        "create_copy", "list_copies", "pause_copy", "resume_copy",
        "delete_copy", "sync_copy", "resize_copy",
        # the blended book
        "portfolio", "sync_portfolio",
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

    def _default_mode(self) -> str:
        """Whichever mode is actually deployed.

        Docker being *installed* is not the same as copytensor *running* in
        docker — defaulting to docker on a local deployment made `serve`
        attempt an image build and `status`/`logs` report an empty container
        while the real services were up. Pick docker only when this module
        actually has a compose container.
        """
        if self._mode:
            return self._mode
        if not _has_docker():
            return "local"
        try:
            proc = subprocess.run(
                ["docker", "compose", "ps", "-q"],
                cwd=ROOT_DIR, capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return "docker"
        except Exception:
            pass
        return "local"

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
    # We avoid m.pm2.start() here: it auto-generates a python serve script
    # that imports `mod`, which collides with copytensor/src/mod.py when the
    # cwd is inside the package. We drive the pm2 CLI directly instead, so
    # both services get restarted on crash / reboot like the rest of the
    # fleet, and fall back to plain Popen + a PID file when pm2 is absent.

    LOG_DIR = "/tmp/copytensor"

    def _pm2_bin(self) -> Optional[str]:
        return shutil.which("pm2")

    def _pm2_name(self, role: str) -> str:
        return f"{self.name}-{role}"

    def _pm2_running(self, role: str) -> bool:
        pm2 = self._pm2_bin()
        if not pm2:
            return False
        try:
            proc = subprocess.run(
                [pm2, "jlist"], capture_output=True, text=True, timeout=15,
            )
            for p in json.loads(proc.stdout or "[]"):
                if p.get("name") == self._pm2_name(role):
                    return p.get("pm2_env", {}).get("status") == "online"
        except Exception:
            pass
        return False

    def _pm2_spawn(self, role: str, cmd: List[str], cwd: str,
                   env: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Start `cmd` under pm2. Returns None if pm2 isn't usable.

        `--interpreter none` is not optional: pm2 hands anything it doesn't
        recognise to node, so the API's python entrypoint crash-looped on
        "Cannot use import statement outside a module" while `pm2 start` still
        exited 0. The API reported itself started, was never up, and after a
        reboot the app served 500s against nothing. Hence also the online
        check — pm2's exit code says the process was accepted, not that it
        survived.
        """
        pm2 = self._pm2_bin()
        if not pm2:
            return None
        name = self._pm2_name(role)
        subprocess.run([pm2, "delete", name], capture_output=True, timeout=30)
        proc = subprocess.run(
            [pm2, "start", cmd[0], "--name", name, "--cwd", cwd,
             "--interpreter", "none", "--"] + cmd[1:],
            cwd=cwd, env={**os.environ, **env},
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            log.warning("pm2 start %s failed: %s", name, proc.stderr.strip())
            return None
        if not self._pm2_settled(role):
            log.warning("pm2 %s did not stay up — falling back to Popen", name)
            subprocess.run([pm2, "delete", name], capture_output=True, timeout=30)
            return None
        subprocess.run([pm2, "save"], capture_output=True, timeout=30)
        return {"ok": True, "supervisor": "pm2", "pm2_name": name}

    def _pm2_settled(self, role: str, wait_sec: float = 8.0) -> bool:
        """True once the process is online and has stopped restarting.

        A crash-looping process reports "online" between restarts, so status
        alone is not enough — the restart counter has to hold still too.
        """
        deadline = time.time() + wait_sec
        restarts = None
        while time.time() < deadline:
            time.sleep(2)
            proc = subprocess.run([self._pm2_bin(), "jlist"],
                                  capture_output=True, text=True, timeout=15)
            try:
                procs = json.loads(proc.stdout or "[]")
            except json.JSONDecodeError:
                continue
            for p in procs:
                if p.get("name") != self._pm2_name(role):
                    continue
                env = p.get("pm2_env", {})
                if env.get("status") != "online":
                    return False
                n = env.get("restart_time", 0)
                if restarts is not None and n == restarts:
                    return True
                restarts = n
        return restarts is not None

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
        pm2 = self._pm2_bin()
        if pm2:
            subprocess.run([pm2, "delete", self._pm2_name(role)],
                           capture_output=True, timeout=30)
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
            out = (self._pm2_spawn("api", cmd, cwd=ROOT_DIR, env=env)
                   or self._spawn("api", cmd, cwd=ROOT_DIR, env=env))
            out["backend"] = "rust"
            out["port"] = port
            return out

        # api/app.py uses `from ..chain.client` so it must be loaded as
        # `src.api.app`. We run uvicorn from ROOT_DIR (one above src/) and
        # set PYTHONPATH there too so the `src` package is importable.
        cmd = [
            sys.executable or "python3", "-m", "uvicorn", "src.api.app:app",
            "--host", "0.0.0.0", "--port", str(port),
        ]
        env = {"PORT": str(port), "PYTHONPATH": ROOT_DIR}
        out = (self._pm2_spawn("api", cmd, cwd=ROOT_DIR, env=env)
               or self._spawn("api", cmd, cwd=ROOT_DIR, env=env))
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
        # Serve a production build: `next dev` recompiles every route on each
        # request and dies under load, which is how the app used to disappear
        # behind a gateway 500. Build once if .next is missing or is a dev
        # build (dev builds have no BUILD_ID).
        built = os.path.exists(os.path.join(APP_DIR, ".next", "BUILD_ID"))
        if not built:
            b = subprocess.run(
                ["npm", "run", "build"],
                cwd=APP_DIR, env={**os.environ, **env},
                capture_output=True, text=True, timeout=900,
            )
            if b.returncode != 0:
                return {"ok": False, "port": port, "stage": "build",
                        "error": (b.stderr or b.stdout)[-2000:]}

        cmd = ["npm", "run", "start", "--", "-p", str(port)]
        out = (self._pm2_spawn("app", cmd, cwd=APP_DIR, env=env)
               or self._spawn("app", cmd, cwd=APP_DIR, env=env))
        out["port"] = port
        return out

    # ── unified serve / kill / status / logs ──────────────────────

    def serve(self, mode: Optional[str] = None, api_port: Optional[int] = None,
              app_port: Optional[int] = None, build: bool = True, **kw) -> Dict[str, Any]:
        """Start copytensor. mode='docker' (default if available) or 'local'."""
        if mode is None:
            mode = self._default_mode()

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
                "mcp": f"http://localhost:{api_port}/mcp",
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
            mode = self._default_mode()

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
            mode = self._default_mode()

        if mode == "docker":
            return self._docker_status()

        out = {"module": self.name, "mode": "local", "api_url": self.api_url}
        for role in ("api", "app"):
            out[role] = ("running" if (self._pm2_running(role)
                                       or self._read_pid(role)) else "stopped")
        try:
            r = requests.get(f"{self.api_url}/health", timeout=15)
            out["health"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 50,
             mode: Optional[str] = None) -> str:
        """Fetch logs. mode='docker' (default) or 'local'."""
        if mode is None:
            mode = self._default_mode()

        if mode == "docker":
            return self._docker_logs(lines=lines)

        if self._pm2_running(target):
            proc = subprocess.run(
                [self._pm2_bin(), "logs", self._pm2_name(target),
                 "--lines", str(lines), "--nostream"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.stdout.strip():
                return proc.stdout

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

    def _post_q(self, path: str, **params) -> Any:
        """POST with query params (the discovery endpoints take no body)."""
        r = requests.post(f"{self.api_url}{path}", params=params, timeout=120)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: Optional[Dict] = None) -> Any:
        r = requests.put(f"{self.api_url}{path}", json=body or {}, timeout=30)
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

    def account_curve(self, ss58: str, days: int = 7) -> Any:
        """Equity/PnL curve from local snapshots + the trades on it."""
        return self._get(f"/account/{ss58}/curve", days=str(days))

    def _first_watched(self) -> str:
        """Any tracked coldkey — enough to smoke-test the account endpoints."""
        try:
            with open(os.path.join(ROOT_DIR, "config.json")) as f:
                accounts = json.load(f).get("watched_accounts") or []
            if accounts:
                return accounts[0]
        except Exception:
            pass
        return (self._get("/watches")["accounts"] or [{}])[0].get("ss58", "")

    def trader(self, ss58: str) -> Any:
        return self._get(f"/trader/{ss58}")

    # tracked traders — bt's index answers these, not the RPC pool
    def traders(self, sort_by: str = "total_tao") -> Any:
        """Tracked traders with value, allocation and windowed PnL."""
        return self._get("/traders", sort_by=sort_by)

    def trader_history(self, ss58: str, hours: int = 168) -> Any:
        """A tracked trader's portfolio value over time."""
        return self._get(f"/traders/{ss58}/history", hours=str(hours))

    def flows(self, ss58: Optional[str] = None, hours: int = 168,
              limit: int = 100) -> Any:
        """Inferred buys/sells — one trader's, or the whole tape."""
        path = f"/traders/{ss58}/flows" if ss58 else "/flows"
        return self._get(path, hours=str(hours), limit=str(limit))

    def source(self) -> Any:
        """Which backend serves reads right now: bt's index or public RPCs."""
        st = self._get("/status")
        return {"reads": st.get("reads"), "bt": st.get("bt"),
                "block_height": st.get("block_height")}

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

    # the trader pool the leaderboard ranks
    def universe(self) -> Any:
        """How many traders we rank vs how many exist on-chain."""
        return self._get("/universe")

    def set_pool(self, size: int = 250, refresh: bool = False) -> Any:
        """Watch the top `size` coldkeys by stake. Grows in the background."""
        return self._post_q("/pool", size=str(size),
                            refresh="true" if refresh else "false")

    def discover(self, top: int = 8, kind: str = "validator") -> Any:
        """Add the top-N of the on-chain universe now (blocking, snapshots)."""
        return self._post_q("/discover", top=str(top), kind=kind)

    # copies
    def create_copy(self, target_ss58: str, our_hotkey: str,
                    alloc_tao: Optional[float] = None,
                    label: Optional[str] = None,
                    max_tao_per_tx: Optional[float] = None,
                    daily_limit_tao: Optional[float] = None,
                    rebalance_threshold_pct: Optional[float] = None,
                    poll_interval_sec: Optional[int] = None) -> Any:
        """Copy one trader with `alloc_tao` TAO behind them.

        Copies compose: every active one contributes its trader's shape at
        its own size, and the engine blends them into a single book. Run this
        once per trader to copy a set of them.
        """
        body = {
            "target_ss58": target_ss58,
            "our_hotkey": our_hotkey,
            "alloc_tao": alloc_tao,
            "label": label,
            "max_tao_per_tx": max_tao_per_tx,
            "daily_limit_tao": daily_limit_tao,
            "rebalance_threshold_pct": rebalance_threshold_pct,
            "poll_interval_sec": poll_interval_sec,
        }
        return self._post("/copy", {k: v for k, v in body.items() if v is not None})

    def resize_copy(self, copy_id: str, alloc_tao: float) -> Any:
        """Change the TAO behind one trader. The next pass rebalances to it —
        the position is re-weighted, not exited and re-entered."""
        return self._put(f"/copy/{copy_id}", {"alloc_tao": alloc_tao})

    def list_copies(self) -> Any:
        return self._get("/copies")

    def pause_copy(self, copy_id: str) -> Any:
        return self._post(f"/copy/{copy_id}/pause")

    def resume_copy(self, copy_id: str) -> Any:
        return self._post(f"/copy/{copy_id}/resume")

    def delete_copy(self, copy_id: str) -> Any:
        return self._delete(f"/copy/{copy_id}")

    def sync_copy(self, copy_id: str) -> Any:
        """Rebalance now. Runs the whole book — a sleeve applied on its own
        would drag every other trader's money with it."""
        return self._post(f"/copy/{copy_id}/sync")

    def portfolio(self) -> Any:
        """The blended book: every trader you copy, the TAO behind each, and
        the trades that would close the gap. Pure read."""
        return self._get("/portfolio")

    def sync_portfolio(self, dry_run: bool = False) -> Any:
        """Run a portfolio pass now. `dry_run=True` returns the same plan
        without signing anything."""
        return self._post(f"/portfolio/sync?dry_run={str(bool(dry_run)).lower()}")

    # MCP
    def mcp(self) -> Any:
        """How to connect an MCP client, and every tool it gets. The server
        is the running API itself (POST /mcp) — nothing extra to start."""
        out = self._get("/mcp/schema")
        out["tools"] = [{"name": t["name"], "description": t["description"]}
                        for t in out.get("tools", [])]
        return out

    # the strat agent
    def agent_status(self) -> Any:
        """Whether the strat agent can run, and which tools it can reach."""
        return self._get("/agent")

    def ask(self, question: str, session_id: Optional[str] = None,
            stream: bool = False) -> Dict[str, Any]:
        """Ask the strat agent for a basket of traders to mirror.

        Returns {answer, strat, tools, session_id}. Pass `session_id` back to
        keep talking about the same basket; `stream=True` prints each tool
        call as it happens instead of waiting in the dark.

        The proposal is not live and not saved — `strat` is a basket you feed
        to `create_copy` (or the console's strat maker) yourself.
        """
        out: Dict[str, Any] = {"answer": "", "strat": None, "tools": [],
                               "session_id": session_id}
        r = requests.post(
            f"{self.api_url}/agent/ask",
            json={"question": question, "session_id": session_id},
            stream=True, timeout=(10, 420),
        )
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "tool":
                out["tools"].append(ev["name"])
                if stream:
                    print(f"  ▸ {ev['name']} {ev.get('args') or ''}")
            elif kind == "text" and stream:
                print(ev["text"])
            elif kind == "strat":
                out["strat"] = ev["strat"]
            elif kind in ("start", "done"):
                out["session_id"] = ev.get("session_id") or out["session_id"]
                if kind == "done":
                    out["answer"] = ev.get("answer") or ""
            elif kind == "error":
                out["error"] = ev.get("error")
        return out

    # wallet
    def set_wallet(self, mnemonic: Optional[str] = None,
                   seed_hex: Optional[str] = None) -> Any:
        body = {k: v for k, v in {"mnemonic": mnemonic, "seed_hex": seed_hex}.items() if v}
        return self._post("/wallet/set", body)

    def wallet_balance(self) -> Any:
        return self._get("/wallet/balance")

    # ── test ──────────────────────────────────────────────────────

    def _check_agent(self) -> Dict[str, Any]:
        st = self._get("/agent")
        if not st.get("ready"):
            raise RuntimeError(st.get("hint") or "strat agent has no auth")
        return {"model": st.get("model"), "tools": len(st.get("tools") or [])}

    def test(self) -> Dict[str, Any]:
        """Hit every public read endpoint + show the RPC pool."""
        results: Dict[str, Any] = {}
        for name, fn in [
            ("health", lambda: self._get("/health")),
            ("subnets", lambda: self._get("/subnets")),
            ("leaderboard", lambda: self._get("/leaderboard", days="7", top="10")),
            ("status", lambda: self._get("/status")),
            ("curve", lambda: self.account_curve(self._first_watched(), days=7)),
            # Not a conversation — just that the agent has auth and a toolbox.
            ("agent", self._check_agent),
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
