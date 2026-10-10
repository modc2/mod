"""
The proving backend: a real Sapling/Orchard spend, with no full node.

Everything else in this module is pure Python, and pure Python is where
shielded *spending* stops: a spend carries a Groth16 proof (Sapling) or a
Halo 2 proof (Orchard), and neither is something you compute in Python in a
web request. That is why `shielded_send` used to answer with an apology and
an exported spending key.

This is the part that closes it. `zcash-devtool` -- the ECC's reference light
client over `zcash_client_backend` / `zcash_client_sqlite` / `zcash_proofs` --
is built once into `~/.mod/zcash/bin/` and driven from here. It syncs compact
blocks from a lightwalletd server, keeps the note commitment trees, builds the
proof and broadcasts the transaction. No `zcashd`, no 100 GB of chain: the
same trust model every mobile Zcash wallet already runs on.

**One seed, two wallets.** The light client wallet is restored from the same
BIP39 mnemonic the rest of this module holds, so its Sapling and Orchard
accounts are literally the same account, at the same ZIP-32 path. Addresses
derived here and addresses derived in `shielded.py` are the same addresses;
a note received by one is spendable by the other.

**Where the secret lives.** `zcash-devtool` seals the mnemonic to an `age`
identity, which it keeps in the clear beside the wallet -- fine for a laptop,
not fine here. So the identity file never touches the disk in the clear: it
is re-sealed with this module's own AES-256-GCM under the wallet password
(`identity.enc`) and materialised into a 0600 temporary file only for the
seconds a send needs it. Sync, balance and address listing do not need it at
all, which is why only spending asks for a password.

**Syncing takes minutes, not milliseconds.** A wallet born today has a few
blocks to scan; a wallet restored from an old seed has millions. So a sync is
a background job -- `sync_start` spawns it, `status` reports on it -- and
never something an HTTP request waits on.
"""

import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from . import wallet as _wallet
except ImportError:                     # loaded as loose modules
    import wallet as _wallet


class LightClientError(Exception):
    pass


# The oldest block a Sapling note can live in; a restore with no birthday has
# to start here, which is the difference between a minute of scanning and a
# day of it. Always pass a birthday when you know one.
SAPLING_ACTIVATION = 419_200

# How far below the wallet's birthday to start the light client's scan.
#
# A spend is anchored to a commitment tree state some confirmations back, so a
# wallet whose birthday *is* the chain tip has scanned nothing usable and the
# prover refuses with "Must scan blocks first" -- which reads like a bug and
# is really an off-by-a-few-blocks. A margin costs seconds of scanning and
# removes the whole class of failure.
BIRTHDAY_MARGIN = 100

# lightwalletd endpoints. `zecrocks` and `ywallet` are names zcash-devtool
# resolves itself; anything else is passed through as host:port.
DEFAULT_SERVER = "zecrocks"

BUILD_MARKER = "keys.toml"              # written by `wallet restore-mnemonic`
DATA_DB = "data.sqlite"


# ── Where things live ───────────────────────────────────────────────────────

