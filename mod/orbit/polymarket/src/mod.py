"""
polymarket — Rust API + Next.js frontend for Polymarket trading & analytics

Layout:
    src/
      mod.py       # this file (orchestrator)
      api/         # Rust Axum binary
      app/         # Next.js frontend
"""

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

import requests
import mod as m

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
API_DIR = os.path.join(SRC_DIR, "api")
TARGET_DIR = os.path.join(API_DIR, "target")
APP_DIR = os.path.join(SRC_DIR, "app")


def _has_docker() -> bool:
    """Check if docker compose is available."""
    try:
        subprocess.run(["docker", "compose", "version"],
                       capture_output=True, timeout=5)
        return True
    except Exception:
        return False


class Polymarket(m.Mod):
    """Polymarket prediction market — trading, data, copy-trading analytics."""

    name = "polymarket"
    description = "Polymarket prediction market — trading, data, scraping, backtesting (Rust-powered)"
    fns = [
        "serve", "kill", "status", "build", "logs", "test",
        "search", "markets", "trending", "events",
        "active_traders", "forward",
        "build_cid", "publish_build", "build_onchain",
    ]

    api_port = 50091
    app_port = 3091

    def __init__(self, **kwargs):
        self.api_url = os.environ.get("POLYMARKET_API_URL", f"http://localhost:{self.api_port}")
        self._mode = None  # set by serve/kill to track active mode

    # ── build / binary ───────────────────────────────────────────

    def build(self, release=True, docker=False) -> Dict[str, Any]:
        """Build the Rust API binary (or Docker image)."""
        if docker:
            proc = subprocess.run(
                ["docker", "compose", "build"],
                cwd=ROOT_DIR, capture_output=True, text=True,
            )
            return {
                "ok": proc.returncode == 0,
                "mode": "docker",
                "stderr_tail": proc.stderr[-2000:] if proc.returncode != 0 else "",
            }
        if not shutil.which("cargo"):
            return {"ok": False, "error": "cargo not on PATH"}
        cmd = ["cargo", "build"]
        if release:
            cmd.append("--release")
        proc = subprocess.run(cmd, cwd=API_DIR, capture_output=True, text=True)
        return {
            "ok": proc.returncode == 0,
            "release": release,
            "stderr_tail": proc.stderr[-2000:] if proc.returncode != 0 else "",
        }

    def _api_binary(self) -> str:
        rel = os.path.join(TARGET_DIR, "release", "polymarket-api")
        dbg = os.path.join(TARGET_DIR, "debug", "polymarket-api")
        if os.path.exists(rel):
            return rel
        if os.path.exists(dbg):
            return dbg
        return ""

    # ── docker lifecycle ──────────────────────────────────────────

    def _docker_serve(self, gateway_port: int = 3000, build: bool = True) -> Dict[str, Any]:
        """Start via docker compose."""
        env = {**os.environ, "GATEWAY_PORT": str(gateway_port)}
        if build:
            proc = subprocess.run(
                ["docker", "compose", "up", "-d", "--build"],
                cwd=ROOT_DIR, capture_output=True, text=True, env=env,
            )
        else:
            proc = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=ROOT_DIR, capture_output=True, text=True, env=env,
            )
        ok = proc.returncode == 0
        self._mode = "docker" if ok else None
        return {
            "ok": ok,
            "mode": "docker",
            "gateway": f"http://localhost:{gateway_port}/polymarket",
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
        out = {"module": "polymarket", "mode": "docker", "container": container}
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

    def _detect_mode(self) -> str:
        """Resolve the live deployment mode: a running docker container wins,
        then pm2-managed local processes, then the mode set by serve/kill."""
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
            if pm2.exists("polymarket-api") or pm2.exists("polymarket-app"):
                return "local"
        except Exception:
            pass
        return self._mode or ("docker" if _has_docker() else "local")

    # ── local lifecycle (pm2 / subprocess) ────────────────────────

    def api(self, port: Optional[int] = None, build: bool = True) -> Dict[str, Any]:
        """Start the Rust API under pm2."""
        port = port or self.api_port
        if build:
            bin_path = self._api_binary()
            if not bin_path:
                r = self.build(release=True)
                if not r["ok"]:
                    return {"ok": False, "stage": "build", **r}

        script = os.path.join(API_DIR, "start.sh")
        name = "polymarket-api"
        env = {"PORT": str(port)}
        try:
            pm2 = m.mod("pm.pm2")()
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=API_DIR,
                             interpreter="bash", env=env)
            return {"ok": True, "name": name, "port": port}
        except Exception as e:
            proc = subprocess.Popen(["bash", script], cwd=API_DIR,
                                    env={**os.environ, **env})
            return {"ok": True, "pid": proc.pid, "port": port, "warn": str(e)}

    def app(self, port: Optional[int] = None, api_port: Optional[int] = None,
            dev: bool = True) -> Dict[str, Any]:
        """Start the Next.js app under pm2."""
        port = port or self.app_port
        api_port = api_port or self.api_port
        if not os.path.isdir(os.path.join(APP_DIR, "node_modules")):
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                           cwd=APP_DIR, capture_output=True)

        script = os.path.join(APP_DIR, "start.sh")
        name = "polymarket-app"
        env = {"PORT": str(port), "API_PORT": str(api_port)}
        try:
            pm2 = m.mod("pm.pm2")()
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=APP_DIR,
                             interpreter="bash", env=env)
            return {"ok": True, "name": name, "port": port}
        except Exception as e:
            proc = subprocess.Popen(["bash", script], cwd=APP_DIR,
                                    env={**os.environ, **env})
            return {"ok": True, "pid": proc.pid, "port": port, "warn": str(e)}

    # ── unified serve / kill / status / logs ──────────────────────

    def serve(self, mode=None, api_port=None, app_port=None, gateway_port=3000,
              dev=True, build=True, **kw) -> Dict[str, Any]:
        """Start polymarket. mode='docker' (default) or 'local' (pm2/subprocess)."""
        if mode is None:
            mode = "docker" if _has_docker() else "local"

        if mode == "docker":
            result = self._docker_serve(gateway_port=gateway_port, build=build)
            if result["ok"]:
                self._register_gateway(self.api_port, self.app_port)
            return result

        # local mode (original behavior)
        self._mode = "local"
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port

        a = self.api(port=api_port, build=build)
        if not a.get("ok"):
            return {"ok": False, "mode": "local", "api": a}
        time.sleep(1)
        b = self.app(port=app_port, api_port=api_port, dev=dev)

        self._register_gateway(api_port, app_port)

        return {"ok": a.get("ok") and b.get("ok"), "mode": "local", "api": a, "app": b}

    def _register_gateway(self, api_port: int, app_port: int):
        """Persist urls to config.json and sync with routy gateway."""
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            cfg["urls"] = {
                "api": f"http://localhost:{api_port}",
                "app": f"http://localhost:{app_port}/polymarket",
            }
            cfg["port"] = api_port
            cfg["app_port"] = app_port
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=4)
                f.write("\n")
        except Exception:
            pass

        import threading
        def _sync():
            for port in (3001, 3000):
                try:
                    requests.post(f"http://localhost:{port}/_api/register", json={
                        "name": "polymarket",
                        "target_url": f"http://127.0.0.1:{api_port}",
                        "website_type": "api",
                    }, timeout=3)
                    requests.post(f"http://localhost:{port}/_api/register", json={
                        "name": "polymarket",
                        "target_url": f"http://127.0.0.1:{app_port}",
                        "website_type": "app",
                    }, timeout=3)
                    break
                except Exception:
                    continue
        threading.Thread(target=_sync, daemon=True).start()

    def kill(self, target: str = "all", mode=None) -> Dict[str, Any]:
        """Stop services. mode defaults to whatever is actually running."""
        if mode is None:
            mode = self._detect_mode()

        if mode == "docker":
            return self._docker_kill()

        out = {}
        try:
            pm2 = m.mod("pm.pm2")()
            if target in ("api", "all") and pm2.exists("polymarket-api"):
                pm2.kill("polymarket-api"); out["api"] = "stopped"
            if target in ("app", "all") and pm2.exists("polymarket-app"):
                pm2.kill("polymarket-app"); out["app"] = "stopped"
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

        out = {"module": "polymarket", "mode": "local", "api_url": self.api_url}
        try:
            pm2 = m.mod("pm.pm2")()
            out["api"] = "running" if pm2.exists("polymarket-api") else "stopped"
            out["app"] = "running" if pm2.exists("polymarket-app") else "stopped"
        except Exception:
            out["api"] = out["app"] = "unknown"
        try:
            r = requests.get(f"{self.api_url}/health", timeout=2)
            out["health"] = r.json() if r.ok else {"http": r.status_code}
        except Exception as e:
            out["health"] = {"error": str(e)}
        return out

    def logs(self, target: str = "api", lines: int = 50, mode=None) -> str:
        """Fetch logs. mode defaults to whatever is actually running."""
        if mode is None:
            mode = self._detect_mode()

        if mode == "docker":
            return self._docker_logs(lines=lines)

        try:
            pm2 = m.mod("pm.pm2")()
            return pm2.logs(f"polymarket-{target}", lines=lines)
        except Exception as e:
            return f"<no logs: {e}>"

    # ── build CID + onchain publish ──────────────────────────────

    # Folders that don't belong in the build hash — generated, vendored, or
    # otherwise non-source. Keeps the CID stable across `npm install`,
    # `cargo build`, and Next.js dev rebuilds.
    _BUILD_IGNORE = [
        "node_modules", "target", ".next", "__pycache__",
        ".git", "artifacts", "cache",
    ]

    # Polygon mainnet — RPC fallbacks chosen because polygon-rpc.com and
    # rpc.ankr.com gate-keep with API keys / 401s.
    _POLYGON_RPCS = [
        "https://polygon.drpc.org",
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.llamarpc.com",
    ]
    _POLYGON_CHAIN_ID = 137
    _POLYGON_SCAN = "https://polygonscan.com/tx/"

    def build_cid(self, mod: str = "polymarket") -> str:
        """Localfs CID over the module's source files.

        IPFS-compatible (CIDv0 Qm...) — recompute locally via
        `m.mod('localfs')().cid(m.content('<mod>', ignore_folders=[...]))`
        with the same ignore list.
        """
        lfs = m.mod("localfs")()
        content = m.content(mod, ignore_folders=self._BUILD_IGNORE)
        return lfs.cid(content)

    def _polygon_w3(self):
        from web3 import Web3
        last_err = None
        for url in self._POLYGON_RPCS:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
                if w3.is_connected() and w3.eth.chain_id == self._POLYGON_CHAIN_ID:
                    return w3, url
            except Exception as e:
                last_err = e
        raise RuntimeError(f"no Polygon RPC reachable; last error: {last_err}")

    def publish_build(self, mod: str = "polymarket", key: str = None,
                      tag: str = "mod-cid:", dry_run: bool = False) -> Dict[str, Any]:
        """Send a Polygon tx whose calldata records the build CID.

        Self-transfer with `tag + cid` as calldata — no contract required.
        The CID lands in the tx's `input` field, verifiable on Polygonscan.
        Persists the result under `build_onchain` in config.json so the
        docker entrypoint can expose it to the UI.
        """
        from web3 import Web3

        cid = self.build_cid(mod=mod)
        signer = m.key(key) if key else m.key()
        addr = Web3.to_checksum_address(signer.address)
        data = "0x" + (tag.encode() + cid.encode()).hex()

        w3, rpc = self._polygon_w3()
        bal = w3.eth.get_balance(addr)

        # 21000 base + 16 gas / non-zero calldata byte. Polygon's effective
        # gas price floats — we bump by 20% to keep the tx from getting
        # silently dropped by the public RPCs' mempools.
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
                "estimated_cost_matic": float(w3.from_wei(cost, "ether")),
                "balance_matic": float(w3.from_wei(bal, "ether")),
                "funded": bal >= cost,
                "needed_matic": float(w3.from_wei(max(cost - bal, 0), "ether")),
            }

        nonce = w3.eth.get_transaction_count(addr)
        tx = {
            "to": addr,
            "value": 0,
            "data": data,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "chainId": self._POLYGON_CHAIN_ID,
        }
        signed = w3.eth.account.sign_transaction(tx, signer.private_key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

        record = {
            "cid": cid,
            "mod": mod,
            "network": "polygon",
            "chain_id": self._POLYGON_CHAIN_ID,
            "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
            "block": receipt.blockNumber,
            "from": addr,
            "to": addr,
            "scan": self._POLYGON_SCAN + (tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)),
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
        """Return the last published build record (cid, tx, polygonscan link)."""
        cfg_path = os.path.join(ROOT_DIR, "config.json")
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            return cfg.get("build_onchain", {})
        except Exception:
            return {}

    # ── data passthroughs ────────────────────────────────────────

    def _get(self, path: str, **params) -> Any:
        r = requests.get(f"{self.api_url}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def search(self, query: str) -> Any:
        return self._get("/", endpoint="public-search", q=query)

    def markets(self, limit: int = 20) -> Any:
        return self._get("/", endpoint="markets", _limit=str(limit), active="true")

    def trending(self, limit: int = 20) -> Any:
        return self._get("/", endpoint="markets", _limit=str(limit), active="true", order="volume", ascending="false")

    def events(self, limit: int = 20) -> Any:
        return self._get("/", endpoint="events", _limit=str(limit), active="true")

    def active_traders(self, days: int = 7, pool: int = 1000) -> Any:
        return self._get("/active-traders", days=str(days), pool=str(pool))

    # ── test ──────────────────────────────────────────────────────

    def test(self) -> Dict[str, Any]:
        """Test the API by hitting key endpoints."""
        results = {}

        # health
        try:
            r = requests.get(f"{self.api_url}/health", timeout=5)
            results["health"] = {"ok": r.ok, "status": r.status_code, "data": r.json() if r.ok else r.text[:200]}
        except Exception as e:
            results["health"] = {"ok": False, "error": str(e)}

        # markets
        try:
            r = requests.get(f"{self.api_url}/", params={"endpoint": "markets", "_limit": "3", "active": "true"}, timeout=10)
            data = r.json() if r.ok else None
            results["markets"] = {"ok": r.ok, "status": r.status_code, "count": len(data) if isinstance(data, list) else None}
        except Exception as e:
            results["markets"] = {"ok": False, "error": str(e)}

        # events
        try:
            r = requests.get(f"{self.api_url}/", params={"endpoint": "events", "_limit": "3", "active": "true"}, timeout=10)
            data = r.json() if r.ok else None
            results["events"] = {"ok": r.ok, "status": r.status_code, "count": len(data) if isinstance(data, list) else None}
        except Exception as e:
            results["events"] = {"ok": False, "error": str(e)}

        # active-traders
        try:
            r = requests.get(f"{self.api_url}/active-traders", params={"days": "1", "pool": "10"}, timeout=15)
            results["active_traders"] = {"ok": r.ok, "status": r.status_code}
        except Exception as e:
            results["active_traders"] = {"ok": False, "error": str(e)}

        passed = sum(1 for v in results.values() if v.get("ok"))
        total = len(results)
        results["summary"] = f"{passed}/{total} passed"
        results["ok"] = passed == total
        return results

    # ── forward ──────────────────────────────────────────────────

    def forward(self, fn=None, **kwargs) -> Any:
        if fn is None:
            return {"module": self.name, "fns": self.fns, "api": self.api_url}
        if fn.startswith("_") or fn not in self.fns:
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)


Mod = Polymarket
