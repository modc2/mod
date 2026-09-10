"""
Local REST surface for the monero module, used by the web app.

Two surfaces exist and they are deliberately different:

  * the mod-protocol server (port 50690) is the fleet's front door and is
    gated by the shared gate -- every function there needs owner auth;
  * this server is bound to localhost and backs the app. Reads are open, but
    anything that can move funds, reveal a key, or scan with one needs a
    bearer token read from ~/.mod/monero/server.secret (generated on first
    start).

Scanning is guarded even though it only reads the chain: it needs the wallet
password, and what it returns is precisely the information a Monero view key
is supposed to keep private.

The same functions are also served to agents as MCP tools on /mcp, mounted from
mcp.py so there is one definition of each tool and not two that can disagree.

Print the token with:  m monero/token   or   cat ~/.mod/monero/server.secret
"""

import os
import secrets
import sys
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_root = os.path.dirname(os.path.abspath(__file__))
for p in (_root, os.path.join(_root, 'monero')):
    if p not in sys.path:
        sys.path.insert(0, p)

import mcp as mcp_server  # noqa: E402  — this module's mcp.py; _root leads sys.path

app = FastAPI(title="Monero", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get(
        "MONERO_CORS_ORIGINS",
        "http://localhost:50691,http://127.0.0.1:50691").split(",") if o],
    allow_methods=["*"], allow_headers=["*"],
)

# Functions that only read public chain data, do maths on public keys, or
# report local metadata.
OPEN_FNS = {
    'info', 'block', 'tx', 'mempool', 'price', 'supply', 'network', 'search',
    'fee', 'ring', 'validate', 'subaddress', 'integrated',
    'wallet_list', 'wallet_info', 'wallet_integrated',
    'rpc_status', 'capabilities', 'status', 'test',
    'bridge_routes', 'bridge_assets', 'bridge_quote', 'bridge_status',
    # Descriptions of the MCP surface. The tools themselves are reached at
    # /mcp, where each one carries its own auth.
    'mcp', 'mcp_tools',
}

# Functions that spend, reserve, reveal a secret, or use one.
GUARDED_FNS = {
    'seed_new', 'keys_from_seed',
    'wallet_create', 'wallet_restore', 'wallet_watch', 'wallet_new_address',
    'wallet_label', 'wallet_reveal', 'wallet_delete', 'wallet_restore_height',
    'wallet_scan',
    'balance', 'transfers', 'send', 'send_confirm', 'sweep', 'broadcast_raw',
    'rpc_open', 'rpc_load_wallet', 'key_images',
    'bridge_start',
    # mcp_call runs a tool with this box's own identity, which is every tool.
    # Open here it would be a way around every line above it.
    'mcp_call',
}

_mod = None


def get_mod():
    global _mod
    if _mod is None:
        from mod import Mod          # monero/mod.py, via sys.path above
        _mod = Mod()
    return _mod


def secret_path() -> Path:
    base = Path(os.environ.get("MONERO_STATE_DIR") or Path.home() / ".mod" / "monero")
    base.mkdir(parents=True, exist_ok=True)
    return base / "server.secret"


def server_token() -> str:
    p = secret_path()
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p.read_text().strip()


def check_auth(fn: str, authorization: str = None):
    if fn not in GUARDED_FNS:
        return
    expected = server_token()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail=f"{fn} can move funds or use a private key and needs the "
                   f"module token. Read it from {secret_path()} and send it as "
                   f"'Authorization: Bearer <token>'.")


@app.get("/health")
def health():
    return {"status": "ok", "module": "monero"}


@app.get("/fns")
def fns():
    return {"open": sorted(OPEN_FNS), "guarded": sorted(GUARDED_FNS),
            "mcp": {"endpoint": "POST /mcp", "schema": "GET /mcp",
                    "stdio": "python3 mcp.py",
                    "tools": len(mcp_server.TOOLS)}}


# ── MCP ────────────────────────────────────────────────────────────────────