def base_dir() -> Path:
    base = Path(os.environ.get("ZCASH_LIGHTCLIENT_DIR")
                or Path.home() / ".mod" / "zcash" / "lightwallets")
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def wallet_dir(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe:
        raise LightClientError(f"invalid wallet name: {name!r}")
    return base_dir() / safe


def binary() -> str:
    """The zcash-devtool binary, or '' when it has not been built yet."""
    explicit = os.environ.get("ZCASH_DEVTOOL_BIN")
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else ""
    candidates = [Path.home() / ".mod" / "zcash" / "bin" / "zcash-devtool"]
    found = shutil.which("zcash-devtool")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return ""


def server() -> str:
    return os.environ.get("ZCASH_LIGHTWALLETD") or DEFAULT_SERVER


def network() -> str:
    return os.environ.get("ZCASH_NETWORK") or "main"


def available() -> dict:
    """Is there a proving backend on this host, and where."""
    b = binary()
    out = {"installed": bool(b), "binary": b or None,
           "server": server(), "network": network(),
           "backend": "zcash-devtool (zcash_client_backend + zcash_proofs)"}
    if b:
        # `zcash-devtool` has no --version; `--help` is the cheap liveness
        # check that it is the binary we think it is and that it runs here.
        try:
            v = subprocess.run([b, "--help"], capture_output=True, text=True,
                               timeout=20)
            out["runnable"] = v.returncode == 0
        except Exception:
            out["runnable"] = False
    else:
        out["how_to_install"] = ("run `m zcash/shielded_backend_install` (or "
                                "bash install_prover.sh) -- it builds the "
                                "prover from source, once, into "
                                "~/.mod/zcash/bin/")
    return out


# ── Running the binary ──────────────────────────────────────────────────────

def _run(args: list, stdin: str = None, timeout: int = 300,
         cwd: str = None) -> subprocess.CompletedProcess:
    b = binary()
    if not b:
        raise LightClientError(
            "no proving backend installed: build it once with "
            "`m zcash/shielded_backend_install`")
    env = dict(os.environ)
    env.setdefault("RUST_LOG", "info")
    try:
        return subprocess.run([b] + args, input=stdin, capture_output=True,
                              text=True, timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise LightClientError(
            f"the proving backend did not finish within {timeout}s "
            f"({' '.join(args[:3])})")


def _fail(cp: subprocess.CompletedProcess, what: str):
    """Turn a non-zero exit into the most useful line the tool printed."""
    text = (cp.stderr or "") + (cp.stdout or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    detail = ""
    for ln in reversed(lines):
        if "ERROR" in ln or "Error" in ln or "error" in ln:
            detail = ln
            break
    if not detail and lines:
        detail = lines[-1]
    raise LightClientError(f"{what}: {detail or 'exit %d' % cp.returncode}")


def _wallet_args(name: str) -> list:
    return ["wallet", "--wallet-dir", str(wallet_dir(name))]


# ── The age identity, sealed under the wallet password ──────────────────────

def _identity_path(name: str) -> Path:
    return wallet_dir(name) / "identity.enc"


def _seal_identity(name: str, identity_text: str, password: str):
    blob = _wallet._encrypt({"age_identity": identity_text}, password)
    p = _identity_path(name)
    p.write_text(json.dumps(blob))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


class _Identity:
    """The age identity on disk in the clear, for as short a time as possible."""

    def __init__(self, name: str, password: str):
        self.name, self.password, self.path = name, password, None

    def __enter__(self) -> str:
        p = _identity_path(self.name)
        if not p.exists():
            raise LightClientError(
                f"light wallet {self.name!r} has no sealed identity; run "
                f"shielded_sync_start once to set it up")
        text = _wallet._decrypt(json.loads(p.read_text()),
                                self.password)["age_identity"]
        fd, tmp = tempfile.mkstemp(prefix="zcash-id-", suffix=".txt",
                                   dir=str(wallet_dir(self.name)))
        os.write(fd, text.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        self.path = tmp
        return tmp

    def __exit__(self, *exc):
        if self.path and os.path.exists(self.path):
            try:                        # overwrite before unlinking
                size = os.path.getsize(self.path)
                with open(self.path, "wb") as f:
                    f.write(b"\0" * size)
            except OSError:
                pass
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.path = None
        return False


# ── Setting a light wallet up ───────────────────────────────────────────────

def initialized(name: str) -> bool:
    return (wallet_dir(name) / BUILD_MARKER).exists()


def init(name: str, mnemonic: str, password: str, birthday: int = None,
         timeout: int = 180) -> dict:
    """Restore the module's mnemonic into a light wallet, once.

    The birthday matters more than anything else here: it is the first block
    the scan has to look at. Passing the wallet's real birthday turns a
    multi-hour scan into a short one, and passing none means Sapling
    activation -- correct, but slow.
    """
    if not mnemonic:
        raise LightClientError("a mnemonic is required to set up a light wallet")
    d = wallet_dir(name)
    if initialized(name):
        return {"name": name, "wallet_dir": str(d), "created": False}
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass

    # zcash-devtool writes the age identity itself when the path is empty, so
    # point it at a temporary one and seal what it produces.
    fd, tmp_id = tempfile.mkstemp(prefix="zcash-id-", suffix=".txt", dir=str(d))
    os.close(fd)
    os.unlink(tmp_id)                   # it must not exist, or it is reused
    args = _wallet_args(name) + [
        "restore-mnemonic", "--name", name, "-i", tmp_id,
        "-n", network(), "-s", server(),
    ]
    if birthday:
        birthday = max(SAPLING_ACTIVATION, int(birthday) - BIRTHDAY_MARGIN)
        args += ["--birthday", str(birthday)]
    try:
        cp = _run(args, stdin=mnemonic.strip() + "\n", timeout=timeout)
        if cp.returncode != 0:
            _fail(cp, "could not set up the light wallet")
        _seal_identity(name, Path(tmp_id).read_text(), password)
    finally:
        if os.path.exists(tmp_id):
            try:
                os.unlink(tmp_id)
            except OSError:
                pass
    return {"name": name, "wallet_dir": str(d), "created": True,
            "birthday": birthday or SAPLING_ACTIVATION,
            "birthday_margin": BIRTHDAY_MARGIN,
            "server": server(), "network": network()}


def reset(name: str) -> dict:
    """Throw away the scanned state (not the keys) and start the scan over."""
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    cp = _run(_wallet_args(name) + ["reset"], timeout=120)
    if cp.returncode != 0:
        _fail(cp, "could not reset the light wallet")
    return {"name": name, "reset": True}


# ── Syncing, in the background ──────────────────────────────────────────────

def _job_path(name: str) -> Path:
    return wallet_dir(name) / "sync.json"


def _log_path(name: str) -> Path:
    return wallet_dir(name) / "sync.log"


def _read_job(name: str) -> dict:
    p = _job_path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def _running(job: dict) -> bool:
    pid = job.get("pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def sync_start(name: str) -> dict:
    """Start (or rejoin) a background sync for this light wallet."""
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    job = _read_job(name)
    if _running(job):
        return dict(job, already_running=True)

    log = _log_path(name)
    args = [binary()] + _wallet_args(name) + ["sync", "-s", server()]
    env = dict(os.environ)
    env.setdefault("RUST_LOG", "info")
    with open(log, "ab") as fh:
        fh.write(f"\n=== sync started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"against {server()} ===\n".encode())
        proc = subprocess.Popen(args, stdout=fh, stderr=fh,
                                stdin=subprocess.DEVNULL, env=env,
                                start_new_session=True)
    job = {"pid": proc.pid, "started": time.time(), "server": server(),
           "log": str(log)}
    _job_path(name).write_text(json.dumps(job))
    return dict(job, already_running=False)


def sync_stop(name: str) -> dict:
    job = _read_job(name)
    if not _running(job):
        return {"name": name, "running": False, "stopped": False}
    try:
        os.killpg(os.getpgid(int(job["pid"])), signal.SIGTERM)
    except OSError:
        try:
            os.kill(int(job["pid"]), signal.SIGTERM)
        except OSError:
            pass
    return {"name": name, "running": False, "stopped": True}


# `scan_queue.priority` is a ScanPriority: 0 Ignored, 10 Scanned, and
# everything above that is work still to do (Historic, OpenAdjacent,
# FoundNote, ChainTip, Verify). The tip range keeps a high priority *label*
# after being scanned, so "how much is left" is `priority > SCANNED`, not
# `priority > 0` -- the latter reads a freshly-synced wallet as behind.
SCAN_PRIORITY_SCANNED = 10


def _db_progress(name: str) -> dict:
    """Scan progress read straight out of the light client's own database.

    Read-only, and out of process: the sync holds the write lock, so this
    opens the file in immutable mode rather than waiting behind it.
    """
    db = wallet_dir(name) / DATA_DB
    if not db.exists():
        return {}
    out = {}
    try:
        conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT MAX(height) FROM blocks").fetchone()
            if row and row[0] is not None:
                out["max_scanned_height"] = int(row[0])
            row = conn.execute("SELECT MIN(birthday_height) FROM accounts").fetchone()
            if row and row[0] is not None:
                out["birthday"] = int(row[0])
            row = conn.execute(
                "SELECT COALESCE(SUM(block_range_end - block_range_start), 0), "
                "COUNT(*) FROM scan_queue WHERE priority > ?",
                (SCAN_PRIORITY_SCANNED,)).fetchone()
            if row:
                out["blocks_remaining"] = int(row[0])
                out["ranges_remaining"] = int(row[1])
            row = conn.execute(
                "SELECT MAX(block_range_end) FROM scan_queue").fetchone()
            if row and row[0] is not None:
                # scan ranges are half-open, so the last block is end - 1
                out["chain_tip_height"] = int(row[0]) - 1
        finally:
            conn.close()
    except sqlite3.Error:
        return out

    tip = out.get("chain_tip_height")
    birthday = out.get("birthday")
    if tip is not None and birthday is not None:
        span = max(1, tip - birthday)
        done = max(0, span - out.get("blocks_remaining", 0))
        out["percent"] = round(min(100.0, 100.0 * done / span), 2)
    scanned = out.get("max_scanned_height")
    out["synced"] = (out.get("blocks_remaining") == 0
                     and scanned is not None and tip is not None
                     and scanned >= tip)
    return out


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _log_tail(name: str, lines: int = 12) -> list:
    """The last few log lines, with the terminal colouring stripped.

    The tool writes ANSI escapes because it expects a terminal; this log is
    read by a JSON API and rendered in a browser, where they are noise.
    """
    p = _log_path(name)
    if not p.exists():
        return []
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return []
    out = []
    for ln in text.strip().splitlines()[-lines:]:
        ln = _ANSI.sub("", ln).strip()
        if ln:
            out.append(ln)
    return out


def status(name: str) -> dict:
    """Where this light wallet stands: syncing or not, and how far."""
    d = wallet_dir(name)
    out = {"name": name, "wallet_dir": str(d), "initialized": initialized(name),
           "backend_installed": bool(binary()), "server": server()}
    if not out["initialized"]:
        out["note"] = ("this wallet has no light client yet -- "
                       "shielded_sync_start sets one up from its mnemonic")
        return out
    job = _read_job(name)
    out["syncing"] = _running(job)
    if job.get("started"):
        out["sync_started"] = job["started"]
        out["sync_seconds"] = round(time.time() - job["started"], 1)
    out.update(_db_progress(name))
    if out["syncing"]:
        # A scan in flight has not finished, whatever the last committed
        # range says; calling it synced is how a send gets attempted against
        # a half-built commitment tree.
        out["synced"] = False
    out["log"] = _log_tail(name)
    out["spendable"] = bool(out.get("synced"))
    return out


# ── Reading the wallet ──────────────────────────────────────────────────────

def balance(name: str, min_confirmations: int = None) -> dict:
    """Spendable balance per pool, in zatoshi, as the light client sees it."""
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    args = _wallet_args(name) + ["balance", "--json"]
    if min_confirmations:
        args += ["--min-confirmations", str(int(min_confirmations))]
    cp = _run(args, timeout=120)
    if cp.returncode != 0:
        _fail(cp, "could not read the light wallet balance")
    line = [ln for ln in cp.stdout.strip().splitlines() if ln.startswith("{")]
    if not line:
        raise LightClientError("the proving backend returned no balance")
    raw = json.loads(line[-1])
    zat = lambda k: int(raw.get(k) or 0)                      # noqa: E731
    total = zat("total")
    return {
        "name": name,
        "total_zat": total, "total_zec": total / 1e8,
        "sapling_spendable_zat": zat("sapling_spendable"),
        "orchard_spendable_zat": zat("orchard_spendable"),
        "transparent_spendable_zat": zat("transparent_spendable"),
        "shielded_spendable_zec": (zat("sapling_spendable")
                                   + zat("orchard_spendable")) / 1e8,
        "chain_tip_height": zat("chain_tip_height"),
    }


def addresses(name: str) -> dict:
    """The light client's own addresses -- the same ones this module derives."""
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    cp = _run(_wallet_args(name) + ["list-addresses"], timeout=120)
    if cp.returncode != 0:
        _fail(cp, "could not list light wallet addresses")
    found = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    return {"name": name, "addresses": found}


# ── Spending ────────────────────────────────────────────────────────────────

def send(name: str, password: str, to: str, zatoshis: int, memo: str = None,
         min_confirmations: int = None, timeout: int = 600) -> dict:
    """Build, prove and broadcast a shielded spend. This moves real money.

    There is no dry run here: proving and broadcasting are one act inside the
    light client, and a proof built without submitting it is a proof thrown
    away. The caller (`Mod.shielded_send`) is what holds the `broadcast=True`
    gate, so nothing reaches this function by accident.
    """
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    if zatoshis <= 0:
        raise LightClientError("amount must be positive")
    args = _wallet_args(name) + [
        "send", "--address", to, "--value", str(int(zatoshis)),
        "-s", server(),
    ]
    if memo:
        args += ["--memo", memo]
    if min_confirmations:
        args += ["--min-confirmations", str(int(min_confirmations))]

    started = time.time()
    with _Identity(name, password) as identity:
        cp = _run(args + ["-i", identity], timeout=timeout)
    if cp.returncode != 0:
        _translate_send_error(cp)
        _fail(cp, "the shielded send failed")

    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    txid = _extract_txid(text)
    return {
        "sent": True, "mode": "BROADCAST", "txid": txid,
        "to": to, "amount_zec": zatoshis / 1e8, "amount_zat": int(zatoshis),
        "memo": memo, "seconds": round(time.time() - started, 1),
        "pool": "shielded", "prover": "local (Groth16 + Halo 2)",
        "server": server(),
        "output": [ln for ln in text.splitlines() if ln.strip()][-12:],
        "note": ("the transaction is broadcast; it is spendable by the "
                 "recipient once mined. Re-sync to see the change note."),
    }


def _translate_send_error(cp: subprocess.CompletedProcess):
    """Say what actually went wrong, in the caller's vocabulary.

    The prover's own messages are accurate and unhelpful: "Must scan blocks
    first" is what a wallet says when its scan has no depth behind the anchor,
    not when nothing has been scanned at all.
    """
    text = (cp.stderr or "") + (cp.stdout or "")
    if "Must scan blocks first" in text:
        raise LightClientError(
            "the light client has not scanned deeply enough to anchor a "
            "spend: a proof commits to a commitment tree state several "
            "confirmations back. Run shielded_sync_start again once the "
            "chain has moved on, or re-create the light client with an "
            "earlier birthday.")
    if "Insufficient balance" in text or "InsufficientFunds" in text:
        raise LightClientError(
            "not enough spendable shielded value for that amount plus the "
            "ZIP-317 fee. Notes also need confirmations before they can be "
            "spent, so a payment that arrived moments ago is not spendable "
            "yet.")


def _extract_txid(text: str) -> str:
    """Pull the txid out of the tool's output without guessing at its wording."""
    for line in text.splitlines():
        if "txid" in line.lower() or "transaction" in line.lower():
            m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
            if m:
                return m.group(1).lower()
    m = re.findall(r"\b([0-9a-fA-F]{64})\b", text)
    return m[-1].lower() if m else None


def shield(name: str, password: str, timeout: int = 600) -> dict:
    """Move this wallet's transparent balance into the shielded pool."""
    if not initialized(name):
        raise LightClientError(f"no light wallet for {name!r}")
    with _Identity(name, password) as identity:
        cp = _run(_wallet_args(name) + ["shield", "-i", identity, "-s", server()],
                  timeout=timeout)
    if cp.returncode != 0:
        _fail(cp, "could not shield transparent funds")
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    return {"shielded": True, "txid": _extract_txid(text),
            "output": [ln for ln in text.splitlines() if ln.strip()][-12:]}
