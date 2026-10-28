"""Who may make this module's agent act.

Reading is open: the interface, /health, /info, /agents, /readme describe the
module and cost nothing — and GET /agents MUST stay open, it is the probe
orbit/build sends before it will mount this module as an agent backend.

Everything else runs a third-party CLI with its approval prompts off, which is
this host's own shell. So, unlike a model-only module, a valid signature is not
enough — the signer has to be one of this module's owners:

    secret   Authorization: Bearer `cat ~/.mod/<name>/server.secret`
    local    a loopback request that did not come through the gateway (Caddy
             stamps X-Forwarded-For on everything it proxies)
    owner    a mod-protocol token (body `key`, or `token:` header) whose signer
             is config.json `owner` or listed in `owners`
"""
import hmac
import json
import os
import secrets
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPEN = {"", "/", "/health", "/info", "/agents", "/readme", "/env", "/setup"}
OPEN_GET_ONLY = {"/env", "/setup"}    # reading which keys are set is fine; writing is not


class Denied(Exception):
    def __init__(self, why, hint=None):
        super().__init__(why)
        self.why = why
        self.hint = hint


def _cfg():
    return json.loads((HERE / "config.json").read_text())


def state_dir() -> Path:
    return Path(os.path.expanduser(f"~/.mod/{_cfg()['name']}"))


def owners():
    cfg = _cfg()
    got = [cfg.get("owner")] + list(cfg.get("owners") or [])
    return {str(a).lower() for a in got if a}


def secret(create=True):
    f = state_dir() / "server.secret"
    try:
        got = f.read_text().strip()
        if got:
            return got
    except OSError:
        pass
    if not create:
        return ""
    f.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    return token


def presented(headers):
    raw = headers.get("authorization") or ""
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def is_local(client_addr, headers):
    if (client_addr or "") not in ("127.0.0.1", "::1", "localhost"):
        return False
    return not any(headers.get(h) for h in ("x-forwarded-for", "x-forwarded-host", "x-real-ip"))


def signer(key):
    """The address behind a mod-protocol token, or None. A missing auth module
    is a refusal, never a pass."""
    if not key:
        return None
    try:
        import mod as m
        return str(m.mod("auth")().verify(key)["key"]).lower()
    except Exception:
        return None


def guard(path, method="GET", headers=None, client_addr=None, key=None):
    """Raise Denied unless this request may act; returns who is asking."""
    headers = headers or {}
    clean = (path or "/").rstrip("/") or "/"
    if clean in OPEN and (method == "GET" or clean not in OPEN_GET_ONLY):
        return "public"
    token = presented(headers)
    if token and hmac.compare_digest(token, secret()):
        return "owner"
    if is_local(client_addr, headers):
        return "local"
    who = signer(key or headers.get("token"))
    if who and who in owners():
        return who
    if who:
        raise Denied(f"{who} is signed in but does not own this module — it runs a "
                     f"CLI on this host's shell, so only its owners may")
    raise Denied(f"{clean} runs on this host's shell — sign in as the module owner",
                 hint="sign in with the owner wallet, send Authorization: Bearer "
                      "<~/.mod/<name>/server.secret>, or call from localhost")
