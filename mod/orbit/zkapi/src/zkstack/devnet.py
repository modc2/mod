"""A persistent local zkAPI deployment built from the upstream services.

    anvil (chain 31337, state persisted)            :51421
      ├─ Bn254Poseidon + Groth16ProofAdapter + ZkApiVault   (upstream Solidity)
      └─ DevnetPriceFeed  (Chainlink AggregatorV3 shape, keeper-fed ETH/USD)
    zkapi-indexerd   vault events -> Merkle tree           :51422
    zkapi serverd    leases, settlement, Schnorr signing   :51423
    provider         OpenRouter Management API + inference :51424 (in-process)
    zkapi-challenged watches escapes, challenges stale ones (anvil account #1)

Anvil runs with one slot per epoch, so the ``finalized`` tag the server's oracle
reads is two blocks behind head; the keeper mines past it after each round.
Everything lives under ``state/devnet`` and survives restarts: anvil dumps its
state, the indexer keeps a cursor, the server keeps its SQLite archive, the
provider keeps its key ledger and wallets are files.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import paths
from .chain import GWEI, Contract, Rpc, RpcError, Sender, link_bytecode
from .client import Deployment, ZkApiClient, ZkApiError, http_json
from .provider import Provider
from .wallet import WalletDriver

CHAIN_ID = 31337
MNEMONIC = "test test test test test test test test test test test junk"
DEFAULT_PORTS = {"anvil": 51421, "indexer": 51422, "server": 51423, "provider": 51424}
REQUEST_CHARGE_CAP = 50_000          # gwei per request, same as mainnet
NOTE_TTL = 30 * 86_400               # seconds
CHALLENGE_PERIOD = 86_400            # seconds, vault default
PRICE_DECIMALS = 8
PRICE_MAX_AGE = 3_600                # server --native-price-max-age-seconds
FALLBACK_PRICE = 2_500 * 10**PRICE_DECIMALS
CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
MAINNET_RPC = os.environ.get("ZKAPI_MAINNET_RPC", "https://ethereum-rpc.publicnode.com")
# anvil accounts: 0 deployer/owner/keeper, 1 challenger, 2 treasury, 3.. users
ROLE_DEPLOYER, ROLE_CHALLENGER, ROLE_TREASURY, FIRST_USER = 0, 1, 2, 3


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Devnet:
    def __init__(self, root: Path | None = None, ports: dict | None = None, quiet: bool = True):
        self.root = Path(root or paths.STATE_DIR / "devnet")
        self.ports = {**DEFAULT_PORTS, **(ports or {})}
        self.logs = self.root / "logs"
        self.procs: dict[str, subprocess.Popen] = {}
        self.provider: Provider | None = None
        self._keeper: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.wallets = WalletDriver(self.root / "wallets")
        self.vault_abi = json.loads(paths.artifact("ZkApiVault").read_text())["abi"]
        self.feed_abi = json.loads(paths.artifact("DevnetPriceFeed", local=True).read_text())["abi"]
        self.price_source = None
        self.started_at = None
        self.last_error = None

    # -- paths & persisted metadata --------------------------------------------------------------
    @property
    def rpc_url(self) -> str:
        return f"http://127.0.0.1:{self.ports['anvil']}"

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self.ports['server']}"

    @property
    def indexer_url(self) -> str:
        return f"http://127.0.0.1:{self.ports['indexer']}"

    @property
    def provider_url(self) -> str:
        return f"http://127.0.0.1:{self.ports['provider']}"

    def _json(self, name: str) -> dict:
        try:
            return json.loads((self.root / name).read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, name: str, value: dict, private: bool = False) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2))
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    @property
    def deployment(self) -> dict:
        return self._json("deployment.json")

    def secrets(self) -> dict:
        data = self._json("secrets.json")
        if not data:
            data = {"state_seed": "0x" + secrets.token_hex(31), "clear_seed": "0x" + secrets.token_hex(31)}
            self._write("secrets.json", data, private=True)
        return data

    @property
    def rpc(self) -> Rpc:
        return Rpc(self.rpc_url)

    def account(self, index: int) -> str:
        return self.rpc.accounts()[index]

    def sender(self, index: int = FIRST_USER) -> Sender:
        return Sender(self.rpc, self.account(index))

    # -- process management ---------------------------------------------------------------------------
    def _launch(self, name: str, args: list[str]) -> subprocess.Popen:
        self.logs.mkdir(parents=True, exist_ok=True)
        log = open(self.logs / f"{name}.log", "ab")
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}\n".encode())
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin") + f":{Path.home()}/.foundry/bin",
               "HOME": str(self.root), "RUST_LOG": "warn,zkapi=info,zkapi_serverd=info,zkapi_indexerd=info,r1cs=warn"}
        proc = subprocess.Popen(args, cwd=self.root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        self.procs[name] = proc
        self._save_pids()
        return proc

    def _save_pids(self) -> None:
        self._write("pids.json", {n: p.pid for n, p in self.procs.items() if p.poll() is None})

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _stop_pid(self, pid: int, timeout: float = 8.0) -> None:
        try:
            os.kill(pid, signal.SIGINT)  # anvil dumps --state on SIGINT
        except ProcessLookupError:
            return
        end = time.time() + timeout
        while time.time() < end and self._alive(pid):
            time.sleep(0.1)
        if self._alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _kill_stale(self) -> None:
        """Stop children recorded by a previous (crashed) owner of this devnet."""
        for name, pid in self._json("pids.json").items():
            if pid and self._alive(pid):
                try:
                    cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
                except OSError:
                    cmd = b""
                if any(k in cmd for k in (b"anvil", b"zkapi")):
                    self._stop_pid(pid, timeout=8.0 if name == "anvil" else 2.0)

    def _wait(self, label: str, fn, timeout: float = 60.0) -> Any:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                value = fn()
                if value:
                    return value
            except Exception as e:  # noqa: BLE001 - polling
                last = e
            for name, proc in self.procs.items():
                if proc.poll() is not None:
                    tail = (self.logs / f"{name}.log").read_text(errors="replace")[-1500:]
                    raise RuntimeError(f"{name} exited while waiting for {label}:\n{tail}")
            time.sleep(0.15)
        raise TimeoutError(f"timeout waiting for {label}" + (f" ({last})" if last else ""))

    # -- lifecycle ---------------------------------------------------------------------------------------
    def up(self) -> dict:
        with self._lock:
            missing = [str(p) for p in (paths.bin_path("zkapi"), paths.bin_path("zkapi-indexerd"),
                                        paths.bin_path("zkapi-challenged"), paths.bin_path("zkapi-wallet"),
                                        paths.artifact("ZkApiVault"), paths.artifact("DevnetPriceFeed", local=True))
                       if not Path(p).exists()]
            if missing:
                raise RuntimeError(f"zkAPI toolchain not built ({missing[0]}); run: m zkapi/setup")
            if self.running():
                return self.status()
            self._kill_stale()
            self.procs.clear()
            self.root.mkdir(parents=True, exist_ok=True)
            self._stop.clear()
            self._start_anvil()
            dep = self.deployment
            if not dep or not self._deployment_live(dep):
                if dep:
                    self._archive_chain_state()
                dep = self._deploy()
            self._ensure_price(force=False)
            self._start_indexer(dep)
            self._start_provider()
            self._start_server(dep)
            self._start_challenger(dep)
            self.started_at = time.time()
            self._keeper = threading.Thread(target=self._keep, daemon=True, name="zkapi-keeper")
            self._keeper.start()
            return self.status()

    def down(self) -> dict:
        with self._lock:
            self._stop.set()
            self.started_at = None
            order = ["challenger", "server", "indexer", "anvil"]
            for name in order:
                proc = self.procs.get(name)
                if proc and proc.poll() is None:
                    # The Rust daemons don't trap SIGINT promptly; like the upstream
                    # acceptance runner, give them 2s then SIGKILL (SQLite/cursors are
                    # crash-safe). anvil gets longer: it dumps --state on SIGINT.
                    self._stop_pid(proc.pid, timeout=8.0 if name == "anvil" else 2.0)
                    proc.poll()
            self.procs.clear()
            self._kill_stale()
            self._write("pids.json", {})
            if self.provider:
                self.provider.stop()
                self.provider = None
            self.wallets.close()
            return {"running": False}

    def reset(self) -> dict:
        """Throw away the chain and every service database (wallets are archived, not deleted)."""
        self.down()
        self._archive_chain_state()
        for name in ("deployment.json", "anvil-state.json"):
            (self.root / name).unlink(missing_ok=True)
        return {"reset": True}

    def _archive_chain_state(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archive = self.root / "archive" / stamp
        moved = []
        for name in ("server.db", "server.db-wal", "server.db-shm", "indexer.json", "challenger.json",
                     "provider.json", "deployment.json", "wallets"):
            src = self.root / name
            if src.exists():
                archive.mkdir(parents=True, exist_ok=True)
                src.replace(archive / name)
                moved.append(name)
        if moved:
            self.last_error = f"previous chain state archived to {archive}"

    def running(self) -> bool:
        """Fully up: every service started and the deployment verified (not merely spawning)."""
        return self.started_at is not None and bool(self.procs) and all(p.poll() is None for p in self.procs.values())

    # -- components ------------------------------------------------------------------------------------------
    def _start_anvil(self) -> None:
        state = self.root / "anvil-state.json"
        args = [paths.which("anvil") or "anvil", "--host", "127.0.0.1", "--port", str(self.ports["anvil"]),
                "--chain-id", str(CHAIN_ID), "--mnemonic", MNEMONIC, "--accounts", "10", "--balance", "10000",
                "--slots-in-an-epoch", "1", "--state", str(state), "--state-interval", "15", "--silent"]
        self._launch("anvil", args)
        self._wait("anvil", lambda: self.rpc.chain_id() == CHAIN_ID, timeout=30)

    def _deployment_live(self, dep: dict) -> bool:
        try:
            return self.rpc.code(dep["vault"]) not in ("0x", "0x0", "") and \
                self.rpc.code(dep["price_feed"]) not in ("0x", "0x0", "")
        except (RpcError, KeyError):
            return False

    def _signing_keys(self) -> dict:
        sec = self.secrets()
        out = subprocess.run([str(paths.bin_path("zkapi")), "signing-keys", "--state-seed", sec["state_seed"],
                              "--clear-seed", sec["clear_seed"]], capture_output=True, text=True, timeout=60,
                             env={"PATH": "/usr/bin:/bin"})
        if out.returncode != 0:
            raise RuntimeError(f"zkapi signing-keys failed: {out.stderr[-500:]}")
        return json.loads(out.stdout)

    def _deploy(self) -> dict:
        rpc = self.rpc
        accounts = rpc.accounts()
        deployer = Sender(rpc, accounts[ROLE_DEPLOYER])
        art = lambda n, local=False: json.loads(paths.artifact(n, local).read_text())  # noqa: E731
        keys = self._signing_keys()

        def create(artifact: dict, code: str, *ctor: Any) -> tuple[str, dict]:
            c = Contract(artifact["abi"])
            receipt = deployer.send(None, c.encode_constructor(code, *ctor), gas=12_000_000)
            return receipt["contractAddress"], receipt

        poseidon_art, adapter_art, vault_art = art("Bn254Poseidon"), art("Groth16ProofAdapter"), art("ZkApiVault")
        poseidon, _ = create(poseidon_art, link_bytecode(poseidon_art, {}))
        adapter, _ = create(adapter_art, link_bytecode(adapter_art, {}))
        vault_code = link_bytecode(vault_art, {"src/libraries/Bn254Poseidon.sol:Bn254Poseidon": poseidon})
        sk, ck = keys["state_signing_key"], keys["clearance_signing_key"]
        vault, receipt = create(vault_art, vault_code, accounts[ROLE_TREASURY], NOTE_TTL, CHALLENGE_PERIOD,
                                REQUEST_CHARGE_CAP, adapter, sk["x"], sk["y"], ck["x"], ck["y"], accounts[ROLE_DEPLOYER])
        price, source = self._observe_price()
        feed_art = art("DevnetPriceFeed", local=True)
        feed, _ = create(feed_art, feed_art["bytecode"]["object"], PRICE_DECIMALS, "ETH / USD (zkapi devnet)",
                         price, int(time.time()) - 1)
        self.price_source = source
        rpc.call("anvil_mine", ["0x3"])
        v = Contract(vault_art["abi"], vault)
        dep = {
            "name": "devnet", "chain_id": CHAIN_ID, "rpc_url": self.rpc_url, "vault": vault, "adapter": adapter,
            "poseidon": poseidon, "price_feed": feed, "price_feed_decimals": PRICE_DECIMALS,
            "price_max_age_seconds": PRICE_MAX_AGE, "treasury": accounts[ROLE_TREASURY],
            "owner": accounts[ROLE_DEPLOYER], "challenger": accounts[ROLE_CHALLENGER],
            "deploy_block": int(receipt["blockNumber"], 16), "deployed_at": int(time.time()),
            "request_charge_cap": int(v.call(rpc, "requestChargeCap")), "note_ttl": int(v.call(rpc, "noteTtl")),
            "challenge_period": int(v.call(rpc, "challengePeriod")), "state_signing_key": sk,
            "clearance_signing_key": ck, "circuit_id": paths.CIRCUIT_ID, "upstream_commit": paths.UPSTREAM_COMMIT,
            "proof_artifacts_sha256": {f: sha256(paths.SETUP_DIR / f) for f in
                                       ("request.pk", "request.vk", "withdrawal.pk", "withdrawal.vk")},
        }
        self._write("deployment.json", dep)
        return dep

    # -- price keeper -----------------------------------------------------------------------------------------
    def _observe_price(self) -> tuple[int, str]:
        """Live Chainlink mainnet ETH/USD answer, or a fixed fallback offline."""
        if os.environ.get("ZKAPI_DEVNET_PRICE"):
            return int(float(os.environ["ZKAPI_DEVNET_PRICE"]) * 10**PRICE_DECIMALS), "env ZKAPI_DEVNET_PRICE"
        try:
            raw = Rpc(MAINNET_RPC, timeout=6).eth_call(CHAINLINK_ETH_USD, "0xfeaf968c")
            answer = int(raw[2 + 64: 2 + 128], 16)
            if 10**PRICE_DECIMALS < answer < 10**6 * 10**PRICE_DECIMALS:
                return answer, f"chainlink mainnet {CHAINLINK_ETH_USD}"
        except Exception:  # noqa: BLE001 - offline is fine
            pass
        return FALLBACK_PRICE, "fallback $2,500 (mainnet feed unreachable)"

    def feed(self) -> Contract:
        return Contract(self.feed_abi, self.deployment["price_feed"])

    def price(self) -> dict:
        feed = self.feed()
        rid, answer, started, updated, _ = feed.call(self.rpc, "latestRoundData")
        frid, fanswer, _, fupdated, _ = feed.call(self.rpc, "latestRoundData", tag="finalized")
        return {"round_id": rid, "answer": answer, "usd": answer / 10**PRICE_DECIMALS, "updated_at": updated,
                "age_seconds": int(time.time()) - updated, "finalized_round_id": frid,
                "finalized_usd": fanswer / 10**PRICE_DECIMALS, "max_age_seconds": PRICE_MAX_AGE,
                "source": self.price_source}

    def push_price(self, usd: float | None = None) -> dict:
        if usd is not None:
            answer, source = int(float(usd) * 10**PRICE_DECIMALS), "manual"
        else:
            answer, source = self._observe_price()
        rpc = self.rpc
        keeper = Sender(rpc, rpc.accounts()[ROLE_DEPLOYER])
        keeper.send(self.deployment["price_feed"], self.feed().encode("pushAnswer", answer, int(time.time()) - 1))
        rpc.call("anvil_mine", ["0x3"])  # finalized = head - 2 with one slot per epoch
        self.price_source = source
        return self.price()

    def _ensure_price(self, force: bool = False) -> None:
        try:
            p = self.price()
            # Refresh at 1/3 of the server's freshness window, and if the
            # finalized view lags the head round (never left un-finalized).
            if force or p["age_seconds"] > PRICE_MAX_AGE // 3 or p["finalized_round_id"] != p["round_id"]:
                self.push_price()
        except (RpcError, KeyError):
            self.push_price()

    def _keep(self) -> None:
        while not self._stop.wait(20):
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._ensure_price()
                    dep = self.deployment
                    for name, start in (("indexer", self._start_indexer), ("server", self._start_server),
                                        ("challenger", self._start_challenger)):
                        proc = self.procs.get(name)
                        if proc is not None and proc.poll() is not None:
                            self.last_error = f"{name} exited ({proc.returncode}); restarted"
                            start(dep)
                    anvil = self.procs.get("anvil")
                    if anvil is not None and anvil.poll() is not None:
                        self.last_error = "anvil exited; restarting devnet"
                        self._start_anvil()
            except Exception as e:  # noqa: BLE001 - keep the keeper alive
                self.last_error = f"keeper: {e}"

    def _start_indexer(self, dep: dict) -> None:
        self._launch("indexer", [str(paths.bin_path("zkapi-indexerd")), "--listen", f"127.0.0.1:{self.ports['indexer']}",
                                 "--rpc-url", self.rpc_url, "--contract-address", dep["vault"],
                                 "--from-block", str(dep["deploy_block"]), "--poll-interval-ms", "200",
                                 "--cursor-path", str(self.root / "indexer.json")])
        vault = Contract(self.vault_abi, dep["vault"])

        def synced() -> bool:
            status, snap = http_json(f"{self.indexer_url}/v1/tree/snapshot", timeout=5)
            return status == 200 and int(snap["root"], 16) == int(vault.call(self.rpc, "currentRoot"))
        self._wait("indexer sync", synced, timeout=60)

    def _start_provider(self) -> None:
        if self.provider is None:
            provider = Provider(self.root / "provider.json")
            # bind first: a failed bind (port briefly held) must not leave a provider with no listener
            provider.serve("127.0.0.1", self.ports["provider"])
            self.provider = provider

    def _start_server(self, dep: dict) -> None:
        sec = self.secrets()
        self._launch("server", [
            str(paths.bin_path("zkapi")), "--chain-id", str(CHAIN_ID), "--contract-address", dep["vault"],
            "--request-charge-cap", str(dep["request_charge_cap"]), "--proof-setup-dir", str(paths.SETUP_DIR),
            "serverd", "--listen", f"127.0.0.1:{self.ports['server']}",
            "--openrouter-api-base", self.provider_url, "--openrouter-management-key", self.provider.management_key,
            "--openrouter-lease-ttl-seconds", "300", "--openrouter-settlement-grace-seconds", "1",
            "--openrouter-settlement-poll-seconds", "1", "--native-billing-rpc-url", self.rpc_url,
            "--native-price-feed-address", dep["price_feed"], "--native-price-feed-decimals", str(PRICE_DECIMALS),
            "--native-price-max-age-seconds", str(PRICE_MAX_AGE), "--db-path", str(self.root / "server.db"),
            "--state-seed", sec["state_seed"], "--clear-seed", sec["clear_seed"], "--indexer-url", self.indexer_url,
            "--root-poll-interval-ms", "250"])
        vault = Contract(self.vault_abi, dep["vault"])

        def ready() -> bool:
            status, health = http_json(f"{self.server_url}/health", timeout=5)
            return status == 200 and int(health["current_root"], 16) == int(vault.call(self.rpc, "currentRoot"))
        self._wait("server at chain root", ready, timeout=90)
        status, att = http_json(f"{self.server_url}/v1/attestation", timeout=5)
        if att.get("state_signing_key") != dep["state_signing_key"]:
            raise RuntimeError("server attestation does not match the vault's pinned signing key")

    def _start_challenger(self, dep: dict) -> None:
        self._launch("challenger", [
            str(paths.bin_path("zkapi-challenged")), "--rpc-url", self.rpc_url, "--indexer-url", self.indexer_url,
            "--sender", dep["challenger"], "--chain-id", str(CHAIN_ID), "--contract-address", dep["vault"],
            "--from-block", str(dep["deploy_block"]), "--confirmations", "1",
            "--db-path", str(self.root / "server.db"), "--checkpoint", str(self.root / "challenger.json"),
            "--proof-setup-dir", str(paths.SETUP_DIR), "--poll-interval-ms", "500"])

    # -- client + views --------------------------------------------------------------------------------------
    def deployment_obj(self) -> Deployment:
        dep = self.deployment
        if not dep:
            raise ZkApiError("devnet is not deployed; run m zkapi/up", code="not_deployed")
        return Deployment(name="devnet", chain_id=CHAIN_ID, vault=dep["vault"], rpc_url=self.rpc_url,
                          server_url=self.server_url, indexer_url=self.indexer_url,
                          request_charge_cap=dep["request_charge_cap"], state_signing_key=dep["state_signing_key"],
                          clearance_signing_key=dep["clearance_signing_key"])

    def client(self) -> ZkApiClient:
        return ZkApiClient(self.deployment_obj(), self.wallets, self.vault_abi)

    def mine(self, blocks: int = 1) -> int:
        self.rpc.call("anvil_mine", [hex(max(1, int(blocks)))])
        return self.rpc.block_number()

    def fast_forward(self, seconds: int) -> dict:
        """Advance only the devnet clock (e.g. past an escape's challenge window)."""
        rpc = self.rpc
        rpc.call("evm_increaseTime", [int(seconds)])
        rpc.call("anvil_mine", ["0x1"])
        return {"chain_time": int(rpc.block("latest")["timestamp"], 16), "wall_time": int(time.time())}

    def manifest(self) -> dict:
        """SDK-style deployment manifest (same shape as upstream sdk/assets/config/*.json)."""
        dep = self.deployment
        return {
            "trusted_deployment": {
                "deployment_id": f"zkapi-native-eth-devnet-{dep.get('vault', '')[2:10].lower()}",
                "chain_id": CHAIN_ID, "contract_address": dep.get("vault"), "billing_token_address": None,
                "protocol_server_url": self.server_url, "indexer_url": self.indexer_url,
                "request_charge_cap": dep.get("request_charge_cap"),
                "state_signing_key": dep.get("state_signing_key"),
                "clearance_signing_key": dep.get("clearance_signing_key"), "rpc_url": self.rpc_url,
                "billing_asset": "native_eth", "billing_unit": "gwei", "native_asset_wei_per_unit": str(GWEI),
                "native_price_feed_address": dep.get("price_feed"), "native_price_feed_decimals": PRICE_DECIMALS,
                "native_price_max_age_seconds": PRICE_MAX_AGE,
                "request_proving_key_sha256": (dep.get("proof_artifacts_sha256") or {}).get("request.pk"),
                "withdrawal_proving_key_sha256": (dep.get("proof_artifacts_sha256") or {}).get("withdrawal.pk"),
                "circuit_id": paths.CIRCUIT_ID, "openrouter_inference_base": f"{self.provider_url}/v1",
            },
            "billing_token_symbol": "ETH", "billing_token_decimals": 9, "require_oa_key_source": False,
        }

    def status(self) -> dict:
        dep = self.deployment
        out: dict[str, Any] = {
            "running": self.running(), "starting": self.started_at is None and bool(self.procs),
            "root": str(self.root), "ports": self.ports,
            "processes": {n: {"pid": p.pid, "alive": p.poll() is None, "exit": p.returncode}
                          for n, p in self.procs.items()},
            "provider": self.provider.summary() if self.provider else None,
            "deployment": dep or None, "started_at": self.started_at, "last_error": self.last_error,
        }
        if self.running() and dep:
            try:
                rpc = self.rpc
                vault = Contract(self.vault_abi, dep["vault"])
                out["chain"] = {"block": rpc.block_number(),
                                "chain_time": int(rpc.block("latest")["timestamp"], 16),
                                "current_root": hex(int(vault.call(rpc, "currentRoot"))),
                                "next_note_id": int(vault.call(rpc, "nextNoteId")),
                                "vault_balance_eth": rpc.balance(dep["vault"]) / 10**18,
                                "treasury_balance_eth": rpc.balance(dep["treasury"]) / 10**18,
                                "paused": bool(vault.call(rpc, "paused"))}
                out["price"] = self.price()
                _, out["server"] = http_json(f"{self.server_url}/health", timeout=5)
                _, out["indexer"] = http_json(f"{self.indexer_url}/v1/tree/root", timeout=5)
                _, out["dashboard"] = http_json(f"{self.server_url}/v1/dashboard/summary", timeout=5)
            except Exception as e:  # noqa: BLE001 - status must not throw
                out["error"] = str(e)
        return out
