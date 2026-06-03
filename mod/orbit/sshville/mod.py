"""
sshville — manage multiple SSH remote connections, auth'd by either:

  • MetaMask / Ethereum personal_sign        (X-Mod-Auth: eth <sig>)
  • Polkadot.js or SubWallet sr25519         (X-Mod-Auth: sub <pubkey> <sig> [prefix])
    (the sr25519 path covers Bittensor — SS58 prefix 42 — and any other
    Substrate chain when you pass its prefix)

The Rust axum backend (src/api) does the signature verification, owns the
JSON-backed connection store at ~/.sshville/connections.json, and shells out
to OpenSSH for test/exec. This Python anchor only knows how to start/stop
the Rust binary and expose a thin CLI wrapper over the on-disk store.

Sensitive credentials (passwords or private keys) are encrypted client-side
under a wallet-derived AES-GCM key — the server only ever sees ciphertext for
storage, and a freshly-decrypted plaintext per test/exec call (used once,
never persisted).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import mod as m


class Mod:
    description = "Multi-host SSH manager — MetaMask (ETH) + SubWallet/Polkadot.js (sr25519) auth"
    path = r"/Users/broski/mod/mod/orbit/sshville"

    CHALLENGE = "sshville-auth-v1"

    def __init__(self):
        self._cfg_path = os.path.join(self.path, "config.json")
        self._store_dir = Path(
            os.environ.get("SSHVILLE_STORE_DIR", os.path.expanduser("~/.sshville"))
        )
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._store_path = self._store_dir / "connections.json"
        self._cfg = self._load_config()
        self._api_dir = Path(self.path) / "src" / "api"

    # ── config / store ────────────────────────────────────────────────

    def _load_config(self) -> dict:
        if not os.path.exists(self._cfg_path):
            return {}
        with open(self._cfg_path) as f:
            return json.load(f)

    def _load_store(self) -> dict:
        if not self._store_path.exists():
            return {}
        try:
            return json.loads(self._store_path.read_text() or "{}")
        except Exception:
            return {}

    def _save_store(self, data: dict) -> None:
        self._store_path.write_text(json.dumps(data, indent=2))

    @staticmethod
    def _wallet_id(wallet: str) -> str:
        """Normalize a wallet hint into the store's compound ID.

        Accepts the already-prefixed form (`eth:0x…`, `sub:5…`), a bare
        ethereum address (auto-prefixed `eth:`), or a bare ss58 (`sub:`).
        """
        w = wallet.strip()
        if w.startswith(("eth:", "sub:")):
            return w[:4] + w[4:].lower() if w.startswith("eth:") else w
        if w.startswith("0x") and len(w) == 42:
            return f"eth:{w.lower()}"
        # treat anything else as ss58
        return f"sub:{w}"

    # ── public ────────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        store = self._load_store()
        return {
            "name": self._cfg.get("name", "sshville"),
            "version": self._cfg.get("version"),
            "description": self.description,
            "path": self.path,
            "backend": "rust (axum)",
            "ports": {
                "api": self._cfg.get("port"),
                "app": self._cfg.get("app_port"),
            },
            "store_path": str(self._store_path),
            "wallets": len(store),
            "connections": sum(len(v) for v in store.values()),
            "challenge": self.CHALLENGE,
            "auth_schemes": ["eth (MetaMask)", "sub (Polkadot.js / SubWallet, sr25519)"],
        }

    def list(self, wallet: Optional[str] = None) -> dict:
        """List connections for one wallet, or every wallet if none is given."""
        store = self._load_store()
        if wallet is None:
            return {w: list(conns.keys()) for w, conns in store.items()}
        return store.get(self._wallet_id(wallet), {})

    def add(
        self,
        wallet: str,
        name: str,
        host: str,
        user: str,
        ciphertext: str,
        iv: str,
        port: int = 22,
        auth_type: str = "password",
        id: Optional[str] = None,
    ) -> dict:
        """Insert or upsert a connection record for a wallet.

        `ciphertext`/`iv` are produced client-side by encrypting the password
        or private-key text under a wallet-signature-derived key. Use the
        store-CID form `eth:0x…` or `sub:5…`, or pass the raw address and we'll
        guess the scheme.
        """
        wallet = self._wallet_id(wallet)
        if auth_type not in {"password", "key"}:
            return {"error": f"auth_type must be 'password' or 'key', got {auth_type!r}"}
        store = self._load_store()
        bucket = store.setdefault(wallet, {})
        cid = id or f"{user}@{host}:{port}"
        prior = bucket.get(cid, {})
        bucket[cid] = {
            "id": cid,
            "name": name,
            "host": host,
            "port": int(port),
            "user": user,
            "auth_type": auth_type,
            "ciphertext": ciphertext,
            "iv": iv,
            "created_at": prior.get("created_at", int(time.time())),
            "updated_at": int(time.time()),
        }
        self._save_store(store)
        return {"ok": True, "wallet": wallet, "id": cid}

    def remove(self, wallet: str, id: str) -> dict:
        wallet = self._wallet_id(wallet)
        store = self._load_store()
        bucket = store.get(wallet, {})
        if id not in bucket:
            return {"ok": False, "error": f"connection {id!r} not found"}
        del bucket[id]
        if not bucket:
            store.pop(wallet, None)
        self._save_store(store)
        return {"ok": True, "wallet": wallet, "id": id}

    # ── ssh helpers (local cli use only — the server uses its own path) ─

    def test(self, wallet: str, id: str, secret: str) -> dict:
        return self._ssh(wallet, id, secret, "whoami && uname -a")

    def exec(self, wallet: str, id: str, secret: str, command: str) -> dict:
        return self._ssh(wallet, id, secret, command)

    def _ssh(self, wallet: str, id: str, secret: str, command: str) -> dict:
        conn = self._load_store().get(self._wallet_id(wallet), {}).get(id)
        if not conn:
            return {"ok": False, "error": f"connection {id!r} not found"}
        opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=no",
            "-o", "ConnectTimeout=10",
            "-p", str(conn["port"]),
        ]
        target = f"{conn['user']}@{conn['host']}"
        if conn["auth_type"] == "password":
            if shutil.which("sshpass") is None:
                return {"ok": False, "error": "sshpass required for password auth"}
            cmd = ["sshpass", "-e", "ssh", *opts, target, command]
            env = {**os.environ, "SSHPASS": secret}
        else:
            keyfile = self._store_dir / f".tmp.{int(time.time()*1000)}.key"
            keyfile.write_text(secret if secret.endswith("\n") else secret + "\n")
            os.chmod(keyfile, 0o600)
            cmd = ["ssh", *opts, "-i", str(keyfile), target, command]
            env = os.environ.copy()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
            return {
                "ok": r.returncode == 0,
                "exit": r.returncode,
                "stdout": r.stdout,
                "stderr": r.stderr,
            }
        finally:
            if conn["auth_type"] == "key":
                try:
                    keyfile.unlink()
                except Exception:
                    pass

    # ── lifecycle ─────────────────────────────────────────────────────

    def build(self, release: bool = True) -> dict:
        """Compile the Rust API. Required at least once before `serve`."""
        args = ["cargo", "build"]
        if release:
            args.append("--release")
        r = subprocess.run(args, cwd=self._api_dir, capture_output=True, text=True)
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout[-2000:],
            "stderr": r.stderr[-4000:],
        }

    def _binary(self) -> Path:
        target = self._api_dir / "target" / "release" / "sshville-api"
        if not target.exists():
            target = self._api_dir / "target" / "debug" / "sshville-api"
        return target

    def serve(self) -> dict:
        port = int(self._cfg.get("port", 50180))
        app_port = int(self._cfg.get("app_port", 50181))
        log_dir = Path("/tmp/sshville")
        log_dir.mkdir(parents=True, exist_ok=True)

        binary = self._binary()
        if not binary.exists():
            built = self.build(release=True)
            if not built["ok"]:
                return {"error": "cargo build failed", **built}

        env = {
            **os.environ,
            "SSHVILLE_PORT": str(port),
            "SSHVILLE_STORE_DIR": str(self._store_dir),
            "SSHVILLE_CHALLENGE": self.CHALLENGE,
        }
        api_log = open(log_dir / "api.log", "ab")
        app_log = open(log_dir / "app.log", "ab")
        api_proc = subprocess.Popen(
            [str(binary)],
            cwd=str(self._api_dir),
            stdout=api_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        app_proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(app_port)],
            cwd=os.path.join(self.path, "src/app"),
            stdout=app_log,
            stderr=subprocess.STDOUT,
        )
        (log_dir / "api.pid").write_text(str(api_proc.pid))
        (log_dir / "app.pid").write_text(str(app_proc.pid))
        return {
            "api": f"http://localhost:{port}",
            "app": f"http://localhost:{app_port}",
            "api_pid": api_proc.pid,
            "app_pid": app_proc.pid,
            "binary": str(binary),
        }

    def kill(self) -> dict:
        killed = {}
        for name in ("api", "app"):
            pid_file = Path(f"/tmp/sshville/{name}.pid")
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                    killed[name] = pid
                except Exception as e:
                    killed[name] = f"err: {e}"
                pid_file.unlink(missing_ok=True)
        return killed
