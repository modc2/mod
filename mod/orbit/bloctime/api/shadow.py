"""
ShadowBloc — private BlocTime on Starknet (Cairo contract in ../cairo).

This file is the server half: it runs a local starknet-devnet, deploys the
PaperToken + ShadowBloc contracts, reads them over raw JSON-RPC and posts
transactions through a RELAYER account. The relayer is what keeps the staker's
own account off the chain: every stake is paid by the relayer (paper NAT on
devnet), and every claim / unstake / rekey is a signature the browser made
with a one-time Stark key that this server never sees.

What this server learns: a note's public key, the request's IP, and the
payout address you choose. What it never learns: the note's private key.
The chain learns less: no account address is stored against a note.

Layout: state lives off-tree in ~/.mod/bloctime/shadow/ (devnet dump,
devnet account keys 0600, deployment record, declared-class cache).
Toolchain: scarb 2.8.4 + sncast 0.31 + starknet-devnet 0.2.4 in ~/.local/bin.
"""

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAIRO = HERE.parent / "cairo"
STATE = Path(os.path.expanduser("~/.mod/bloctime/shadow"))
DEVNET_DIR = STATE / "devnet"
ACCOUNTS = DEVNET_DIR / "accounts.json"
DEPLOY = STATE / "deployment.json"
CLASSES = STATE / "classes.json"
BIN = os.path.expanduser("~/.local/bin")

DEVNET_PORT = int(os.environ.get("BLOCTIME_SHADOW_PORT", 8853))
RPC = f"http://127.0.0.1:{DEVNET_PORT}/rpc"
SECTION = "alpha-sepolia"  # devnet reports SN_SEPOLIA; sncast keys off that
ONE = 10 ** 18
U128 = 1 << 128
WEEK = 604800
FRIDAY_1700 = 147600
RELAYER_FLOAT = 10_000_000 * ONE  # paper NAT the relayer stakes on users' behalf

_write_lock = threading.Lock()  # one relayer nonce stream; serialise writes
_cache: dict = {}


# ── plumbing ────────────────────────────────────────────────────────────

def _env():
    env = dict(os.environ)
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "")
    return env


def _load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def _save(p: Path, obj, private=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, p)


def _selector(name: str) -> str:
    from eth_hash.auto import keccak
    return hex(int.from_bytes(keccak(name.encode()), "big") & ((1 << 250) - 1))


