"""promptland api — wallet-authenticated prompt store with CID sharing.

Auth mirrors the build console: GET /auth/challenge hands out a nonce'd
message, the wallet signs it (MetaMask personal_sign, or an in-browser
ethers key), POST /auth/verify recovers the signer and mints an HMAC bearer
token (address:timestamp:hmac). The first address ever to verify claims
ownership in ~/.mod/promptland/owner.json; sign-in stays open after that —
every wallet gets its own private library.

Prompts live per-address under ~/.mod/promptland/prompts/<addr>/. Sharing
pins the prompt to localfs and lists its CID in a public gallery, so any
other wallet (or fleet module) can read or import it by CID.
"""

import hashlib
import hmac as hmac_mod
import importlib.util as _ilu
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── localfs (CID store) — same canonical implementation orbit/localfs wraps ──
_CORE_LOCALFS = Path(__file__).resolve().parents[3] / "core" / "store" / "src" / "localfs" / "localfs" / "mod.py"
_spec = _ilu.spec_from_file_location("_promptland_localfs", _CORE_LOCALFS)
_lfs_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_lfs_mod)
_localfs = _lfs_mod.LocalFS()

# ── private state (off-chain, never committed) ───────────────────────────────
STATE = Path.home() / ".mod" / "promptland"
PROMPTS_DIR = STATE / "prompts"
SHARED_INDEX = STATE / "shared.json"
OWNER_FILE = STATE / "owner.json"
SECRET_FILE = STATE / "server.secret"

CHALLENGE_TTL = 300
MAX_CHALLENGES = 512
TOKEN_TTL = 7 * 86400
SHARE_TYPE = "promptland/prompt@1"

_lock = threading.Lock()
_challenges: Dict[str, tuple] = {}  # addr -> (message, issued_at)


