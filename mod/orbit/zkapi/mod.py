"""
zkapi — the Ethereum zkAPI framework (vbuterin/zkapi, Open Anonymity + EF) as a mod.

zkAPI lets you pay for a metered API from an Ethereum deposit without the server
learning which deposit is yours: deposit ETH into the vault once, then every
request carries a Groth16 proof (BN254, Poseidon, note-bound Baby-JubJub
commitments) that *some* note in the vault's 32-level Merkle tree can cover it.
The server issues a bounded, short-lived provider key, settles actual usage and
signs your next balance (Schnorr); you withdraw cooperatively with a clearance
signature, or unilaterally through an escape window that a challenger polices.

This module runs the real thing, unmodified, from a pinned upstream checkout
(vendor/zkapi @ UPSTREAM_COMMIT):

  * a persistent local devnet: anvil + ZkApiVault/Groth16ProofAdapter/Bn254Poseidon,
    a Chainlink-shaped ETH/USD feed fed with the live mainnet answer,
    zkapi serverd, zkapi-indexerd, zkapi-challenged and an OpenRouter-compatible
    lease provider;
  * a wallet driver (src/wallet) around the upstream zkapi-browser wallet core
    with durable write-ahead journals;
  * verification of the live mainnet / Sepolia deployments against the SDK pins,
    the operator manifest, on-chain code hashes and the local proving keys;
  * the upstream acceptance suite (``acceptance``) as an independent check.

CLI:
    m zkapi/setup                     # clone pinned upstream, build services+wallet+contracts
    m zkapi/serve                     # API+app on :51420, boots the devnet
    m zkapi/devnet                    # devnet status
    m zkapi/create_wallet name=alice deposit_gwei=10000000
    m zkapi/chat name=alice prompt="hello"
    m zkapi/withdraw name=alice
    m zkapi/escape name=alice [history_index=0]   # stale index = fraud demo
    m zkapi/verify network=mainnet    # 29 cross-checks against the live deployment
    m zkapi/test                      # unit + full lifecycle on a scratch devnet
    m zkapi/acceptance                # upstream scripts/v2-acceptance.mjs
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR / "src"))

from zkstack import paths  # noqa: E402

STATE_DIR = paths.STATE_DIR
PID_FILE = STATE_DIR / "server.pid"


def _config() -> dict:
    return json.loads((MODULE_DIR / "config.json").read_text())


def _pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 3600, env: dict | None = None) -> dict:
    t = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})
    return {"cmd": " ".join(cmd[:6]) + (" ..." if len(cmd) > 6 else ""), "ok": proc.returncode == 0,
            "seconds": round(time.time() - t, 1), "tail": (proc.stdout + proc.stderr)[-1200:] if proc.returncode else ""}


class Mod:
    description = ("Ethereum zkAPI (vbuterin/zkapi): anonymous, proof-paid API credits from an ETH vault — "
                   "local devnet of the real stack + live mainnet/Sepolia verification")

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = _config()
        self.port = int(self.config.get("port", 51420))

    def forward(self, **kwargs):
        return self.info()

    # -- local API plumbing ------------------------------------------------------------------------------
    def _api(self, path: str, body: dict | None = None, timeout: float = 300) -> dict:
        url = f"http://127.0.0.1:{self.port}/api/{path.lstrip('/')}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"content-type": "application/json", "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return {"ok": False, "status": e.code, **json.loads(e.read())}
            except ValueError:
                return {"ok": False, "status": e.code}
        except (urllib.error.URLError, OSError) as e:
            return {"ok": False, "error": f"zkapi server not reachable on :{self.port} ({e}); run m zkapi/serve"}

    def _ensure_server(self) -> None:
        if _pid() is None:
            self.serve()
        for _ in range(240):
            if _pid() is None:
                tail = (STATE_DIR / "server.log").read_text(errors="replace")[-800:]
                raise RuntimeError(f"zkapi server exited during startup:\n{tail}")
            st = self._api("devnet", timeout=10)
            if st.get("running"):
                return
            info = self._api("info", timeout=10)
            if info.get("boot_error"):
                raise RuntimeError(info["boot_error"])
            time.sleep(0.5)

    # -- module surface -----------------------------------------------------------------------------------
    def info(self) -> dict:
        built = {name: paths.bin_path(name).exists() for name in
                 ("zkapi", "zkapi-indexerd", "zkapi-challenged", "zkapi-wallet")}
        return {
            "name": self.config["name"], "description": self.config["description"],
            "version": self.config["version"], "port": self.port, "running": _pid() is not None,
            "upstream": {"repo": paths.UPSTREAM_REPO, "commit": paths.UPSTREAM_COMMIT,
                         "present": (paths.UPSTREAM_DIR / ".git").exists(), "circuit_id": paths.CIRCUIT_ID},
            "built": built, "contracts_built": paths.artifact("ZkApiVault").exists(),
            "app": f"http://localhost:{self.port}/zkapi", "api": f"http://localhost:{self.port}/zkapi/api",
        }

    def status(self) -> dict:
        pid = _pid()
        out = {"running": pid is not None, "pid": pid, "port": self.port, "version": self.config["version"]}
        if pid:
            dev = self._api("devnet", timeout=15)
            out["devnet"] = {k: dev.get(k) for k in ("running", "chain", "price", "last_error")}
        return out

    def setup(self, rebuild: bool = False) -> dict:
        """Clone the pinned upstream, build its services + our wallet driver, compile all contracts."""
        steps = []
        for tool in ("git", "cargo", "forge", "anvil"):
            if not paths.which(tool):
                return {"ok": False, "error": f"{tool} is required (rust toolchain + foundry)"}
        up = paths.UPSTREAM_DIR
        if not (up / ".git").exists():
            paths.VENDOR_DIR.mkdir(parents=True, exist_ok=True)
            steps.append(_run(["git", "clone", "-q", paths.UPSTREAM_REPO, str(up)]))
        head = subprocess.run(["git", "-C", str(up), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if head != paths.UPSTREAM_COMMIT:
            steps.append(_run(["git", "-C", str(up), "fetch", "-q", "origin"]))
            steps.append(_run(["git", "-C", str(up), "checkout", "-q", paths.UPSTREAM_COMMIT]))
        target = str(paths.TARGET_DIR)
        if rebuild or not all(paths.bin_path(n).exists() for n in ("zkapi", "zkapi-indexerd", "zkapi-challenged")):
            # Same package set as upstream scripts/run-v2-acceptance.sh (feature unification matters).
            steps.append(_run(["cargo", "build", "--locked", "--release", "--target-dir", target,
                               "-p", "zkapi-cli", "--bin", "zkapi", "-p", "zkapi-serverd", "--bin", "zkapi-challenged",
                               "--example", "v2_acceptance_wallet", "-p", "zkapi-indexerd", "--bin", "zkapi-indexerd"],
                              cwd=up))
        if rebuild or not paths.bin_path("zkapi-wallet").exists():
            lock = paths.WALLET_CRATE / "Cargo.lock"
            if not lock.exists():
                shutil.copy(up / "Cargo.lock", lock)
            steps.append(_run(["cargo", "build", "--release", "--target-dir", target], cwd=paths.WALLET_CRATE))
        if rebuild or not paths.artifact("ZkApiVault").exists():
            steps.append(_run(["forge", "build"], cwd=paths.UPSTREAM_CONTRACTS))
        if rebuild or not paths.artifact("DevnetPriceFeed", local=True).exists():
            steps.append(_run(["forge", "build"], cwd=paths.LOCAL_CONTRACTS))
        ok = all(s["ok"] for s in steps)
        return {"ok": ok and all(self.info()["built"].values()), "steps": steps, **self.info()}

    def serve(self, port: int | None = None) -> dict:
        if (pid := _pid()) is not None:
            return {"running": True, "pid": pid, "port": self.port}
        if not paths.bin_path("zkapi-wallet").exists():
            res = self.setup()
            if not res["ok"]:
                return {"running": False, "error": "setup failed", "setup": res}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = open(STATE_DIR / "server.log", "ab")
        proc = subprocess.Popen([sys.executable, __file__, "--serve", str(port or self.port)], cwd=MODULE_DIR,
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        PID_FILE.write_text(str(proc.pid))
        return {"running": True, "pid": proc.pid, "port": port or self.port,
                "app": f"http://localhost:{port or self.port}/zkapi"}

    def kill(self) -> dict:
        pid = _pid()
        if pid is None:
            PID_FILE.unlink(missing_ok=True)
            return {"running": False}
        os.kill(pid, signal.SIGTERM)  # the server stops the devnet children on the way out
        gone = False
        for _ in range(450):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                gone = True
                break
        if not gone:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.3)
        # children that outlived a hard kill (anvil/serverd/indexer/challenger)
        from zkstack.devnet import Devnet
        Devnet()._kill_stale()
        PID_FILE.unlink(missing_ok=True)
        return {"running": False, "killed": pid, "graceful": gone}

    # -- devnet -----------------------------------------------------------------------------------------------
    def devnet(self) -> dict:
        """Status of the local zkAPI deployment (chain, vault, price, services)."""
        return self._api("devnet")

    def up(self) -> dict:
        self._ensure_server()
        return self._api("devnet/up", {})

    def down(self) -> dict:
        return self._api("devnet/down", {})

    def reset(self) -> dict:
        """Fresh chain + fresh vault. Old wallets/databases are archived under state/devnet/archive."""
        self._ensure_server()
        return self._api("devnet/reset", {})

    def manifest(self) -> dict:
        """SDK-shaped deployment manifest for the devnet (same schema as upstream mainnet.json)."""
        return self._api("devnet/manifest")

    def price(self, usd: float | None = None, push: bool = False) -> dict:
        if usd is not None or push:
            return self._api("devnet/price", {} if usd is None else {"usd": float(usd)})
        return self._api("devnet/price")

    def fast_forward(self, seconds: int = 86_401) -> dict:
        return self._api("devnet/fast_forward", {"seconds": int(seconds)})

    def accounts(self) -> list:
        return self._api("devnet/accounts")

    # -- wallets on the devnet ------------------------------------------------------------------------------
    def wallets(self) -> list:
        return self._api("wallets")

    def wallet(self, name: str) -> dict:
        return self._api(f"wallets/{name}")

    def create_wallet(self, name: str, deposit_gwei: int = 10_000_000, account: int = 3) -> dict:
        self._ensure_server()
        return self._api("wallets", {"name": name, "deposit_gwei": int(deposit_gwei), "account": int(account)})

    def deposit(self, name: str, amount_gwei: int = 10_000_000, account: int = 3) -> dict:
        return self._api(f"wallets/{name}/deposit", {"amount_gwei": int(amount_gwei), "account": int(account)})

    def chat(self, name: str = "default", prompt: str = "hello", model: str = "zkapi/echo",
             max_tokens: int | None = None) -> dict:
        self._ensure_server()
        body = {"model": model, "messages": [{"role": "user", "content": str(prompt)}]}
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if name == "default":
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/v1/chat/completions",
                                         data=json.dumps(body).encode(), headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                return json.loads(e.read() or b"{}")
        return self._api(f"wallets/{name}/chat", body)

    def recover(self, name: str) -> dict:
        return self._api(f"wallets/{name}/recover", {})

    def withdraw(self, name: str, destination: str | None = None, account: int = 3) -> dict:
        return self._api(f"wallets/{name}/withdraw", {"destination": destination, "account": int(account)})

    def escape(self, name: str, history_index: int | None = None, destination: str | None = None,
               account: int = 3) -> dict:
        return self._api(f"wallets/{name}/escape", {"history_index": history_index, "destination": destination,
                                                    "account": int(account)})

    def finalize(self, note_id: int, account: int = 3) -> dict:
        return self._api(f"notes/{int(note_id)}/finalize", {"account": int(account)})

    def note(self, note_id: int) -> dict:
        return self._api(f"notes/{int(note_id)}")

    # -- agents (agent contract: GET /agents + POST /run/stream) ---------------------------------------------------
    def agents(self) -> dict:
        return self._api("agents")

    def agent(self, id: str = "zeke", **params) -> dict:
        """Read an agent, or edit it when params are given (m zkapi/agent id=zeke prompt="...")."""
        return self._api(f"agents/{id}", params) if params else self._api(f"agents/{id}")

    def run(self, query: str = "what is zkapi?", agent: str = "zeke") -> dict:
        self._ensure_server()
        return self._api("run", {"query": query, "agent_type": agent})

    def publish(self) -> dict:
        """Mount orbit/zkapi in the build console's agent roster."""
        return self._api("agents/publish", {})

    # -- live networks ---------------------------------------------------------------------------------------
    def networks(self) -> dict:
        from zkstack import networks
        return {n: networks.pins(n) for n in networks.NETWORKS}

    def verify(self, network: str = "mainnet") -> dict:
        """Cross-check a live deployment: SDK pins, operator manifest, chain, server, indexer, local keys."""
        from zkstack import networks
        return networks.verify(network)

    def probe(self, network: str = "mainnet") -> dict:
        from zkstack import networks
        return networks.probe(network)

    def live(self, network: str = "sepolia", action: str = "status", name: str = "me", amount_gwei: int = 0,
             prompt: str = "", model: str = "openai/gpt-4o-mini", destination: str = "", confirm: bool = False) -> dict:
        """Use a LIVE deployment with this module's wallet (real ETH on mainnet).

        Reads (status/quote) are free. Writes need ZKAPI_ETH_PRIVATE_KEY in the env
        and confirm=True; Sepolia /v2 calls need ZKAPI_TESTNET_PASSWORD.
        actions: status | quote | create | deposit | chat | recover | withdraw | escape
        """
        from zkstack import networks
        from zkstack.chain import Sender
        from zkstack.client import ZkApiClient
        from zkstack.wallet import WalletDriver

        dep = networks.deployment(network)
        drv = WalletDriver(STATE_DIR / "live" / network / "wallets")
        vault_abi = json.loads(paths.artifact("ZkApiVault").read_text())["abi"]
        c = ZkApiClient(dep, drv, vault_abi)
        try:
            if action == "quote":
                return c.quote()
            if action == "status":
                return c.status(name)
            if action == "create":
                return c.create_wallet(name, labels={"network": network})
            if action == "recover":
                return c.recover(name)
            if action == "chat":
                if not confirm:
                    return {"ok": False, "error": "spends your deposited balance: pass confirm=True"}
                return c.chat(name, {"model": model, "messages": [{"role": "user", "content": prompt}]})
            key = os.environ.get("ZKAPI_ETH_PRIVATE_KEY")
            if not key:
                return {"ok": False, "error": "set ZKAPI_ETH_PRIVATE_KEY (funded account) for on-chain actions"}
            if not confirm:
                return {"ok": False, "error": f"{action} on {network} sends a real transaction: pass confirm=True"}
            sender = Sender(c.rpc, private_key=key)
            if action == "deposit":
                return c.deposit(name, int(amount_gwei), sender)
            if action == "withdraw":
                return c.withdraw(name, destination or sender.address, sender)
            if action == "escape":
                return c.escape(name, destination or sender.address, sender)
            return {"ok": False, "error": f"unknown action {action}"}
        finally:
            drv.close()

    # -- tests -----------------------------------------------------------------------------------------------------
    def test(self, e2e: bool = True, live: bool = False) -> dict:
        """Unit checks + (default) the full lifecycle on a scratch devnet; live=True also verifies mainnet."""
        cmd = [sys.executable, "-m", "pytest", "-q", str(MODULE_DIR / "src" / "tests")]
        env = {"ZKAPI_TEST_E2E": "1" if e2e else "0", "ZKAPI_TEST_LIVE": "1" if live else "0"}
        r = subprocess.run(cmd, cwd=MODULE_DIR, capture_output=True, text=True, timeout=1800,
                           env={**os.environ, **env})
        tail = (r.stdout + r.stderr).strip().splitlines()
        return {"ok": r.returncode == 0, "summary": tail[-1] if tail else "", "output": "\n".join(tail[-40:])}

    NODE24 = ("v24.22.0", "cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008")

    def _fetch_node24(self) -> dict:
        """Upstream's harness needs Node >= 24; fetch the pinned linux-x64 build (sha256-checked)."""
        import hashlib
        import tarfile
        version, digest = self.NODE24
        name = f"node-{version}-linux-x64"
        url = f"https://nodejs.org/dist/{version}/{name}.tar.xz"
        tmp = paths.VENDOR_DIR / f"{name}.tar.xz"
        try:
            paths.VENDOR_DIR.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=300) as r:
                tmp.write_bytes(r.read())
            if hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
                return {"ok": False, "error": f"checksum mismatch for {url}"}
            with tarfile.open(tmp) as tar:
                tar.extractall(paths.VENDOR_DIR, filter="tar")
            (paths.VENDOR_DIR / name).rename(paths.NODE24_DIR)
            return {"ok": True}
        except (OSError, urllib.error.URLError, tarfile.TarError) as e:
            return {"ok": False, "error": f"could not fetch Node 24 ({e}); unpack it into vendor/node24"}
        finally:
            tmp.unlink(missing_ok=True)

    def acceptance(self) -> dict:
        """Run the upstream v2 acceptance harness (real proofs, vault, server, indexer, challenger)."""
        node = paths.NODE24_DIR / "bin" / "node"
        if not node.exists():
            got = self._fetch_node24()
            if not got["ok"]:
                return got
        up = paths.UPSTREAM_DIR
        env = {"PATH": f"{paths.NODE24_DIR / 'bin'}:{os.environ.get('PATH', '')}:{Path.home()}/.foundry/bin"}
        if not (up / "docker/aws/signer/node_modules/ethers").exists():
            r = _run([str(paths.NODE24_DIR / "bin" / "npm"), "ci", "--prefix", "docker/aws/signer", "--ignore-scripts",
                      "--no-audit", "--no-fund"], cwd=up, env=env)
            if not r["ok"]:
                return {"ok": False, "step": "npm ci", **r}
        out_dir = STATE_DIR / "acceptance"
        r = subprocess.run([str(node), "scripts/v2-acceptance.mjs", "--bin-dir", str(paths.BIN_DIR),
                            "--output-dir", str(out_dir)], cwd=up, capture_output=True, text=True, timeout=1800,
                           env={**os.environ, **env})
        text = r.stdout + r.stderr
        evidence = next((ln.split("Evidence: ", 1)[1].strip() for ln in text.splitlines() if "Evidence: " in ln), None)
        result = None
        if evidence and (Path(evidence) / "result.json").exists():
            full = json.loads((Path(evidence) / "result.json").read_text())
            result = {k: full.get(k) for k in ("passed", "elapsed_wall_seconds", "source_commit", "withdrawal",
                                               "challenge", "coverage")}
        return {"ok": r.returncode == 0, "output": text[-1500:], "evidence": evidence, "result": result}


if __name__ == "__main__":
    if "--serve" in sys.argv:
        from zkstack.server import run

        i = sys.argv.index("--serve")
        port = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else _config().get("port", 51420)
        run(port)
    else:
        print(json.dumps(Mod().info(), indent=2))
