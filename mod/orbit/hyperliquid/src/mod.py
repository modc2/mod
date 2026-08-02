"""
hyperliquid — copy-trading + indexes on Hyperliquid

High-level manager:
    - builds the Rust API (cargo build --release)
    - serves the Rust API on `api_port`
    - serves the Next.js app on `app_port`
    - exposes a `forward(fn=..., **kwargs)` dispatch that mirrors the
      Rust /forward endpoint for mod-protocol callers

Layout:
    src/
      mod.py           # this file
      api/             # Rust API (axum)
        Cargo.toml
        src/...
      app/             # Next.js app
        package.json
        app/...
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


def _has_docker() -> bool:
    """Check if docker compose is available."""
    try:
        subprocess.run(["docker", "compose", "version"],
                       capture_output=True, timeout=5)
        return True
    except Exception:
        return False


class Hyperliquid(m.Mod):
    """Hyperliquid copy-trading + indexes orchestrator."""

    name = "hyperliquid"
    description = (
        "Hyperliquid DEX — copy traders by N-day performance, build "
        "weighted indexes, and back them with private vaults"
    )
    fns = [
        "forward", "serve", "app", "api", "kill", "status",
        "build", "logs", "test",
        "build_cid", "publish_build", "build_onchain",
        # MCP tool server (the same fn surface, spoken as JSON-RPC)
        "mcp", "mcp_tools", "mcp_call", "mcp_config",
        # strategies (modular Python classes)
        "strat", "list_strats", "run_strat",
        # data passthroughs
        "top_traders", "analyze_trader", "leaderboard",
        # indexes
        "list_indexes", "get_index", "create_index", "update_index",
        "delete_index", "index_perf", "auto_index",
        # follows
        "list_follows", "create_follow", "update_follow", "delete_follow",
        "pause_follow", "resume_follow", "list_signals",
        # vaults
        "list_vaults", "vault_details", "vault_perf", "vault_intent",
        "create_vault", "vault_transfer",
        # backend signer / agent
        "signer_address", "agent_status", "approve_agent_intent",
        # trading
        "trade", "cancel", "cancel_by_cloid", "modify", "set_leverage",
        "update_isolated_margin", "schedule_cancel", "action",
        # transfers / bridging
        "usd_class_transfer", "withdraw", "usd_send", "spot_send",
        # referrer
        "set_referrer",
        # live copy-trade engine
        "live_start", "live_stop", "live_status",
        # market / wallet data
        "mids", "meta", "orderbook", "candles", "wallet_config",
        "user_state", "user_fills", "user_pnl", "user_orders", "user_funding",
    ]

    api_port = 8919
    app_port = 3919

    def __init__(self, testnet: bool = False, api_url: Optional[str] = None,
                 token: Optional[str] = None, **kwargs):
        self.testnet = testnet or os.environ.get("HYPERLIQUID_TESTNET", "").lower() == "true"
        self.api_url = api_url or os.environ.get(
            "HL_API_URL", f"http://localhost:{self.api_port}"
        )
        # Wallet-scoped fns (follows, signer, trading, live engine) are gated
        # behind a mod protocol-auth token. Public reads need none.
        self.token = token or os.environ.get("HYPERLIQUID_TOKEN", "")

    # ── lifecycle ────────────────────────────────────────────────────

    def build(self, release: bool = True) -> Dict[str, Any]:
        """Compile the Rust API. Idempotent — `cargo` skips work if up to date."""
        if not shutil.which("cargo"):
            return {"ok": False, "error": "cargo not on PATH — install rust toolchain"}
        cmd = ["cargo", "build"]
        if release:
            cmd.append("--release")
        proc = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True)
        return {
            "ok": proc.returncode == 0,
            "release": release,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

    def _api_binary(self) -> str:
        rel = os.path.join(API_DIR, "target", "release", "hyperliquid-api")
        dbg = os.path.join(API_DIR, "target", "debug", "hyperliquid-api")
        if os.path.exists(rel): return rel
        if os.path.exists(dbg): return dbg
        return ""

    # ── docker lifecycle ──────────────────────────────────────────

    def _docker_serve(self, gateway_port: int = 3000, build: bool = True) -> Dict[str, Any]:
        """Start via docker compose."""
        env = {**os.environ, "GATEWAY_PORT": str(gateway_port)}
        cmd = ["docker", "compose", "up", "-d", "--build"] if build else \
              ["docker", "compose", "up", "-d"]
        proc = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, env=env)
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "mode": "docker",
            "gateway": f"http://localhost:{gateway_port}/hyperliquid",
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
                container = "running" if proc.stdout.strip() else "stopped"
        out = {"module": "hyperliquid", "mode": "docker", "container": container}
        try:
            r = requests.get(f"{self.api_url}/status", timeout=2)
            out["api_status"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["api_status"] = {"error": str(e)}
        return out

    def _docker_logs(self, lines: int = 50) -> str:
        proc = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(lines)],
            cwd=ROOT_DIR, capture_output=True, text=True,
        )
        return proc.stdout or proc.stderr or "<no output>"

    def _detect_mode(self) -> str:
        """Resolve the live deployment mode: a running docker container wins,
        then pm2-managed local processes, else local."""
        try:
            proc = subprocess.run(
                ["docker", "compose", "ps", "--status", "running", "-q"],
                cwd=ROOT_DIR, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return "docker"
        except Exception:
            pass
        try:
            pm2 = m.mod("pm.pm2")()
            if pm2.exists("hyperliquid-api") or pm2.exists("hyperliquid-app"):
                return "local"
        except Exception:
            pass
        return "local"

    # ── local lifecycle (pm2 / subprocess) ────────────────────────

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
        env_lines = [
            f"export PORT={port}",
            f'export HYPERLIQUID_TESTNET={"true" if self.testnet else "false"}',
        ]
        with open(script, "w") as f:
            f.write("#!/bin/bash\n" + "\n".join(env_lines) + f"\nexec {bin_path}\n")
        os.chmod(script, 0o755)

        try:
            pm2 = m.mod("pm.pm2")()
            name = "hyperliquid-api"
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=SRC_DIR, interpreter="bash")
            return {"ok": True, "manager": "pm2", "name": name, "port": port,
                    "url": f"http://localhost:{port}"}
        except Exception as e:
            proc = subprocess.Popen(["bash", script], cwd=SRC_DIR)
            return {"ok": True, "manager": "subprocess", "pid": proc.pid,
                    "port": port, "url": f"http://localhost:{port}",
                    "warn": f"pm2 unavailable ({e}); using subprocess"}

    def app(self, port: Optional[int] = None, dev: bool = True,
            install: bool = True) -> Dict[str, Any]:
        """Start the Next.js app under pm2."""
        port = port or self.app_port
        if install and not os.path.isdir(os.path.join(APP_DIR, "node_modules")):
            r = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                               cwd=APP_DIR, capture_output=True, text=True)
            if r.returncode != 0:
                return {"ok": False, "stage": "install", "stderr": r.stderr[-2000:]}

        cmd = (
            f"npm run dev -- -p {port}" if dev
            else f"npm run build && npm run start -- -p {port}"
        )
        script = os.path.join(SRC_DIR, "_app.sh")
        with open(script, "w") as f:
            f.write(
                "#!/bin/bash\n"
                f"export HL_API_URL={self.api_url}\n"
                f"cd {APP_DIR}\n"
                f"{cmd}\n"
            )
        os.chmod(script, 0o755)

        try:
            pm2 = m.mod("pm.pm2")()
            name = "hyperliquid-app"
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=APP_DIR, interpreter="bash")
            return {"ok": True, "manager": "pm2", "name": name, "port": port,
                    "url": f"http://localhost:{port}"}
        except Exception as e:
            proc = subprocess.Popen(["bash", script], cwd=APP_DIR)
            return {"ok": True, "manager": "subprocess", "pid": proc.pid,
                    "port": port, "url": f"http://localhost:{port}",
                    "warn": f"pm2 unavailable ({e}); using subprocess"}

    def serve(self, mode=None, api_port: Optional[int] = None,
              app_port: Optional[int] = None, gateway_port: int = 3000,
              dev: bool = True, build: bool = True, **kwargs) -> Dict[str, Any]:
        """Start hyperliquid. mode='local' (pm2, default) or 'docker'."""
        if mode is None:
            mode = "local"

        if mode == "docker":
            result = self._docker_serve(gateway_port=gateway_port, build=build)
            if result["ok"]:
                self._register_gateway(self.api_port, self.app_port)
            return result

        # local mode (pm2)
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port

        a = self.api(port=api_port, build=build)
        if not a.get("ok"):
            return {"ok": False, "mode": "local", "api": a}
        # let the API come up before the Next dev server tries to proxy
        time.sleep(1.0)
        b = self.app(port=app_port, dev=dev)

        self._register_gateway(api_port, app_port)

        return {"ok": a.get("ok") and b.get("ok"), "mode": "local", "api": a, "app": b}

    def _register_gateway(self, api_port: int, app_port: int):
        """Persist urls to config.json and register in the :3000 gateway app namespace."""
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg["urls"] = {
                "api": f"http://localhost:{api_port}",
                "app": f"http://localhost:{app_port}/hyperliquid",
                "gateway": "http://localhost:3000/hyperliquid",
            }
            cfg["ports"] = {"api": api_port, "app": app_port}
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
                f.write("\n")
        except Exception:
            pass

        # Gateway middleware routes /hyperliquid/* and /api/hyperliquid/* from
        # the app_namespace registry — register the same way polymarket does.
        try:
            ns = m.mod("server.namespace")()
            ns.reg_app(
                "hyperliquid",
                f"http://localhost:{app_port}",
                api_url=f"http://localhost:{api_port}",
            )
        except Exception as e:
            print(f"[hyperliquid] gateway registration failed: {e}")

    def kill(self, target: str = "all", mode=None) -> Dict[str, Any]:
        """Stop services. mode defaults to whatever is actually running."""
        if mode is None:
            mode = self._detect_mode()

        if target == "all":
            try:
                m.mod("server.namespace")().dereg_app("hyperliquid")
            except Exception:
                pass

        if mode == "docker":
            return self._docker_kill()

        out: Dict[str, Any] = {}
        try:
            pm2 = m.mod("pm.pm2")()
            if target in ("api", "all") and pm2.exists("hyperliquid-api"):
                pm2.kill("hyperliquid-api"); out["api"] = "stopped"
            if target in ("app", "all") and pm2.exists("hyperliquid-app"):
                pm2.kill("hyperliquid-app"); out["app"] = "stopped"
        except Exception as e:
            out["error"] = str(e)
        out["mode"] = "local"
        return out

    def status(self, mode=None) -> Dict[str, Any]:
        """Service status. mode defaults to whatever is actually running."""
        if mode is None:
            mode = self._detect_mode()

        if mode == "docker":
            return self._docker_status()

        out: Dict[str, Any] = {"module": "hyperliquid", "mode": "local",
                               "testnet": self.testnet, "api_url": self.api_url}
        try:
            pm2 = m.mod("pm.pm2")()
            out["api"] = "running" if pm2.exists("hyperliquid-api") else "stopped"
            out["app"] = "running" if pm2.exists("hyperliquid-app") else "stopped"
        except Exception:
            out["api"] = out["app"] = "unknown"
        try:
            r = requests.get(f"{self.api_url}/status", timeout=2)
            out["api_status"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["api_status"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 100, mode=None) -> str:
        """Fetch logs. mode defaults to whatever is actually running."""
        if mode is None:
            mode = self._detect_mode()

        if mode == "docker":
            return self._docker_logs(lines=lines)

        try:
            pm2 = m.mod("pm.pm2")()
            return pm2.logs(f"hyperliquid-{target}", lines=lines)
        except Exception as e:
            return f"<no pm2 logs: {e}>"

    # ── data passthroughs (use the Rust API) ──────────────────────

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, _timeout: float = 30, **params) -> Any:
        # Top-traders scan can legitimately take ~60s under HL's /info
        # rate limiting (parallel=2 × up to 8 retries × ~4s backoff). Keep
        # short timeouts for everything else but give the scan budget.
        if path.startswith("/traders/top") or path.startswith("/trader/"):
            _timeout = max(_timeout, 120)
        r = requests.get(f"{self.api_url}{path}", params=params,
                         headers=self._headers(), timeout=_timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Any) -> Any:
        r = requests.post(f"{self.api_url}{path}", json=body,
                          headers=self._headers(), timeout=60)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: Any) -> Any:
        r = requests.patch(f"{self.api_url}{path}", json=body,
                           headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> Any:
        r = requests.delete(f"{self.api_url}{path}",
                            headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def top_traders(self, days: int = 7, min_per_day: float = 1.0,
                    pool: int = 150, seed: Optional[List[str]] = None) -> Any:
        params = {"days": days, "min_per_day": min_per_day, "pool": pool}
        if seed: params["seed"] = ",".join(seed)
        return self._get("/traders/top", **params)

    def analyze_trader(self, address: str, days: int = 7) -> Any:
        return self._get(f"/trader/{address}/analyze", days=days)

    def leaderboard(self) -> Any: return self._get("/leaderboard")

    # indexes
    def list_indexes(self) -> Any: return self._get("/indexes")
    def get_index(self, id: str) -> Any: return self._get(f"/indexes/{id}")
    def create_index(self, **body) -> Any: return self._post("/indexes", body)
    def update_index(self, id: str, **body) -> Any: return self._patch(f"/indexes/{id}", body)
    def delete_index(self, id: str) -> Any: return self._delete(f"/indexes/{id}")
    def index_perf(self, id: str, days: Optional[int] = None) -> Any:
        return self._get(f"/indexes/{id}/perf", **({"days": days} if days else {}))
    def auto_index(self, **body) -> Any: return self._post("/indexes/auto", body)

    # follows
    def list_follows(self, follower: Optional[str] = None) -> Any:
        return self._get("/follows", **({"follower": follower} if follower else {}))
    def create_follow(self, **body) -> Any: return self._post("/follows", body)
    def update_follow(self, id: str, **body) -> Any: return self._patch(f"/follows/{id}", body)
    def delete_follow(self, id: str) -> Any: return self._delete(f"/follows/{id}")
    def pause_follow(self, id: str) -> Any: return self._post(f"/follows/{id}/pause", {})
    def resume_follow(self, id: str) -> Any: return self._post(f"/follows/{id}/resume", {})
    def list_signals(self, follower: Optional[str] = None, limit: int = 100) -> Any:
        params: Dict[str, Any] = {"limit": limit}
        if follower: params["follower"] = follower
        return self._get("/signals", **params)

    # vaults
    def list_vaults(self) -> Any: return self._get("/vaults")
    def vault_details(self, address: str) -> Any: return self._get(f"/vaults/{address}")
    def vault_perf(self, address: str) -> Any: return self._get(f"/vaults/{address}/perf")
    def vault_intent(self, index_id: str, initial_usd: float, nonce: Optional[int] = None) -> Any:
        body: Dict[str, Any] = {"initial_usd": initial_usd}
        if nonce is not None: body["nonce"] = nonce
        return self._post(f"/indexes/{index_id}/vault/intent", body)

    # ── market / wallet data passthroughs ──

    def mids(self) -> Any: return self._get("/mids")
    def meta(self) -> Any: return self._get("/market/meta")
    def wallet_config(self) -> Any: return self._get("/wallet/config")
    def orderbook(self, coin: str) -> Any: return self._get(f"/orderbook/{coin}")
    def candles(self, coin: str, interval: str = "1h", hours: int = 24) -> Any:
        return self._get(f"/candles/{coin}", interval=interval, hours=hours)

    def user_state(self, addr: str) -> Any:   return self._get(f"/user/{addr}/state")
    def user_fills(self, addr: str) -> Any:   return self._get(f"/user/{addr}/fills")
    def user_pnl(self, addr: str) -> Any:     return self._get(f"/user/{addr}/pnl")
    def user_orders(self, addr: str) -> Any:  return self._get(f"/user/{addr}/orders")
    def user_funding(self, addr: str) -> Any: return self._get(f"/user/{addr}/funding")

    # ── backend agent signer ──

    def signer_address(self, eoa: str) -> Any:
        """Get (or generate) the backend agent address for an EOA. The
        master wallet must `approveAgent` this address before the engine
        can trade for them."""
        return self._post("/signer/address", {"eoa": eoa})

    def agent_status(self, eoa: str) -> Any:
        """Is the backend agent for this EOA approved on Hyperliquid yet?"""
        return self._get("/agent/status", eoa=eoa)

    def approve_agent_intent(self, eoa: str, agent_name: Optional[str] = None) -> Any:
        """Return the `approveAgent` action + EIP-712 digest the user's
        master wallet must sign in their browser. Once signed, POST
        {action, nonce, signature} to /exchange via `forward(fn='exchange_post')`."""
        body: Dict[str, Any] = {"eoa": eoa}
        if agent_name: body["agent_name"] = agent_name
        return self._post("/signer/approve_agent", body)

    # ── trading (signed by backend agent) ──

    def trade(self, eoa: str, coin: str, is_buy: bool, size: float,
              price: Optional[float] = None, tif: Optional[str] = None,
              reduce_only: bool = False, slippage_bps: Optional[int] = None,
              cloid: Optional[str] = None, vault_address: Optional[str] = None) -> Any:
        """Place an order. Omit `price` for a slippage-padded IOC market order."""
        body: Dict[str, Any] = {"eoa": eoa, "coin": coin, "is_buy": is_buy,
                                "size": size, "reduce_only": reduce_only}
        if price is not None: body["price"] = price
        if tif is not None: body["tif"] = tif
        if slippage_bps is not None: body["slippage_bps"] = slippage_bps
        if cloid is not None: body["cloid"] = cloid
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/trade", body)

    def cancel(self, eoa: str, cancels: List[Dict[str, Any]],
               vault_address: Optional[str] = None) -> Any:
        """cancels = [{coin, oid}, ...]"""
        body: Dict[str, Any] = {"eoa": eoa, "cancels": cancels}
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/cancel", body)

    def cancel_by_cloid(self, eoa: str, cancels: List[Dict[str, Any]],
                        vault_address: Optional[str] = None) -> Any:
        """cancels = [{coin, cloid}, ...]"""
        body: Dict[str, Any] = {"eoa": eoa, "cancels": cancels}
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/cancel_by_cloid", body)

    def modify(self, eoa: str, oid: int, coin: str, is_buy: bool,
               price: float, size: float, reduce_only: bool = False,
               tif: Optional[str] = None, vault_address: Optional[str] = None) -> Any:
        body: Dict[str, Any] = {
            "eoa": eoa, "oid": oid, "coin": coin, "is_buy": is_buy,
            "price": price, "size": size, "reduce_only": reduce_only,
        }
        if tif is not None: body["tif"] = tif
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/modify", body)

    def set_leverage(self, eoa: str, coin: str, leverage: int, is_cross: bool = True,
                     vault_address: Optional[str] = None) -> Any:
        body: Dict[str, Any] = {"eoa": eoa, "coin": coin, "leverage": leverage,
                                "is_cross": is_cross}
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/leverage", body)

    def update_isolated_margin(self, eoa: str, coin: str, is_buy: bool,
                               amount_usd: float, vault_address: Optional[str] = None) -> Any:
        """Add (positive) or remove (negative) isolated margin on an open position."""
        body: Dict[str, Any] = {"eoa": eoa, "coin": coin, "is_buy": is_buy,
                                "amount_usd": amount_usd}
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/isolated_margin", body)

    def schedule_cancel(self, eoa: str, time_ms: Optional[int] = None,
                        vault_address: Optional[str] = None) -> Any:
        """Dead-man's-switch: cancel everything at `time_ms` if no further heartbeat."""
        body: Dict[str, Any] = {"eoa": eoa}
        if time_ms is not None: body["time_ms"] = time_ms
        if vault_address is not None: body["vault_address"] = vault_address
        return self._post("/schedule_cancel", body)

    def action(self, eoa: str, action: Dict[str, Any],
               vault_address: Optional[str] = None,
               nonce: Optional[int] = None) -> Any:
        """Generic signed-action passthrough — sign any L1 action with the
        backend agent and POST to /exchange. The action JSON's key order
        matters (HL hashes msgpack(action))."""
        body: Dict[str, Any] = {"eoa": eoa, "action": action}
        if vault_address is not None: body["vault_address"] = vault_address
        if nonce is not None: body["nonce"] = nonce
        return self._post("/action", body)

    # ── transfers / bridging ──

    def usd_class_transfer(self, eoa: str, amount: str, to_perp: bool) -> Any:
        """Move USDC between perp and spot wallets. `amount` is a string ('10.5')."""
        return self._post("/usd_class_transfer", {"eoa": eoa, "amount": amount, "to_perp": to_perp})

    def vault_transfer(self, eoa: str, vault: str, is_deposit: bool, amount_usd: float) -> Any:
        return self._post("/vault_transfer", {"eoa": eoa, "vault": vault,
                                              "is_deposit": is_deposit, "amount_usd": amount_usd})

    def withdraw(self, eoa: str, destination: str, amount: str) -> Any:
        """Withdraw USDC to L1 (Arbitrum)."""
        return self._post("/withdraw", {"eoa": eoa, "destination": destination, "amount": amount})

    def usd_send(self, eoa: str, destination: str, amount: str) -> Any:
        """HL → HL USDC transfer."""
        return self._post("/usd_send", {"eoa": eoa, "destination": destination, "amount": amount})

    def spot_send(self, eoa: str, destination: str, token: str, amount: str) -> Any:
        """HL → HL spot token transfer. `token` is 'SYMBOL:0x<tokenId>'."""
        return self._post("/spot_send", {"eoa": eoa, "destination": destination,
                                         "token": token, "amount": amount})

    def create_vault(self, eoa: str, name: str, initial_usd: float,
                     description: str = "") -> Any:
        return self._post("/create_vault", {"eoa": eoa, "name": name,
                                            "initial_usd": initial_usd,
                                            "description": description})

    def set_referrer(self, eoa: str, code: str) -> Any:
        return self._post("/set_referrer", {"eoa": eoa, "code": code})

    # ── live copy-trade engine ──

    def live_start(self, eoa: str, traders: List[Dict[str, Any]],
                   interval_ms: int = 15000, size_pct: float = 10.0,
                   max_per_trade_usd: float = 0.0,
                   min_order_size_usd: float = 10.0,
                   max_slippage_bps: int = 100,
                   coins_allow: Optional[List[str]] = None,
                   coins_deny: Optional[List[str]] = None,
                   vault_address: Optional[str] = None,
                   capital: Optional[float] = None,
                   strategy_id: Optional[str] = None) -> Any:
        """Start an autonomous copy-trade session for `eoa`.

        traders = [{address, weight=1.0, enabled=true}, ...]"""
        body: Dict[str, Any] = {
            "eoa": eoa, "traders": traders, "interval_ms": interval_ms,
            "size_pct": size_pct, "max_per_trade_usd": max_per_trade_usd,
            "min_order_size_usd": min_order_size_usd,
            "max_slippage_bps": max_slippage_bps,
        }
        if coins_allow is not None: body["coins_allow"] = coins_allow
        if coins_deny is not None: body["coins_deny"] = coins_deny
        if vault_address is not None: body["vault_address"] = vault_address
        if capital is not None: body["capital"] = capital
        if strategy_id is not None: body["strategy_id"] = strategy_id
        return self._post("/live/start", body)

    def live_stop(self, eoa: str) -> Any:
        return self._post("/live/stop", {"eoa": eoa})

    def live_status(self, eoa: str) -> Any:
        return self._get("/live/status", eoa=eoa)

    # ── MCP tool server (same fn surface, spoken as JSON-RPC 2.0) ──
    #
    # The Rust API serves the tools at POST /mcp; these are the thin mod-side
    # client. Each tool fronts one of `fns` above — /mcp/schema publishes the
    # tool → fn → route mapping, which `mcp_tools()` checks against this class.

    def mcp(self) -> Any:
        """The MCP tool schema and its mod-protocol mapping (public)."""
        return self._get("/mcp/schema")

    def mcp_tools(self) -> List[Dict[str, Any]]:
        """Compact tool list: name, the fn it fronts, route, and whether that
        fn exists here. `bound` false means schema drift — the tool names a fn
        this module does not expose."""
        schema = self.mcp()
        return [{
            "name": t["name"],
            "fn": t["fn"],
            "route": f'{t["method"]} {t["path"]}',
            "public": t["public"],
            "bound": t["fn"] in self.fns and hasattr(self, t["fn"]),
        } for t in schema.get("tools", [])]

    def mcp_call(self, tool: str, arguments: Optional[Dict[str, Any]] = None,
                 **kwargs) -> Any:
        """Invoke an MCP tool over the same JSON-RPC wire an MCP client uses."""
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": dict(arguments or {}, **kwargs)},
        }
        r = requests.post(f"{self.api_url}/mcp", json=body,
                          headers=self._headers(), timeout=300)
        r.raise_for_status()
        msg = r.json()
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        result = msg.get("result", {})
        if result.get("isError"):
            raise RuntimeError(result.get("content", [{}])[0].get("text", "tool error"))
        if "structuredContent" in result:
            return result["structuredContent"]
        text = result.get("content", [{}])[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def mcp_config(self) -> Dict[str, Any]:
        """Client config snippets for registering this server (stdio + HTTP)."""
        binary = self._api_binary() or os.path.join(
            API_DIR, "target", "release", "hyperliquid-api")
        return {
            "stdio": {
                "command": binary,
                "args": ["--stdio"],
                "env": {"HL_API_URL": self.api_url,
                        "HYPERLIQUID_TOKEN": "<mod protocol token, for wallet-scoped tools>"},
            },
            "http": {
                "type": "http",
                "url": f"{self.api_url}/mcp",
                "headers": {"Authorization": "Bearer <mod protocol token>"},
            },
            # Same server through the public gateway (mod protocol URL shape).
            "gateway": {"type": "http", "url": "/api/hyperliquid/mcp"},
            "add_cmd": f"claude mcp add hyperliquid -- {binary} --stdio",
        }

    # ── Modular strategies (see strat.py) ──

    def strat(self, name: str, **kwargs: Any):
        """Instantiate a strategy by name. Returns the Strat object —
        call `.start(self, eoa)`, `.status(self, eoa)`, `.stop(self, eoa)`.

            s = hl.strat('top_n', n=10, days=7, size_pct=5)
            s.start(hl, eoa='0xabc…')
        """
        from . import strats as _strats
        return _strats.make(name, **kwargs)

    def list_strats(self) -> Any:
        """Enumerate registered strategies (name + description)."""
        from . import strats as _strats
        return _strats.list_strats()

    def run_strat(self, name: str, eoa: str, **kwargs: Any) -> Any:
        """One-shot helper: instantiate + start a strat for `eoa`.

            hl.run_strat('top_n', eoa='0xabc…', n=10, days=7, size_pct=5)
        """
        s = self.strat(name, **kwargs)
        return s.start(self, eoa)

    # ── build CID + onchain publish ──────────────────────────────

    # Folders that don't belong in the build hash — generated, vendored, or
    # otherwise non-source. Keeps the CID stable across `npm install`,
    # `cargo build`, and Next.js dev rebuilds.
    _BUILD_IGNORE = [
        "node_modules", "target", ".next", "__pycache__",
        ".git", "artifacts", "cache",
    ]

    # Arbitrum One — hyperliquid's native settlement chain, so the same key
    # the user funds for HL can record build provenance. Public RPC fallbacks.
    _ARB_RPCS = [
        "https://arbitrum.drpc.org",
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.io/rpc",
    ]
    _ARB_CHAIN_ID = 42161
    _ARB_SCAN = "https://arbiscan.io/tx/"

    def build_cid(self, mod: str = "hyperliquid") -> str:
        """Localfs CID over the module's source files.

        IPFS-compatible (CIDv0 Qm...) — recompute locally via
        `m.mod('localfs')().cid(m.content('<mod>', ignore_folders=[...]))`
        with the same ignore list.
        """
        lfs = m.mod("localfs")()
        content = m.content(mod, ignore_folders=self._BUILD_IGNORE)
        return lfs.cid(content)

    def _arb_w3(self):
        from web3 import Web3
        last_err = None
        for url in self._ARB_RPCS:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
                if w3.is_connected() and w3.eth.chain_id == self._ARB_CHAIN_ID:
                    return w3, url
            except Exception as e:
                last_err = e
        raise RuntimeError(f"no Arbitrum RPC reachable; last error: {last_err}")

    def publish_build(self, mod: str = "hyperliquid", key: str = None,
                      tag: str = "mod-cid:", dry_run: bool = False) -> Dict[str, Any]:
        """Send an Arbitrum tx whose calldata records the build CID.

        Self-transfer with `tag + cid` as calldata — no contract required.
        The CID lands in the tx's `input` field, verifiable on Arbiscan.
        Persists the result under `build_onchain` in config.json.
        """
        from web3 import Web3

        cid = self.build_cid(mod=mod)
        signer = m.key(key) if key else m.key()
        addr = Web3.to_checksum_address(signer.address)
        data = "0x" + (tag.encode() + cid.encode()).hex()

        w3, rpc = self._arb_w3()
        bal = w3.eth.get_balance(addr)

        gas_price = int(w3.eth.gas_price * 12 / 10)
        gas_limit = 21000 + 16 * len(tag + cid) + 4096  # padding for safety
        cost = gas_price * gas_limit

        if dry_run or bal < cost:
            return {
                "ok": False if bal < cost else True,
                "dry_run": dry_run,
                "cid": cid,
                "mod": mod,
                "from": addr,
                "rpc": rpc,
                "data": data,
                "gas_price_gwei": float(w3.from_wei(gas_price, "gwei")),
                "estimated_cost_eth": float(w3.from_wei(cost, "ether")),
                "balance_eth": float(w3.from_wei(bal, "ether")),
                "funded": bal >= cost,
                "needed_eth": float(w3.from_wei(max(cost - bal, 0), "ether")),
            }

        nonce = w3.eth.get_transaction_count(addr)
        tx = {
            "to": addr,
            "value": 0,
            "data": data,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "chainId": self._ARB_CHAIN_ID,
        }
        signed = w3.eth.account.sign_transaction(tx, signer.private_key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

        thash = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        record = {
            "cid": cid,
            "mod": mod,
            "network": "arbitrum",
            "chain_id": self._ARB_CHAIN_ID,
            "tx_hash": thash,
            "block": receipt.blockNumber,
            "from": addr,
            "to": addr,
            "scan": self._ARB_SCAN + thash,
            "at": int(time.time()),
        }
        self._save_build_onchain(record)
        return {"ok": receipt.status == 1, **record}

    def _save_build_onchain(self, record: Dict[str, Any]):
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        cfg["build_onchain"] = record
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=4)
            f.write("\n")

    def build_onchain(self) -> Dict[str, Any]:
        """Return the last published build record (cid, tx, arbiscan link)."""
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            return cfg.get("build_onchain", {})
        except Exception:
            return {}

    # ── test ──────────────────────────────────────────────────────

    def test(self) -> Dict[str, Any]:
        """Test the API by hitting key endpoints."""
        results: Dict[str, Any] = {}

        def _probe(name: str, path: str, timeout: float = 10):
            try:
                r = requests.get(f"{self.api_url}{path}", timeout=timeout)
                results[name] = {"ok": r.ok, "status": r.status_code}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}

        _probe("status", "/status", timeout=5)
        _probe("mids", "/mids")
        _probe("meta", "/market/meta")
        _probe("leaderboard", "/leaderboard", timeout=20)
        _probe("mcp_schema", "/mcp/schema", timeout=5)

        # MCP is the same fn surface over JSON-RPC — drive one public tool.
        try:
            results["mcp_call"] = {"ok": self.mcp_call("hl_status").get("ok") is True}
        except Exception as e:
            results["mcp_call"] = {"ok": False, "error": str(e)}

        passed = sum(1 for v in results.values() if v.get("ok"))
        total = len(results)
        results["summary"] = f"{passed}/{total} passed"
        results["ok"] = passed == total
        return results

    # ── mod-protocol forward ──

    def forward(self, fn: Optional[str] = None, **kwargs) -> Any:
        """Dispatch any whitelisted method by name (mod-protocol entry)."""
        if fn is None:
            return {"module": self.name, "fns": self.fns,
                    "api": self.api_url, "testnet": self.testnet}
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


# Class alias kept for compatibility with `m.mod('hyperliquid')` lookups
Mod = Hyperliquid