def _mcp_url(request: Request) -> str:
    """The url an MCP client should use, as the caller actually reached us.

    Three deployments, one answer. On this server's own port the path is
    already right. Behind the Next app the browser asked for
    `/monero/api/mcp`, which the route handler forwarded here as `/mcp`.
    Behind the fleet gateway, caddy has *stripped* `/api/monero` before we ever
    saw the request -- so the path we hold is right for us and wrong for the
    client, and the prefix has to be put back. A client handed `https://host/mcp`
    would 404 forever.
    """
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or request.url.netloc)
    path = str(request.url.path)
    prefix = request.headers.get("x-forwarded-prefix") or ""
    if not prefix and f":{os.environ.get('MONERO_REST_PORT', 8940)}" not in host \
            and ":8940" not in host:
        # Reached over a name rather than our own port: that is the gateway,
        # whose convention for every module's API is /api/{name}.
        prefix = "/api/monero"
    root = path.split("/mcp", 1)[0]
    return f"{scheme}://{host}{prefix}{root}/mcp"


@app.get("/mcp")
def mcp_schema(request: Request):
    """The whole MCP server as a document — no client needed to read it."""
    return mcp_server.describe(_mcp_url(request))


@app.get("/mcp/tools")
def mcp_tools():
    doc = mcp_server.describe()
    return {"count": doc["count"], "tools": doc["tools"]}


@app.get("/mcp/config")
def mcp_config(request: Request):
    return mcp_server.client_config(_mcp_url(request))


@app.post("/mcp")
async def mcp_call(request: Request,
                   authorization: Optional[str] = Header(default=None)):
    """Streamable HTTP transport. The caller's token rides into the tools.

    `local=False`: an HTTP caller is not the box that owns the wallet files, so
    a tool that spends or reads a key refuses unless it carries the module
    token -- the same one that guards the REST functions above.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=mcp_server._error(
            None, -32700, "parse error: body is not valid JSON"))
    ctx = mcp_server.Ctx(token=authorization, local=False)
    if isinstance(body, list):                      # a JSON-RPC batch
        out = [r for r in (mcp_server.handle(item, ctx) for item in body)
               if r is not None]
        return JSONResponse(content=out or None, status_code=200 if out else 202)
    response = mcp_server.handle(body, ctx)
    if response is None:
        return JSONResponse(content=None, status_code=202)
    return JSONResponse(content=response)


@app.post("/{fn}")
def dispatch(fn: str, body: dict = Body(default={}),
             authorization: str = Header(default=None)):
    """Call a module function by name with a JSON body of its arguments."""
    if fn not in OPEN_FNS and fn not in GUARDED_FNS:
        raise HTTPException(status_code=404, detail=f"unknown function: {fn}")
    check_auth(fn, authorization)

    mod = get_mod()
    fn_obj = getattr(mod, fn, None)
    if fn_obj is None or not callable(fn_obj):
        raise HTTPException(status_code=404, detail=f"unknown function: {fn}")
    try:
        result = fn_obj(**(body or {}))
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"{fn}: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{fn}: {e}")

    if isinstance(result, dict) and result.get('error'):
        # Module errors are user-level (bad address, no wallet rpc), not faults.
        # Checked for truthiness, not presence: several functions carry an
        # `error: null` field to say "nothing went wrong", and treating that as
        # a failure turns a good response into an opaque 400.
        raise HTTPException(status_code=400, detail=result['error'])
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    token = server_token()
    # MONERO_REST_PORT before PORT: this process gets restarted by supervisors
    # whose own PORT is the web app's, and binding that would fight the app.
    port = int(os.environ.get("MONERO_REST_PORT") or os.environ.get("PORT") or 8940)
    print(f"monero api on :{port}  ({len(mcp_server.TOOLS)} mcp tools on /mcp, "
          f"guarded-fn token: {token[:8]}… in {secret_path()})")
    uvicorn.run(app, host=os.environ.get("MONERO_API_HOST", "127.0.0.1"), port=port)