def _felt(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _u256(lo: int, hi: int) -> int:
    return lo + hi * U128


def _split(v: int):
    return [v % U128, v // U128]


def _short(text: str) -> int:
    return int.from_bytes(text.encode(), "big")


def _rpc(method, params):
    req = urllib.request.Request(
        RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                              "params": params}).encode(),
        headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if "error" in r:
        raise RuntimeError(r["error"].get("message", r["error"]))
    return r["result"]


def _devnet_http(path, body=None):
    url = f"http://127.0.0.1:{DEVNET_PORT}{path}"
    if body is None:
        return json.loads(urllib.request.urlopen(url, timeout=5).read())
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


# ── devnet ──────────────────────────────────────────────────────────────

def _alive() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{DEVNET_PORT}/is_alive", timeout=1).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def devnet(action: str = "status") -> dict:
    """start | stop | status for the ShadowBloc devnet (state survives restarts)."""
    if action == "stop":
        r = subprocess.run(["fuser", "-k", f"{DEVNET_PORT}/tcp"], capture_output=True)
        return {"stopped": r.returncode == 0}
    if action == "start" and not _alive():
        exe = os.path.join(BIN, "starknet-devnet")
        if not os.path.exists(exe):
            raise RuntimeError("starknet-devnet not in ~/.local/bin")
        DEVNET_DIR.mkdir(parents=True, exist_ok=True)
        dump = DEVNET_DIR / "devnet.json"
        cmd = [exe, "--host", "127.0.0.1", "--port", str(DEVNET_PORT), "--seed", "0",
               "--accounts", "3", "--dump-on", "block", "--dump-path", str(dump)]
        log = open(DEVNET_DIR / "devnet.log", "a")
        subprocess.Popen(cmd, stdout=log, stderr=log, cwd=DEVNET_DIR, start_new_session=True)
        for _ in range(200):
            if _alive():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("devnet did not come up (see ~/.mod/bloctime/shadow/devnet/devnet.log)")
    up = _alive()
    if up and not ACCOUNTS.exists():
        rows = _devnet_http("/predeployed_accounts")
        cls = _rpc("starknet_getClassHashAt", ["latest", rows[0]["address"]])
        book = {name: {"address": r["address"], "class_hash": cls, "deployed": True,
                       "legacy": False, "private_key": r["private_key"],
                       "public_key": r["public_key"], "type": "open_zeppelin"}
                for name, r in zip(("owner", "relayer", "payee"), rows)}
        _save(ACCOUNTS, {SECTION: book}, private=True)
    out = {"up": up, "port": DEVNET_PORT}
    if up:
        out["block"] = _rpc("starknet_blockNumber", [])
    return out


def _account(name: str) -> str:
    return _load(ACCOUNTS, {}).get(SECTION, {}).get(name, {}).get("address", "")


# ── sncast ──────────────────────────────────────────────────────────────

def _sncast(args, account):
    cmd = [os.path.join(BIN, "sncast"), "--accounts-file", str(ACCOUNTS),
           "--account", account, "--json"] + args + ["--url", RPC]
    out = subprocess.run(cmd, cwd=CAIRO, capture_output=True, text=True,
                         timeout=600, env=_env())
    text = out.stdout + "\n" + out.stderr
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and '"command"' in line:
            r = json.loads(line)
            if "error" not in r:
                return r
    m = re.findall(r"Failure reason: (?:0x[0-9a-f]+ )?\(?'([^']*)'\)?", text)
    raise RuntimeError(m[-1] if m else text.strip()[-600:])


def _src_hash():
    h = hashlib.sha256()
    for p in sorted(CAIRO.rglob("*")):
        if "target" in p.parts or not p.is_file():
            continue
        if p.suffix in (".cairo", ".toml"):
            h.update(str(p.relative_to(CAIRO)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _declare(name):
    src = _src_hash()
    known = _load(CLASSES, {}).get(name, {})
    ch = known.get("class_hash") if known.get("src") == src else None
    if ch:
        try:
            _rpc("starknet_getClass", ["latest", ch])
            return ch
        except Exception:  # noqa: BLE001
            pass
    try:
        ch = _sncast(["declare", "--contract-name", name, "--fee-token", "eth"], "owner")["class_hash"]
    except RuntimeError as e:
        m = re.search(r"(0x[0-9a-fA-F]+) is already declared", str(e))
        if not m:
            raise
        ch = m.group(1)
    c = _load(CLASSES, {})
    c[name] = {"src": src, "class_hash": ch}
    _save(CLASSES, c)
    return ch


def _deploy_class(class_hash, ctor):
    r = _sncast(["deploy", "--class-hash", class_hash, "--fee-token", "eth",
                 "--constructor-calldata", *map(hex, ctor)], "owner")
    return r["contract_address"]


def _invoke(address, fn, calldata, account="relayer"):
    args = ["invoke", "--contract-address", address, "--function", fn, "--fee-token", "eth"]
    if calldata:
        args += ["--calldata", *map(hex, calldata)]
    return _sncast(args, account).get("transaction_hash")


def _call(address, fn, calldata=()):
    r = _rpc("starknet_call", [{"contract_address": address,
                                "entry_point_selector": _selector(fn),
                                "calldata": [hex(c) for c in calldata]}, "latest"])
    return [int(x, 16) for x in r]


# ── deployment ──────────────────────────────────────────────────────────

def _deployed_ok(d) -> bool:
    try:
        _rpc("starknet_getClassHashAt", ["latest", d["shadow"]])
        return True
    except Exception:  # noqa: BLE001
        return False


def deploy(force: bool = False) -> dict:
    """Declare + deploy PaperToken and ShadowBloc on the devnet, then give the
    relayer its paper NAT float and approve the pool."""
    with _write_lock:
        devnet("start")
        d = _load(DEPLOY, {})
        if d and not force and _deployed_ok(d):
            return {"ok": True, "fresh": False, **d}
        owner, relayer = _account("owner"), _account("relayer")
        tok_ch = _declare("PaperToken")
        sb_ch = _declare("ShadowBloc")
        token = _deploy_class(tok_ch, [int(relayer, 16)])
        shadow = _deploy_class(sb_ch, [int(owner, 16), int(token, 16)])
        _invoke(token, "mint", [int(relayer, 16), *_split(RELAYER_FLOAT)])
        _invoke(token, "approve", [int(shadow, 16), U128 - 1, U128 - 1])  # max
        d = {"network": "starknet-devnet", "rpc": RPC, "shadow": shadow, "token": token,
             "owner": owner, "relayer": relayer, "payee": _account("payee"),
             "class_hash": sb_ch, "at": int(time.time())}
        _save(DEPLOY, d)
        _cache.clear()
        return {"ok": True, "fresh": True, **d}


def ensure() -> dict:
    """Devnet up + contracts deployed (redeploys if the devnet lost them)."""
    if not _alive():
        devnet("start")
    d = _load(DEPLOY, {})
    if d and _deployed_ok(d):
        return d
    return deploy(force=True)


# ── reads ───────────────────────────────────────────────────────────────

NOTE_FIELDS = 13  # key denom tier amount(2) start unlock bloc(2) debt(2) nonce open


def _decode_note(f, i=0):
    return {
        "key": hex(f[i]), "denom": f[i + 1], "tier": f[i + 2],
        "amount": str(_u256(f[i + 3], f[i + 4])), "start": f[i + 5], "unlock": f[i + 6],
        "bloc": str(_u256(f[i + 7], f[i + 8])), "nonce": f[i + 11], "open": bool(f[i + 12]),
    }


def _array_u256(f):
    n = f[0]
    return [str(_u256(f[1 + 2 * k], f[2 + 2 * k])) for k in range(n)]


def _array_u64(f):
    return f[1:1 + f[0]]


def info() -> dict:
    """Everything the PRIVATE tab needs in one read: deployment, menus,
    totals, schedule and bucket (anonymity-set) sizes."""
    d = ensure()
    sb = d["shadow"]
    s = _call(sb, "stats")
    stats = {
        "totalStaked": str(_u256(s[0], s[1])), "totalBloc": str(_u256(s[2], s[3])),
        "openNotes": s[4], "noteCount": s[5], "pot": str(_u256(s[6], s[7])),
        "distributed": str(_u256(s[8], s[9])), "nextDistribution": s[10],
        "priceUsdMicro": s[11], "now": s[12],
    }
    menus = _cache.get("menus")
    if not menus:
        menus = {"denoms": _array_u256(_call(sb, "denoms")), "tiers": _array_u64(_call(sb, "tiers"))}
        menus["quotes"] = [[str(_u256(*_call(sb, "quote", [di, ti])))
                            for ti in range(len(menus["tiers"]))]
                           for di in range(len(menus["denoms"]))]
        _cache["menus"] = menus
    buckets = [[_call(sb, "bucket_size", [di, ti])[0] for ti in range(len(menus["tiers"]))]
               for di in range(len(menus["denoms"]))]
    chain = _rpc("starknet_chainId", [])
    return {"deployment": {k: d[k] for k in ("network", "shadow", "token", "relayer", "payee")},
            "chainId": chain, "stats": stats, **menus, "buckets": buckets,
            "domain": "bloctime.shadow.v1"}


def note(id: int) -> dict:
    d = ensure()
    f = _call(d["shadow"], "get_note", [int(id)])
    n = _decode_note(f)
    n["id"] = int(id)
    n["pending"] = str(_u256(*_call(d["shadow"], "pending", [int(id)])))
    return n


def notes(limit: int = 200) -> list:
    """The public ledger as the chain sees it: notes, keys, buckets — no owners."""
    d = ensure()
    count = _call(d["shadow"], "stats")[5]
    start = max(0, count - int(limit))
    return [note(i) for i in range(count - 1, start - 1, -1)]


def message_hash(action: str, id: int, nonce: int, arg) -> str:
    d = ensure()
    return hex(_call(d["shadow"], "message_hash",
                     [_short(action), int(id), int(nonce), _felt(arg)])[0])


def balance(address: str) -> str:
    d = ensure()
    return str(_u256(*_call(d["token"], "balance_of", [_felt(address)])))


# ── relayed writes ──────────────────────────────────────────────────────

def stake(key, denom: int, tier: int) -> dict:
    """The relayer pays the stake; the note belongs to `key` (a Stark public
    key generated in the browser). On chain the depositor is the relayer for
    everyone, so nobody can tell whose stake this is."""
    k = _felt(key)
    if not 0 < k < 2 ** 251:
        raise ValueError("key must be a Stark public key (felt)")
    with _write_lock:
        d = ensure()
        if int(balance(d["relayer"])) < RELAYER_FLOAT // 10:
            _invoke(d["token"], "mint", [int(d["relayer"], 16), *_split(RELAYER_FLOAT)])
        tx = _invoke(d["shadow"], "stake", [k, int(denom), int(tier)])
        id = _call(d["shadow"], "stats")[5] - 1
    return {"id": id, "tx": tx, "note": note(id)}


def _signed(fn, id, arg, r, s):
    with _write_lock:
        d = ensure()
        tx = _invoke(d["shadow"], fn, [int(id), _felt(arg), _felt(r), _felt(s)])
    return {"id": int(id), "tx": tx, "note": note(id)}


def claim(id: int, recipient, r, s) -> dict:
    return _signed("claim", id, recipient, r, s)


def unstake(id: int, recipient, r, s) -> dict:
    return _signed("unstake", id, recipient, r, s)


def rekey(id: int, new_key, r, s) -> dict:
    return _signed("rekey", id, new_key, r, s)


def distribute() -> dict:
    """Permissionless weekly sweep (reverts before Friday 17:00 UTC)."""
    with _write_lock:
        d = ensure()
        tx = _invoke(d["shadow"], "distribute", [])
    return {"tx": tx, **info()["stats"]}


def fund_pot(amount_nat: float) -> dict:
    """Owner: mint paper NAT to the relayer and pour it into the weekly pot."""
    amt = int(float(amount_nat) * ONE)
    if amt <= 0:
        raise ValueError("amount must be positive")
    with _write_lock:
        d = ensure()
        _invoke(d["token"], "mint", [int(d["relayer"], 16), *_split(amt)])
        tx = _invoke(d["shadow"], "fund_pot", _split(amt))
    return {"tx": tx, **info()["stats"]}


def warp(to: str = "friday") -> dict:
    """Owner, devnet only: move the devnet clock. to='friday' jumps to the
    next distribution window; an integer adds that many seconds."""
    ensure()
    if to == "friday":
        now = info()["stats"]["nextDistribution"]
        _devnet_http("/set_time", {"time": int(now) + 60, "generate_block": True})
    else:
        _devnet_http("/increase_time", {"time": int(to)})
        _devnet_http("/create_block", {})
    return info()["stats"]
