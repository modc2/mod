"""Client for the persistent ``zkapi-wallet`` driver (src/wallet, upstream wallet core).

One long-lived child process holds the loaded Groth16 proving keys; every
command names its wallet file, and every mutation is fsynced by the driver
before the reply arrives here.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from . import paths


class WalletError(RuntimeError):
    pass


class WalletDriver:
    def __init__(self, wallets_dir: Path, setup_dir: Path = paths.SETUP_DIR,
                 binary: Path | None = None):
        self.wallets_dir = Path(wallets_dir)
        self.setup_dir = Path(setup_dir)
        self.binary = Path(binary or paths.bin_path("zkapi-wallet"))
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._id = 0

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            if not self.binary.exists():
                raise WalletError(f"wallet driver not built: {self.binary} (run m zkapi/setup)")
            self.wallets_dir.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(
                [str(self.binary), str(self.setup_dir), str(self.wallets_dir)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, env={"PATH": "/usr/bin:/bin", "RUST_LOG": "warn,r1cs=warn"},
            )
        return self._proc

    def __call__(self, command: str, **fields: Any) -> Any:
        with self._lock:
            proc = self._ensure()
            self._id += 1
            line = json.dumps({"id": self._id, "command": command, **fields})
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
                reply = proc.stdout.readline()
            except (BrokenPipeError, OSError) as e:
                self._proc = None
                raise WalletError(f"wallet driver died: {e}") from None
            if not reply:
                self._proc = None
                raise WalletError("wallet driver exited")
            value = json.loads(reply)
            if not value.get("ok"):
                raise WalletError(value.get("error", "wallet error"))
            return value["result"]

    def close(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.stdin.close()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
