"""
starktime — StakeTime for Starknet.

Lock STRK for a chosen time. Each position gets
    weight = amount x multiplier(lock)        (1x flexible -> 6x at 4 years)
and the reward stream is split every second pro-rata by weight. When a lock
ends the position lapses back to 1x, so a long lock cannot keep a boosted
share forever.

Stack (one process, one port :51280, route /starktime):
    api/        Rust axum — protocol engine (paper ledger) + live Starknet reads,
                also serves the built app from dist/
    app/        Next.js static export (basePath /starktime)
    contracts/  Cairo 2 StarkTime contract — same math, scarb build

Until contracts/ is deployed and config.json `contract.address` is set, staking
runs on the PAPER ledger (paper STRK, 10k per new address). Network/balance
reads are real Starknet mainnet.

CLI (talks to the running API so CLI and app share one ledger):
    m starktime/stats
    m starktime/quote amount=1000 lock_days=365
    m starktime/stake owner=0xabc amount=500 lock_days=90
    m starktime/account owner=0xabc
    m starktime/claim owner=0xabc
    m starktime/unstake owner=0xabc id=0
    m starktime/network                     # live Starknet mainnet
    m starktime/test                        # cargo test + scarb build + /test
    m starktime/build | serve | kill | status
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
PM2_NAME = "starktime"
BINARY = MODULE_DIR / "api" / "target" / "release" / "starktime-api"
DAY = 86400


class Mod:
    description = (
        "StakeTime for Starknet: lock STRK for time, weight = amount x lock "
        "multiplier, rewards stream pro-rata by weight (Rust API + Cairo contract)"
    )

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / "config.json").read_text())
        self.port = int(self.config.get("port", 51280))

    # ── http to the running service ──────────────────────────────────

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}/_api{path}"

    def _req(self, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self._url(path), data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"error": f"starktime not running on :{self.port} ({e.reason}) — m starktime/serve"}

    @staticmethod
    def _lock(lock_seconds=None, lock_days=None) -> int:
        if lock_seconds is not None:
            return int(lock_seconds)
        return int(float(lock_days or 0) * DAY)

    # ── protocol ─────────────────────────────────────────────────────

    def info(self) -> dict:
        return self._req("/info")

    def stats(self) -> dict:
        return self._req("/stats")

    def params(self) -> dict:
        return self._req("/params")

    def quote(self, amount: float = 1000, lock_days: float = 0, lock_seconds: Optional[int] = None) -> dict:
        return self._req(f"/quote?amount={float(amount)}&lock_seconds={self._lock(lock_seconds, lock_days)}")

    def account(self, owner: str) -> dict:
        return self._req(f"/account?owner={owner}")

    def leaderboard(self) -> dict:
        return self._req("/leaderboard")

    def stake(self, owner: str, amount: float, lock_days: float = 0,
              lock_seconds: Optional[int] = None, validator: Optional[str] = None) -> dict:
        return self._req("/stake", {
            "owner": owner, "amount": float(amount),
            "lock_seconds": self._lock(lock_seconds, lock_days), "validator": validator,
        })

    def unstake(self, owner: str, id: int) -> dict:
        return self._req("/unstake", {"owner": owner, "id": int(id)})

    def extend(self, owner: str, id: int, lock_days: float = 0, lock_seconds: Optional[int] = None) -> dict:
        return self._req("/extend", {"owner": owner, "id": int(id), "lock_seconds": self._lock(lock_seconds, lock_days)})

    def claim(self, owner: str) -> dict:
        return self._req("/claim", {"owner": owner})

    # ── live starknet ────────────────────────────────────────────────

    def network(self) -> dict:
        return self._req("/network")

    def balance(self, address: str, network: str = "mainnet") -> dict:
        return self._req(f"/balance?address={address}&network={network}")

    def contract(self) -> dict:
        r = self._req("/contract")
        r.pop("source", None)  # long; read contracts/src/lib.cairo
        return r

    # ── build / service ──────────────────────────────────────────────

    def build(self) -> dict:
        """Build app (atomic dist swap), Rust API and Cairo contract."""
        r = subprocess.run(["bash", str(MODULE_DIR / "build.sh")], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "tail": (r.stdout + r.stderr).splitlines()[-10:]}

    def test(self) -> dict:
        """cargo test (engine + rpc), snforge/scarb on the contract, live /test."""
        out = {}
        r = subprocess.run(["cargo", "test", "--release", "-q"], cwd=MODULE_DIR / "api", capture_output=True, text=True)
        out["rust"] = {"ok": r.returncode == 0, "tail": (r.stdout + r.stderr).strip().splitlines()[-3:]}
        snforge = shutil.which("snforge") or str(Path.home() / ".local/bin/snforge")
        scarb = shutil.which("scarb") or str(Path.home() / ".local/bin/scarb")
        env = dict(os.environ, PATH=f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}")
        if Path(snforge).exists():
            r = subprocess.run([snforge, "test"], cwd=MODULE_DIR / "contracts", capture_output=True, text=True, env=env)
            out["cairo"] = {"ok": r.returncode == 0, "tail": (r.stdout + r.stderr).strip().splitlines()[-3:]}
        elif Path(scarb).exists():
            r = subprocess.run([scarb, "build"], cwd=MODULE_DIR / "contracts", capture_output=True, text=True, env=env)
            out["cairo"] = {"ok": r.returncode == 0, "tail": (r.stdout + r.stderr).strip().splitlines()[-3:]}
        out["live"] = self._req("/test")
        out["passed"] = all(v.get("ok", v.get("passed", False)) for v in out.values() if isinstance(v, dict))
        return out

    def serve(self, port: Optional[int] = None) -> dict:
        """Run the Rust binary under pm2 (survives reboots); bare process if no pm2."""
        port = int(port or self.port)
        if not BINARY.exists():
            b = self.build()
            if not BINARY.exists():
                return {"serving": False, "error": "build failed", "build": b}
        env = dict(os.environ, STARKTIME_PORT=str(port), STARKTIME_DIR=str(MODULE_DIR))
        if shutil.which("pm2"):
            subprocess.run(["pm2", "delete", PM2_NAME], capture_output=True)
            r = subprocess.run(
                ["pm2", "start", str(BINARY), "--name", PM2_NAME, "--cwd", str(MODULE_DIR)],
                env=env, capture_output=True, text=True,
            )
            subprocess.run(["pm2", "save"], capture_output=True)
            ok = r.returncode == 0
        else:
            subprocess.Popen([str(BINARY)], cwd=str(MODULE_DIR), env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
            ok = True
        return {"serving": ok, "port": port, "url": f"http://localhost:{port}/starktime/"}

    def kill(self) -> dict:
        if shutil.which("pm2"):
            subprocess.run(["pm2", "delete", PM2_NAME], capture_output=True)
            subprocess.run(["pm2", "save"], capture_output=True)
        # exact binary path only — never a bare pattern that could match other mods
        subprocess.run(["pkill", "-f", str(BINARY)], check=False)
        return {"killed": True}

    def status(self) -> dict:
        h = self._req("/health")
        return {
            "running": h.get("ok") is True,
            "port": self.port,
            "built": {"api": BINARY.exists(), "app": (MODULE_DIR / "dist").exists(),
                      "contract": (MODULE_DIR / "contracts/target/dev").exists()},
            "contract": self.config.get("contract"),
        }


if __name__ == '__main__':
    m = Mod()
    fn = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if fn == 'serve':
        out = m.serve(port=sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        method = getattr(m, fn, None)
        if not callable(method):
            out = {"error": f"unknown fn: {fn}"}
        else:
            out = method(*sys.argv[2:])
    print(json.dumps(out, indent=2, default=str))
