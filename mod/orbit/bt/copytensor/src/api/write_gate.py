"""Write gate — the copy book moves real TAO, so only the operator changes it.

copytensor is public twice over: {host}/copytensor/api (caddy straight to
:50150) and {host}/bt/ct/* (bt's submod proxy). Both land here, so this is
the one place writes are checked. Same rule as bt's `_is_operator`:

  * a request made on this machine to localhost — no proxy headers, no
    foreign browser origin (the desk agent's MCP loopback calls pass), or
  * `Authorization: Bearer <token>`, where the token is
    $COPYTENSOR_WRITE_TOKEN, else $BT_WRITE_TOKEN, else ~/.mod/bt/write_token
    (bt.server creates that file, so one token unlocks both consoles).

Reads stay open. A few non-GET routes stay open too, because they are
already scoped or change nothing: strats are owned by the caller's
X-Owner-Key, a backtest and a dry-run sync are pure reads, /mcp parks every
write for an approval (and answering one IS gated), and /agent/ask can only
propose writes the same way.
"""
import os
import re
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

TOKEN_FILE = os.path.expanduser(os.environ.get(
    "BT_WRITE_TOKEN_FILE", "~/.mod/bt/write_token"))
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-real-ip")
_SAFE = {"GET", "HEAD", "OPTIONS"}

# non-GET routes anyone may call (see module docstring)
_OPEN = re.compile(r"^/(strats(/backtest|/[^/]+(/clone)?)?|mcp|agent/ask)/?$")


def token() -> Optional[str]:
    t = os.environ.get("COPYTENSOR_WRITE_TOKEN") or os.environ.get("BT_WRITE_TOKEN")
    if t:
        return t
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _hostname(value: str) -> str:
    v = value.split("://", 1)[-1].split("/", 1)[0]
    if v.startswith("["):
        return v[1:].split("]", 1)[0]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def is_operator(request: Request) -> bool:
    t = token()
    if t and request.headers.get("authorization", "") == f"Bearer {t}":
        return True
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK and not client.startswith("127."):
        return False
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False          # came in through caddy / bt / next, not from this box
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK:
        return False
    origin = request.headers.get("origin")
    if origin and _hostname(origin) not in _LOOPBACK:
        return False          # some web page the operator has open
    return True


def needs_operator(request: Request) -> bool:
    if request.method in _SAFE:
        return False
    path = request.url.path
    if _OPEN.match(path):
        return False
    if path.rstrip("/") == "/portfolio/sync" and \
            request.query_params.get("dry_run", "").lower() in ("1", "true", "yes"):
        return False
    return True


async def middleware(request: Request, call_next):
    if needs_operator(request) and not is_operator(request):
        return JSONResponse(status_code=403, content={
            "detail": "this changes the copy book (real TAO) and only runs for the "
                      "operator: call it from this machine on localhost, or send "
                      "Authorization: Bearer <token> — paste it once on /bt/copy "
                      "(on the server: cat ~/.mod/bt/write_token)"})
    return await call_next(request)