def _init_state():
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    PROMPTS_DIR.mkdir(exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        os.chmod(SECRET_FILE, 0o600)


_init_state()


def _secret() -> bytes:
    return SECRET_FILE.read_text().strip().encode()


def _norm_addr(addr: str) -> str:
    a = (addr or "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", a):
        raise HTTPException(400, "Invalid EVM address")
    return a


def _owner() -> Optional[str]:
    if OWNER_FILE.exists():
        try:
            return json.loads(OWNER_FILE.read_text()).get("owner")
        except Exception:
            return None
    return None


def _role_of(addr: str) -> str:
    return "owner" if addr == _owner() else "user"


# ── tokens: address:timestamp:hmac(sha256, server.secret) ────────────────────

def mint_token(addr: str) -> str:
    payload = f"{addr}:{int(time.time())}"
    sig = hmac_mod.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def validate_token(token: str) -> str:
    parts = token.split(":")
    if len(parts) != 3:
        raise HTTPException(401, "Malformed token")
    addr, ts, sig = parts
    payload = f"{addr}:{ts}"
    expect = hmac_mod.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac_mod.compare_digest(expect, sig):
        raise HTTPException(401, "Invalid token signature")
    try:
        issued = int(ts)
    except ValueError:
        raise HTTPException(401, "Malformed token timestamp")
    if time.time() - issued > TOKEN_TTL:
        raise HTTPException(401, "Session expired — sign in again")
    return addr


def require_auth(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Authorization: Bearer token required")
    return validate_token(header[7:].strip())


# ── prompt storage ───────────────────────────────────────────────────────────

def _addr_dir(addr: str) -> Path:
    d = PROMPTS_DIR / addr
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prompt_path(addr: str, pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8}", pid):
        raise HTTPException(400, "Invalid prompt id")
    return _addr_dir(addr) / f"{pid}.json"


def _read_prompt(addr: str, pid: str) -> dict:
    p = _prompt_path(addr, pid)
    if not p.exists():
        raise HTTPException(404, "Prompt not found")
    return json.loads(p.read_text())


def _read_shared_index() -> List[dict]:
    if SHARED_INDEX.exists():
        try:
            data = json.loads(SHARED_INDEX.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _write_shared_index(entries: List[dict]):
    SHARED_INDEX.write_text(json.dumps(entries, indent=2))


# ── app ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="promptland", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _config() -> dict:
    p = Path(__file__).resolve().parents[1] / "config.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


@app.get("/")
def info():
    cfg = _config()
    return {
        "name": "promptland",
        "version": cfg.get("version", "0.1.0"),
        "description": cfg.get("description"),
        "urls": cfg.get("urls"),
        "endpoints": cfg.get("endpoints"),
        "auth": {
            "flow": "GET /auth/challenge?address= → wallet signs → POST /auth/verify → Bearer token",
            "open_signin": True,
            "owner_claimed": _owner() is not None,
        },
    }


@app.get("/health")
def health():
    return {"ok": True, "service": "promptland", "ts": int(time.time())}


@app.get("/owner")
def owner():
    o = _owner()
    return {"has_owner": o is not None, "owner": o}


# ── auth ─────────────────────────────────────────────────────────────────────

@app.get("/auth/challenge")
def challenge(address: str):
    addr = _norm_addr(address)
    nonce = secrets.token_hex(16)
    message = (
        "Sign this message to authenticate with Promptland.\n\n"
        f"Address: {addr}\nNonce: {nonce}"
    )
    now = time.time()
    with _lock:
        for k in [k for k, (_, t) in _challenges.items() if now - t > CHALLENGE_TTL]:
            del _challenges[k]
        while len(_challenges) >= MAX_CHALLENGES:
            oldest = min(_challenges, key=lambda k: _challenges[k][1])
            del _challenges[oldest]
        _challenges[addr] = (message, now)
    return {"message": message}


class VerifyReq(BaseModel):
    address: str
    signature: str
    message: str


@app.post("/auth/verify")
def verify(req: VerifyReq):
    addr = _norm_addr(req.address)
    now = time.time()
    with _lock:
        entry = _challenges.get(addr)
        if not entry or entry[0] != req.message or now - entry[1] > CHALLENGE_TTL:
            raise HTTPException(400, "Invalid or expired challenge")
    try:
        recovered = Account.recover_message(
            encode_defunct(text=req.message), signature=req.signature
        ).lower()
    except Exception as e:
        raise HTTPException(401, f"Signature verification failed: {e}")
    if recovered != addr:
        raise HTTPException(401, "Signature does not match address")
    with _lock:
        _challenges.pop(addr, None)

    # First verified signer ever claims ownership — same as the build console.
    if _owner() is None:
        OWNER_FILE.write_text(json.dumps({"owner": addr}, indent=2))
        os.chmod(OWNER_FILE, 0o600)
        print(f"✓ First user authenticated - set as owner: {addr}")

    return {"token": mint_token(addr), "address": addr, "role": _role_of(addr)}


@app.get("/auth/session")
def session(request: Request):
    addr = require_auth(request)
    return {"address": addr, "role": _role_of(addr)}


# ── prompts (per-wallet library) ─────────────────────────────────────────────

class PromptReq(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = None
    body: str


@app.get("/prompts")
def list_prompts(request: Request):
    addr = require_auth(request)
    out = []
    for f in sorted(_addr_dir(addr).glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    out.sort(key=lambda p: p.get("updated", 0), reverse=True)
    return {"prompts": out}


@app.post("/prompts")
def save_prompt(request: Request, req: PromptReq):
    addr = require_auth(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    if not req.body.strip():
        raise HTTPException(400, "Prompt body required")
    now = int(time.time())
    if req.id:
        prompt = _read_prompt(addr, req.id)
    else:
        prompt = {"id": secrets.token_hex(4), "created": now}
    prompt.update(
        {
            "name": name,
            "description": (req.description or "").strip(),
            "tags": [t.strip() for t in (req.tags or []) if t.strip()][:12],
            "body": req.body,
            "updated": now,
        }
    )
    _prompt_path(addr, prompt["id"]).write_text(json.dumps(prompt, indent=2))
    return {"prompt": prompt}


@app.get("/prompts/{pid}")
def get_prompt(request: Request, pid: str):
    addr = require_auth(request)
    return {"prompt": _read_prompt(addr, pid)}


@app.delete("/prompts/{pid}")
def delete_prompt(request: Request, pid: str):
    addr = require_auth(request)
    p = _prompt_path(addr, pid)
    if not p.exists():
        raise HTTPException(404, "Prompt not found")
    p.unlink()
    return {"deleted": pid}


# ── sharing (localfs CIDs + public gallery) ──────────────────────────────────

@app.post("/prompts/{pid}/share")
def share_prompt(request: Request, pid: str):
    addr = require_auth(request)
    prompt = _read_prompt(addr, pid)
    payload = {
        "type": SHARE_TYPE,
        "name": prompt["name"],
        "description": prompt.get("description", ""),
        "tags": prompt.get("tags", []),
        "body": prompt["body"],
        "author": addr,
        "shared_at": int(time.time()),
    }
    cid = _localfs.put(payload, pin=True)
    entry = {
        "cid": cid,
        "name": payload["name"],
        "description": payload["description"],
        "tags": payload["tags"],
        "author": addr,
        "ts": payload["shared_at"],
    }
    with _lock:
        entries = [e for e in _read_shared_index() if e.get("cid") != cid]
        entries.insert(0, entry)
        _write_shared_index(entries)
    # Remember the latest CID on the source prompt so the library can badge it.
    prompt["cid"] = cid
    _prompt_path(addr, pid).write_text(json.dumps(prompt, indent=2))
    return {"cid": cid, "prompt": prompt}


@app.get("/shared")
def list_shared():
    return {"shared": _read_shared_index()}


@app.get("/shared/{cid}")
def get_shared(cid: str):
    prompt = _load_shared_cid(cid)
    return {"cid": cid, "prompt": prompt}


def _load_shared_cid(cid: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9]{40,80}", cid):
        raise HTTPException(400, "Invalid CID")
    try:
        data = _localfs.get(cid)
    except Exception:
        raise HTTPException(404, "CID not found in localfs")
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except Exception:
            raise HTTPException(404, "CID does not hold a promptland prompt")
    if not isinstance(data, dict) or data.get("type") != SHARE_TYPE:
        raise HTTPException(404, "CID does not hold a promptland prompt")
    return data


@app.delete("/shared/{cid}")
def unshare(request: Request, cid: str):
    addr = require_auth(request)
    with _lock:
        entries = _read_shared_index()
        entry = next((e for e in entries if e.get("cid") == cid), None)
        if not entry:
            raise HTTPException(404, "CID not in gallery")
        if entry.get("author") != addr and _role_of(addr) != "owner":
            raise HTTPException(403, "Only the author or the owner can delist")
        _write_shared_index([e for e in entries if e.get("cid") != cid])
    return {"delisted": cid}


class ImportReq(BaseModel):
    cid: str


@app.post("/import")
def import_prompt(request: Request, req: ImportReq):
    addr = require_auth(request)
    data = _load_shared_cid(req.cid.strip())
    now = int(time.time())
    prompt = {
        "id": secrets.token_hex(4),
        "name": data["name"],
        "description": data.get("description", ""),
        "tags": data.get("tags", []),
        "body": data["body"],
        "created": now,
        "updated": now,
        "imported_from": req.cid.strip(),
        "original_author": data.get("author"),
    }
    _prompt_path(addr, prompt["id"]).write_text(json.dumps(prompt, indent=2))
    return {"prompt": prompt}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PROMPTLAND_PORT", "50580")))
